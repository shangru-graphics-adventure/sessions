# -*- coding: utf-8 -*-
"""对一个对话能做的动作: 切窗口 / 找它的三份材料 / 生成标题 / 让它自己写 recap。

一个对话有三个入口, 这里负责把它们都找出来:
  1. **resume**   —— 用 session id 把对话接着往下开(server.py 的 do_resume)
  2. **原始记录** —— jsonl 逐字全文, 外加 Stop hook 导出的 markdown 版
  3. **recap**    —— /session-recap 写的人话总结(通过 SESSIONS.md 台账反查)
"""
import io
import os
import re
import glob
import json
import time
import ctypes
import subprocess

import config

HOME = os.path.expanduser("~")
PROJ = config.PROJECTS_DIR
RECAP_DIR = config.RECAP_DIR
ARCHIVE_DIRS = config.ARCHIVE_DIRS


# ---------------------------------------------------------------- 窗口

def _u32():
    return ctypes.windll.user32


def window_alive(hwnd, term_pid=None):
    """这个 HWND 还在吗? 还属于原来那个终端进程吗?"""
    if not hwnd:
        return False
    try:
        import ctypes.wintypes as wt
        u32 = _u32()
        if not u32.IsWindow(hwnd):
            return False
        if term_pid:
            p = wt.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value != term_pid:
                return False        # HWND 被回收给了别的进程
        return True
    except Exception:
        return False


def window_title(hwnd):
    try:
        u32 = _u32()
        n = u32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value
    except Exception:
        return ""


def focus_window(hwnd):
    """把窗口切到前台。

    直接 SetForegroundWindow 经常只让任务栏闪一下 —— Windows 不允许后台进程随便抢
    前台。标准变通: 把自己的输入线程临时挂到当前前台窗口的线程上, 借它的权限。
    """
    if not hwnd:
        return {"ok": False, "why": "没有记录到窗口"}
    u32 = _u32()
    SW_RESTORE = 9
    try:
        if not u32.IsWindow(hwnd):
            return {"ok": False, "why": "窗口已经不存在了"}
        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, SW_RESTORE)
        u32.SetForegroundWindow(hwnd)
        if u32.GetForegroundWindow() == hwnd:
            return {"ok": True, "how": "direct", "title": window_title(hwnd)}

        k32 = ctypes.windll.kernel32
        cur = u32.GetForegroundWindow()
        t1 = u32.GetWindowThreadProcessId(cur, None)
        t2 = k32.GetCurrentThreadId()
        u32.AttachThreadInput(t2, t1, True)
        u32.ShowWindow(hwnd, SW_RESTORE)
        u32.SetForegroundWindow(hwnd)
        u32.BringWindowToTop(hwnd)
        u32.AttachThreadInput(t2, t1, False)
        ok = u32.GetForegroundWindow() == hwnd
        return {"ok": bool(ok), "how": "attach", "title": window_title(hwnd),
                "why": "" if ok else "系统拒绝了前台切换, 请自己点任务栏"}
    except Exception as e:
        return {"ok": False, "why": str(e)}


# ---------------------------------------------------------------- 键盘注入

VK_RETURN = 0x0D
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002


class _KEYBD(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MOUSE(ctypes.Structure):
    """union 里最大的那个成员。必须照实声明 —— INPUT 的大小由它决定, 给小了
    SendInput 会直接返回 0 什么都不做(x64 下 sizeof(INPUT) 应为 40, 不是 32)。"""
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSE), ("ki", _KEYBD)]
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def _send(inputs):
    """返回实际被系统接受的事件数 —— 调用方必须核对, 它失败时是静默的。"""
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    sent = _u32().SendInput(n, arr, ctypes.sizeof(_INPUT))
    if sent != n:
        err = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        raise OSError("SendInput 只发出 %d/%d 个事件 (GetLastError=%s, "
                      "sizeof(INPUT)=%d)" % (sent, n, err, ctypes.sizeof(_INPUT)))
    return sent


def _char_input(ch, up=False):
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return _INPUT(type=1, ki=_KEYBD(wVk=0, wScan=ord(ch), dwFlags=flags,
                                    time=0, dwExtraInfo=None))


def _vk_input(vk, up=False):
    return _INPUT(type=1, ki=_KEYBD(wVk=vk, wScan=0,
                                    dwFlags=(KEYEVENTF_KEYUP if up else 0),
                                    time=0, dwExtraInfo=None))


def type_into_window(hwnd, text, press_enter=True, settle=0.35):
    """切到那个窗口, 把 text 敲进去。

    这是**往一个活着的对话里注入按键**, 是本工具里唯一有副作用的操作, 所以:
      · 先切前台并确认真的切过去了, 切不过去就直接放弃 —— 绝不对着别的窗口乱敲;
      · 敲完停一下再回车, 免得 TUI 还没处理完输入就被提交。
    """
    r = focus_window(hwnd)
    if not r.get("ok"):
        return {"ok": False, "why": r.get("why") or "切不到那个窗口, 已放弃输入"}

    # 刚切过去的一瞬间焦点还可能被别的窗口抢回(自动前台激活、conhost 刚启动等),
    # 所以等它稳定下来再敲: 连续两次采样都是目标窗口才算数, 最多等 1.5 秒。
    u32 = _u32()
    stable = 0
    deadline = time.time() + 1.5
    while time.time() < deadline:
        time.sleep(settle / 2.0)
        if u32.GetForegroundWindow() == hwnd:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
            u32.SetForegroundWindow(hwnd)
    if stable < 2:
        return {"ok": False, "why": "焦点停不在那个窗口上(被别的窗口抢走), 已放弃输入"}

    seq = []
    for ch in text:
        seq.append(_char_input(ch))
        seq.append(_char_input(ch, up=True))
    try:
        sent = _send(seq)
        if press_enter:
            time.sleep(settle)
            _send([_vk_input(VK_RETURN), _vk_input(VK_RETURN, up=True)])
    except OSError as e:
        return {"ok": False, "why": str(e)}
    return {"ok": True, "chars": sent, "title": r.get("title", "")}


# ---------------------------------------------------------------- 三份材料

def transcript_path(sid):
    for d in os.listdir(PROJ):
        fp = os.path.join(PROJ, d, sid + ".jsonl")
        if os.path.exists(fp):
            return fp
    return None


def archive_paths(sid):
    """Stop/SessionEnd hook 导出的 markdown 版逐字记录(可能有多份, 按日期)。"""
    out = []
    for root in ARCHIVE_DIRS:
        if not os.path.isdir(root):
            continue
        out += glob.glob(os.path.join(root, "*", "*", sid + ".md"))
    # 项目内的 _docs/chat/<日期>/
    for root in config.PROJECT_ROOTS:
        out += glob.glob(os.path.join(root, "*", "_docs", "chat", "*", sid + ".md"))
    return sorted(set(out))


_SID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def recap_path(sid):
    """从 session_recaps 里反查这个对话的 recap。

    两条路: 先查 SESSIONS.md 台账(/session-recap 会把 sid 登记进去), 查不到就
    在 recap 正文里搜 sid —— recap 文件头部会写 `> session: <id>` 那行元数据。
    """
    if not os.path.isdir(RECAP_DIR):
        return None
    idx = os.path.join(RECAP_DIR, "SESSIONS.md")
    if os.path.exists(idx):
        try:
            for line in io.open(idx, encoding="utf-8"):
                if sid in line:
                    m = re.search(r"\(([^)]+\.md)\)", line)
                    if m:
                        p = os.path.join(RECAP_DIR, os.path.basename(m.group(1)))
                        if os.path.exists(p):
                            return p
        except Exception:
            pass
    for p in glob.glob(os.path.join(RECAP_DIR, "*.md")):
        if os.path.basename(p) in ("INDEX.md", "SESSIONS.md"):
            continue
        try:
            with io.open(p, encoding="utf-8", errors="replace") as fh:
                if sid in fh.read(4000):        # 元数据在文件头
                    return p
        except Exception:
            pass
    return None


def materials(sid):
    t = transcript_path(sid)
    a = archive_paths(sid)
    r = recap_path(sid)
    return {
        "transcript": t,
        "transcript_kb": round(os.path.getsize(t) / 1024.0, 1) if t else None,
        "archive_md": a[-1] if a else None,
        "archive_count": len(a),
        "recap": r,
        "recap_mtime": os.path.getmtime(r) if r else None,
    }


# ---------------------------------------------------------------- haiku 标题

TITLE_PROMPT = (
    "下面是一个对话里我(用户)先后提出的所有话题。请用一句不超过 22 字的中文,"
    "概括这个对话整体在做什么。直接输出标题本身, 不要引号、不要解释、不要句号。\n\n"
)


def haiku_title(topics, timeout=90):
    """用 claude -p 走你已登录的订阅生成标题(不需要 ANTHROPIC_API_KEY)。

    实测单次约 9-10 秒, 绝大部分是 CLI 启动开销 —— 所以批量要并发, 别串行。
    """
    if not topics:
        return {"ok": False, "why": "这个对话没有可用的话题"}
    body = TITLE_PROMPT + "\n".join("%d. %s" % (i + 1, t)
                                    for i, t in enumerate(topics[:20]))
    try:
        p = subprocess.run(
            ["claude", "-p", "--model", "claude-haiku-4-5"],
            input=body.encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, shell=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "why": "claude -p 超时(%ds)" % timeout}
    except Exception as e:
        return {"ok": False, "why": str(e)}
    out = (p.stdout or b"").decode("utf-8", "replace").strip()
    out = " ".join(out.split())
    if not out:
        err = (p.stderr or b"").decode("utf-8", "replace").strip()[:200]
        return {"ok": False, "why": err or "claude -p 没有输出"}
    return {"ok": True, "title": out[:60]}
