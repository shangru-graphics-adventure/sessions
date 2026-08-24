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


def trust_folder(cwd, path=None):
    """把这个目录标记成"已信任", 免得 resume 出来第一屏是 trust 对话框。

    实测(2026-08-24, 本机 ~/.claude.json)存的就是这个:

        {"projects": {"C:/Users/me/work": {"hasTrustDialogAccepted": true, ...}}}

    注意两件事, 都是实测出来的, 不是猜的:
      · key 用**正斜杠**, 且没有结尾斜杠 —— 写成反斜杠的话 Claude Code 认不出来,
        对话框照样弹;
      · 这个文件是 Claude Code 自己在写的, 所以**只改这一个布尔字段, 原样回写其余
        全部内容**, 并且原子替换(先写 .tmp 再 os.replace)。

    只在你按下 Resume 的那一刻、针对**那一个目录**做。返回值是给调用方看的说明,
    做不到就返回 False, 绝不因此让 resume 失败 —— 大不了自己点一下那个对话框。
    """
    path = path or os.path.join(HOME, ".claude.json")
    if not cwd or not os.path.exists(path):
        return False
    key = os.path.abspath(cwd).replace("\\", "/").rstrip("/")
    try:
        with io.open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        projects = cfg.setdefault("projects", {})
        entry = projects.get(key)
        if entry is None:
            # 没见过这个目录: 建一条最小记录, 其余字段留给 Claude Code 自己补
            entry = projects[key] = {}
        if entry.get("hasTrustDialogAccepted") is True:
            return True                     # 本来就信任, 不写盘
        entry["hasTrustDialogAccepted"] = True
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def close_window(hwnd):
    """礼貌地请一个窗口自己关掉(WM_CLOSE), 不强杀。

    只用来收掉**已经空掉的**终端窗口 —— 真正结束对话走 close_claude()。
    PostMessage 是异步的: 发出去就返回, 窗口自己决定关不关(有未保存内容时它可以弹框)。
    """
    if not hwnd:
        return False
    try:
        WM_CLOSE = 0x0010
        return bool(_u32().PostMessageW(hwnd, WM_CLOSE, 0, 0))
    except Exception:
        return False


def close_claude(pid, ctime=None, hwnd=None, close_terminal=False, timeout=5.0):
    """结束一个对话进程(以及它派生的子进程)。

    **动手前先验明正身**, 这是硬约束不是可选项: 进程名必须是 claude.exe, 创建时间
    必须和记账时对得上(±2s)。pid 会被系统回收再分配 —— 少了这一步, 一个早就退出的
    对话的旧 pid 可能已经属于别人的进程, 关"窗口"就变成了随机杀进程。

    连子进程一起收: claude 派生出来的 shell / 工具进程在父进程没了之后会变成孤儿,
    继续占着端口和文件。这与直接关掉终端窗口的效果一致(那时 conhost 也是杀整棵树)。
    **只杀这一棵 pid 树** —— 绝不按窗口标题去 taskkill, 那个过滤器在 Win10+ 上会
    静默失效并杀光同名进程(README 里记着这笔学费)。

    close_terminal=True 时, 进程收干净后再给终端窗口发一个 WM_CLOSE。调用方必须
    先确认这个终端窗口里没有别的对话(Windows Terminal 是单窗口多标签, 关窗 =
    关掉里面所有标签页)。
    """
    if not pid:
        return {"ok": False, "why": "没有记录到进程"}
    try:
        import psutil
    except Exception:
        return {"ok": False, "why": "需要 psutil 才能安全地结束进程(pip install psutil)"}

    try:
        p = psutil.Process(int(pid))
        name = (p.name() or "").lower()
        if name not in config.CLAUDE_PROCS:
            return {"ok": False, "why": "pid %s 现在是 %s, 不在 %s 里 — 不动它"
                                        % (pid, name, "/".join(config.CLAUDE_PROCS))}
        if ctime is not None and abs(p.create_time() - float(ctime)) >= 2.0:
            return {"ok": False, "why": "pid %s 的创建时间对不上(它已经被系统回收给别的进程了)" % pid}
        kids = p.children(recursive=True)
    except psutil.NoSuchProcess:
        return {"ok": True, "already": True, "why": "进程本来就已经不在了"}
    except Exception as e:
        return {"ok": False, "why": str(e)}

    for proc in kids + [p]:
        try:
            proc.terminate()
        except Exception:
            pass
    gone, alive = psutil.wait_procs(kids + [p], timeout=timeout)
    for proc in alive:                      # 赖着不走的再来一次硬的
        try:
            proc.kill()
        except Exception:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=2.0)

    out = {"ok": True, "killed": len(gone) + len(alive), "children": len(kids)}
    if close_terminal and hwnd:
        out["terminal_closed"] = close_window(hwnd)
    return out


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
