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


def _bridge_one(port, path, body=None, timeout=1.2):
    import urllib.request
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None                     # 没装扩展是常态, 不是错误


def bridge(path, body=None, timeout=1.2):
    """跟 VS Code 桥说话(vscode-bridge/ 那个扩展)。没装 / 没开就返回 None。

    **每个 VS Code 窗口跑一份桥**, 各占端口段里的一个, 所以这里挨个端口问过去,
    直到某一个说 ok —— 问到别的窗口只会得到 `ok:false`("这个窗口里没有这个终端"),
    没有任何副作用, 所以顺序试是安全的。

    超时给得很短且总额有上限: 这是个"有更好就用, 没有就算了"的增强, 绝不能因为它
    让页面卡住。
    """
    first = None
    for i in range(max(1, config.VSCODE_BRIDGE_SPAN)):
        r = _bridge_one(config.VSCODE_BRIDGE_PORT + i, path, body, timeout)
        if r is None:
            continue                    # 这个端口没人监听
        if r.get("ok"):
            return r
        first = first or r              # 记下第一个"在, 但不是它"的回答
    return first


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


def window_for_pid(pid, hops=6):
    """从任意一个进程出发, 沿父链找到第一个"有唯一可见窗口"的祖先, 返回它的 HWND。

    比认死一个 term_pid 更耐用: VS Code 里承载对话的是**没有窗口**的渲染进程, 而且
    重载窗口之后那个 pid 还会变; 从活着的 claude 进程往上走则一定能走到 IDE 主窗口。
    "唯一"这个条件是故意的 —— 一个进程有好几个可见窗口时认不准是哪个, 宁可返回 0。
    """
    if not pid or os.name != "nt":
        return 0
    try:
        import ctypes.wintypes as wt
        import psutil
        u32 = _u32()
        Enum = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

        def windows_of(target):
            got = []

            def cb(h, _):
                p = wt.DWORD()
                u32.GetWindowThreadProcessId(h, ctypes.byref(p))
                if p.value == target and u32.IsWindowVisible(h):
                    got.append(h)
                return True

            u32.EnumWindows(Enum(cb), 0)
            return got

        p = psutil.Process(pid)
        for _ in range(hops):
            ws = windows_of(p.pid)
            if len(ws) == 1:
                return int(ws[0])
            if ws:
                return 0                 # 好几个, 认不准
            p = p.parent()
            if p is None:
                break
    except Exception:
        pass
    return 0


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


# 一个终端标签页里的 shell。杀掉它, VS Code 会把那个标签页收掉(实测 2026-08-24:
# 标签页确实消失了)。只认这几个名字 —— 父进程不是已知 shell 就绝不动它。
# 子进程绝不弹黑框。claude.exe 是控制台程序: 当 server 本身**没有控制台**时
# (用 pythonw 起的常态), 它会自己新建一个控制台窗口, 于是每总结一个标题就闪一个黑框。
# 用 python.exe 起时子进程继承控制台、看不出问题 —— 这个坑只在换成 pythonw 之后才现形。
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SHELLS = ("powershell.exe", "pwsh.exe", "cmd.exe", "bash.exe", "zsh.exe", "sh.exe",
          "git-bash.exe", "nu.exe", "fish.exe")


def close_claude(pid, ctime=None, hwnd=None, close_terminal=False,
                 term_name=None, kill_shell=False, timeout=5.0):
    """结束一个对话进程(以及它派生的子进程)。

    **动手前先验明正身**, 这是硬约束不是可选项: 进程名必须是 claude.exe, 创建时间
    必须和记账时对得上(±2s)。pid 会被系统回收再分配 —— 少了这一步, 一个早就退出的
    对话的旧 pid 可能已经属于别人的进程, 关"窗口"就变成了随机杀进程。

    连子进程一起收: claude 派生出来的 shell / 工具进程在父进程没了之后会变成孤儿,
    继续占着端口和文件。这与直接关掉终端窗口的效果一致(那时 conhost 也是杀整棵树)。
    **只杀这一棵 pid 树** —— 绝不按窗口标题去 taskkill, 那个过滤器在 Win10+ 上会
    静默失效并杀光同名进程(README 里记着这笔学费)。

    kill_shell=True 时**连这个对话所在的 shell 一起收掉**。用途只有一个: VS Code 的
    集成终端没有窗口句柄可关(标签页共用 IDE 主窗口), 但杀掉那个标签自己的 shell,
    VS Code 就会把标签页收走 —— 实测确认标签页会消失。一个标签一个 shell, 所以
    语义是干净的。前提照旧要验: 父进程必须是**已知的 shell 名**(SHELLS), 不是就
    只杀 claude 并在返回里说明, 绝不对着一个认不出来的父进程开枪。

    close_terminal=True 时, 进程收干净后再给终端窗口发一个 WM_CLOSE。两道闸都要过:
      1. 调用方先确认这个终端窗口里没有别的对话(Windows Terminal 是单窗口多标签,
         关窗 = 关掉里面所有标签页);
      2. 这里再确认宿主是**纯终端**(config.CLOSABLE_TERMS)。VS Code 集成终端的宿主是
         Code.exe, 那个窗口里还装着你的编辑器 —— 关掉对话可以, 关掉编辑器不行。
         认不出来的宿主一律当作不能关。
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
        shell = None
        shell_why = ""
        if kill_shell:
            par = p.parent()
            pname = (par.name() or "").lower() if par else ""
            if par and pname in SHELLS:
                shell = par
            else:
                shell_why = ("父进程是 %s, 不在已知 shell 名单里 —— 只关对话, 不动它"
                             % (pname or "?"))
    except psutil.NoSuchProcess:
        return {"ok": True, "already": True, "why": "进程本来就已经不在了"}
    except Exception as e:
        return {"ok": False, "why": str(e)}

    targets = kids + [p] + ([shell] if shell else [])
    for proc in targets:
        try:
            proc.terminate()
        except Exception:
            pass
    gone, alive = psutil.wait_procs(targets, timeout=timeout)
    for proc in alive:                      # 赖着不走的再来一次硬的
        try:
            proc.kill()
        except Exception:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=2.0)

    out = {"ok": True, "killed": len(gone) + len(alive), "children": len(kids)}
    if kill_shell:
        out["shell_killed"] = bool(shell)
        if shell_why:
            out["shell_why"] = shell_why
    if close_terminal and hwnd:
        host = (term_name or "").lower()
        if host and host not in config.CLOSABLE_TERMS:
            out["terminal_closed"] = False
            out["term_kept"] = ("宿主是 %s, 不是纯终端 —— 只结束了对话, 窗口留着"
                                % term_name)
        else:
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
            timeout=timeout, shell=False, creationflags=NO_WINDOW)
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
