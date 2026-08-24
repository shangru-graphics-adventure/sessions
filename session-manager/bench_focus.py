# -*- coding: utf-8 -*-
"""探明"切换到打开着这个对话的那个窗口"能做到什么程度。

要回答三件事:
  1. 从 claude.exe 的 pid, 能不能找到它所在的终端窗口(HWND)?
  2. 后台进程(server)调 SetForegroundWindow 能不能真的把窗口切到前台?
     (Windows 有前台锁定, 后台进程调用常常只让任务栏闪烁)
  3. Windows Terminal 是多标签单进程 —— 能不能精确定位到"那一个标签页"?
     claude.exe 的环境变量里有没有 WT_SESSION 之类可用的线索?

跑法:  python bench_focus.py <claude_pid>
不带参数时自动挑一个当前活着的 claude.exe。
"""
import os
import sys
import time
import ctypes
import ctypes.wintypes as wt

import psutil

import utf8_console
utf8_console.enable()

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32


def claude_procs():
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        if (p.info.get("name") or "").lower() == "claude.exe":
            out.append(p.info["pid"])
    return out


def ancestors(pid):
    chain = []
    try:
        p = psutil.Process(pid)
        for _ in range(12):
            p = p.parent()
            if p is None:
                break
            chain.append((p.pid, p.name()))
    except Exception:
        pass
    return chain


def windows_of_pid(pid):
    """枚举某个进程的所有可见顶层窗口。"""
    res = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        p = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid:
            return True
        if not u32.IsWindowVisible(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        res.append((hwnd, buf.value))
        return True

    u32.EnumWindows(EnumWindowsProc(cb), 0)
    return res


def env_of(pid):
    try:
        return psutil.Process(pid).environ()
    except Exception as e:
        return {"__error__": str(e)}


pid = int(sys.argv[1]) if len(sys.argv) > 1 else None
if pid is None:
    cands = claude_procs()
    print("当前活着的 claude.exe: %d 个 -> %s" % (len(cands), cands[:10]))
    # 挑一个能上溯到终端的
    for c in cands:
        if any(n in ("WindowsTerminal.exe", "conhost.exe", "cmd.exe")
               for _, n in ancestors(c)):
            pid = c
            break
    if pid is None:
        print("没找到挂在终端下的 claude.exe")
        sys.exit(1)

print("\n目标 claude.exe pid = %s" % pid)
print("祖先链:")
term_pid, term_name = None, None
for p, n in ancestors(pid):
    print("   %-8s %s" % (p, n))
    if term_name is None and n in ("WindowsTerminal.exe", "conhost.exe", "cmd.exe"):
        term_pid, term_name = p, n

print("\n1) 终端进程: %s (pid=%s)" % (term_name, term_pid))
if not term_pid:
    print("   找不到终端进程, 后面免谈")
    sys.exit(1)

wins = windows_of_pid(term_pid)
print("\n2) 这个终端进程名下的可见顶层窗口: %d 个" % len(wins))
for h, t in wins[:10]:
    print("   hwnd=%-10s 标题=%r" % (h, t))
if term_name == "WindowsTerminal.exe" and len(wins) >= 1:
    print("   注意: WT 是多窗口单进程 —— 这里列出的是该进程的**所有**窗口,")
    print("        无法从 pid 直接判断目标对话在哪一个窗口的哪一个标签。")

print("\n3) claude.exe 的环境变量里有没有终端线索:")
env = env_of(pid)
if "__error__" in env:
    print("   读不到环境变量: %s" % env["__error__"])
else:
    hit = {k: v for k, v in env.items()
           if k.upper().startswith(("WT_", "TERM", "SESSIONNAME", "CONEMU"))}
    if hit:
        for k, v in sorted(hit.items()):
            print("   %s = %s" % (k, v[:70]))
    else:
        print("   没有 WT_/TERM 类变量")

print("\n4) 试着把窗口切到前台(观察它是真的切过来, 还是只在任务栏闪):")
if wins:
    hwnd = wins[0][0]
    SW_RESTORE = 9
    fg_before = u32.GetForegroundWindow()
    u32.ShowWindow(hwnd, SW_RESTORE)
    ok_plain = bool(u32.SetForegroundWindow(hwnd))
    time.sleep(0.4)
    fg_after = u32.GetForegroundWindow()
    print("   SetForegroundWindow 返回=%s" % ok_plain)
    print("   前台窗口: %s -> %s   %s" % (
        fg_before, fg_after,
        "成功切换" if fg_after == hwnd else "没切过去(被前台锁定挡住)"))

    if fg_after != hwnd:
        # 绕过前台锁定的标准做法: 把自己的输入线程挂到当前前台窗口的线程上
        cur = u32.GetForegroundWindow()
        t1 = u32.GetWindowThreadProcessId(cur, None)
        t2 = k32.GetCurrentThreadId()
        u32.AttachThreadInput(t2, t1, True)
        u32.ShowWindow(hwnd, SW_RESTORE)
        u32.SetForegroundWindow(hwnd)
        u32.BringWindowToTop(hwnd)
        u32.AttachThreadInput(t2, t1, False)
        time.sleep(0.4)
        print("   AttachThreadInput 变通后: %s" % (
            "成功切换" if u32.GetForegroundWindow() == hwnd else "仍然没切过去"))
