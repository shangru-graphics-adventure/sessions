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
import sys

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


def is_real_window(hwnd):
    """这个句柄是不是一个真的、用户看得见的窗口。

    ConPTY 会给每个标签造一个类名 `PseudoConsoleWindow` 的 0x0 伪窗口, 而
    `IsWindowVisible` 对它返回 True —— 拿它当窗口用, "切过去"会落到宿主
    (Windows Terminal)身上, 也就是**随便哪个当前活动的标签**(实测踩到:
    点 A 对话的切过去, 前台变成了完全无关的 B 对话)。类名 + 非零矩形双重验。
    """
    if not hwnd:
        return False
    try:
        import ctypes.wintypes as wt
        u32 = _u32()
        if not u32.IsWindow(hwnd) or not u32.IsWindowVisible(hwnd):
            return False
        cls = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "PseudoConsoleWindow":
            return False
        r = wt.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))
        return (r.right - r.left) > 0 and (r.bottom - r.top) > 0
    except Exception:
        return False


def window_owner(hwnd):
    """这个窗口属于哪个进程(pid, 进程名)。认不出返回 (None, "")。"""
    try:
        import ctypes.wintypes as wt
        import psutil
        p = wt.DWORD()
        _u32().GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        return p.value, psutil.Process(p.value).name()
    except Exception:
        return None, ""


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
                if p.value == target and is_real_window(h):
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


def reset_console(pid, timeout=3.0):
    """把一个控制台从"claude 被强杀后的残留状态"里救出来。

    claude(TUI)开着鼠标上报/括号粘贴/备用屏; 它**正常退出会自己关**, 被我们
    terminate 就来不及了 —— 于是那个标签回到 shell 提示符后, 鼠标一动就满屏
    `[555;170;45M` 这种上报序列(实测截图)。杀完人要打扫: AttachConsole 到那个
    shell, 往 CONOUT$ 写一串 VT 关闭序列(关鼠标上报/括号粘贴, 显示光标, 退备用屏)。
    写到输出端就够了 —— ConPTY 会把序列转发给 WT/VS Code 前端, 状态存在前端那侧。
    """
    seq = "".join(chr(27) + x for x in (
        "[?1000l", "[?1002l", "[?1003l", "[?1006l",   # 各级鼠标上报
        "[?2004l",                                    # 括号粘贴
        "[?25h",                                      # 光标显示回来
        "[?1049l",                                    # 退出备用屏
        "[0m",                                        # 属性复位
    ))
    code = "; ".join([
        "import ctypes, sys",
        "k = ctypes.windll.kernel32",
        "k.FreeConsole()",
        "sys.exit(1) if not k.AttachConsole(%d) else None" % pid,
        "h = k.CreateFileW('CONOUT$', 0xC0000000, 3, None, 3, 0, None)",
        "m = ctypes.c_uint()",
        "k.GetConsoleMode(h, ctypes.byref(m))",
        "k.SetConsoleMode(h, m.value | 4)",           # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        "n = ctypes.c_uint()",
        "k.WriteConsoleW(h, %r, %d, ctypes.byref(n), None)" % (seq, len(seq)),
    ])
    try:
        r = subprocess.run([sys.executable, "-c", code],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


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
        parent = p.parent()               # 杀之前取 —— 杀完就查不到了
        shell = None
        shell_why = ""
        if kill_shell:
            pname = (parent.name() or "").lower() if parent else ""
            if parent and pname in SHELLS:
                shell = parent
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
    # 留下来的 shell 要打扫(见 reset_console); 连 shell 一起杀了就没这回事
    if parent is not None and not kill_shell:
        try:
            if parent.is_running():
                out["console_reset"] = reset_console(parent.pid)
        except Exception:
            pass
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


VK_CONTROL = 0x11
VK_TAB = 0x09


def _title_core(t):
    """标签标题去掉状态符号后的核心文本。

    Claude Code 往终端标题里写"◐ 看盘工具重构"这种, 前面那个符号(✳/◐/◑/●)随
    对话状态变, 只有后面的文字是稳定的 —— 匹配时只认文字部分。
    """
    t = (t or "").strip()
    while t and not (t[0].isalnum() or ord(t[0]) > 0x2E80):   # 丢掉前导符号(保留 CJK)
        t = t[1:].strip()
    return t


def console_title_of(pid, timeout=3.0):
    """直接问一个控制台进程: 你的标题是什么。

    这就是 WT 标签标题的**权威来源** —— 不经过任何记账, 现在问现在答, 所以不受
    "hook 在错误时刻抓了别人标题"的污染(那个坑真踩过: 后台对话 Stop 时抓到的是
    你正看着的标签)。AttachConsole 是进程级状态, 不能在 server 自己身上做(多线程
    会打架), 起一个一次性子进程去问, ~100ms, focus 这种低频操作花得起。
    """
    code = "; ".join([
        "import ctypes, sys",
        "k = ctypes.windll.kernel32",
        "k.FreeConsole()",
        "sys.exit(1) if not k.AttachConsole(%d) else None" % pid,
        "b = ctypes.create_unicode_buffer(512)",
        "k.GetConsoleTitleW(b, 512)",
        "sys.stdout.buffer.write(b.value.encode('utf-8'))",
    ])
    try:
        r = subprocess.run([sys.executable, "-c", code],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, creationflags=NO_WINDOW)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", "replace").strip()
    except Exception:
        pass
    return ""


def wt_select_tab(hwnd, want, timeout=8.0):
    """UI Automation 直选 WT 标签: 瞬切(~240ms 实测), 不需要窗口在前台, 不发键盘。

    WT 的标签栏对 UIA 是可见的(每个标签一个 TabItem, 名字就是标题), 拿
    SelectionItemPattern.Select() 点它 —— 这比"提前台再轮 Ctrl+Tab"(一格 0.18s,
    上限 3 秒, 还要求焦点全程不被抢)干净一整个量级。脚本在 wt_tab.ps1。

    返回 (state, payload): ("selected", 标签名) / ("notfound", [全部标签名]) /
    ("error", 原因)。notfound 是**权威结论**(UIA 枚举了真实标签列表), 不该再退回
    键盘轮转; 只有 error(没有 powershell / UIA 被禁)才值得退。
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wt_tab.ps1")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", script, "-hwnd", str(hwnd), "-want", want],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, creationflags=NO_WINDOW)
        out = r.stdout.decode("utf-8", "replace").strip()
        lines = out.splitlines()
        allnames = []
        for ln in lines:
            if ln.startswith("ALL::"):
                allnames = [t for t in ln[5:].split("||") if t]
        if lines and lines[0].startswith("SELECTED::"):
            return "selected", {"name": lines[0][10:].strip(), "all": allnames}
        if lines and lines[0].startswith("NOTFOUND::"):
            return "notfound", [t for t in lines[0][10:].split("||") if t]
        return "error", (r.stderr.decode("utf-8", "replace").strip()[:200]
                         or "没有输出")
    except Exception as e:
        return "error", str(e)[:200]


def focus_wt_tab(hwnd, want_title, max_tabs=16):
    """把一个 Windows Terminal 窗口切到前台, 并轮到标题匹配的那个标签。

    WT 是单窗口多标签, 所有标签共用一个 HWND —— 光提前台, 活动的还是原来那个标签。
    但**窗口标题跟着活动标签走**, 而每个对话的标签标题(Claude Code 自己写的摘要)
    我们都记着。于是: 提前台 -> 标题不对就发一个 Ctrl+Tab -> 再看标题, 至多轮
    max_tabs 圈。发键盘前必须确认前台就是目标窗口(与 type_into_window 同一条铁律),
    焦点被抢走就立刻停手 —— 绝不对着别的窗口发按键。

    WT 没有任何 CLI/API 能按标题聚焦标签(wt focus-tab 只认 index, 而 index 与对话
    没有映射), 这条键盘路是唯一不猜索引的做法。
    """
    want = _title_core(want_title)
    r = focus_window(hwnd)
    if not r.get("ok"):
        return r
    if not want:
        return dict(r, tab=False, why="没记到这个对话的标签标题, 只能到窗口")
    u32 = _u32()

    def cur():
        return _title_core(window_title(hwnd))

    if want in cur() or cur() in want:
        return dict(r, tab=True, title=window_title(hwnd))

    state, payload = wt_select_tab(hwnd, want)
    if state == "selected":
        return dict(r, tab=True, how="uia", title=payload["name"],
                    all_tabs=payload["all"])
    if state == "notfound":
        return dict(r, tab=False, seen=payload,
                    why="这个窗口的 %d 个标签里没有标题含「%s」的(UIA 枚举过了), "
                        "它可能开在别的窗口" % (len(payload), want[:20]))
    # UIA 走不了(没有 powershell / 被策略禁了) —— 退回键盘轮转
    # 不能用"标题重复出现"当作转满一圈的判据 —— 窗口里可以有两个标签顶着同样的
    # 标题(同一对话开两份就会), 那样第二个同名标签会被误判成"回到起点"而提前放弃
    # (实测踩到: 两对重名标签的窗口里, 找一个明明存在的独名标签也会失败)。
    # 老老实实轮满 max_tabs 次, 一次 0.18s, 上限也就 3 秒。
    seen = []                             # 沿途见到的标签, 失败时报出来辅助诊断
    for _ in range(max_tabs):
        if u32.GetForegroundWindow() != hwnd:
            return dict(r, tab=False, seen=seen,
                        why="轮标签途中焦点被抢走, 已停手(切到了窗口, 标签请自己点)")
        _send([_vk_input(VK_CONTROL), _vk_input(VK_TAB),
               _vk_input(VK_TAB, up=True), _vk_input(VK_CONTROL, up=True)])
        time.sleep(0.18)                  # WT 切标签 + 刷新标题要一拍
        t = cur()
        if t and t not in seen:
            seen.append(t)
        if want in t or (t and t in want):
            return dict(r, tab=True, title=window_title(hwnd))
    return dict(r, tab=False, seen=seen,
                why="轮了 %d 次没遇到标题含「%s」的标签, 标签请自己点" % (max_tabs, want[:20]))


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
