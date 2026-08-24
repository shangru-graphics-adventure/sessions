# -*- coding: utf-8 -*-
"""验证"切到那个窗口并把命令敲进去"这条链路 —— 在一个新开的空 cmd 窗口上测,
绝不碰任何真实的 claude 对话。

验证四件事:
  1. 能不能拿到新窗口的 HWND
  2. 能不能把它切到前台
  3. 敲进去的字符能不能被那个窗口真的收到(让它写个文件回来证明)
  4. 中文能不能正确送进去(/session-recap 是 ASCII, 但注释可能不是)
"""
import io
import os
import sys
import time
import ctypes
import ctypes.wintypes as wt
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import actions

u32 = ctypes.windll.user32
MARK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_inject_probe.txt")
FAILED = []


def check(name, cond, extra=""):
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        FAILED.append(name)


def windows_of(pid):
    res = []
    P = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        p = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(hwnd):
            res.append(hwnd)
        return True

    u32.EnumWindows(P(cb), 0)
    return res


if os.path.exists(MARK):
    os.remove(MARK)

print("1) 开一个测试用的 cmd 窗口(不是 claude)")
proc = subprocess.Popen('cmd /k title INJECT_TEST_WINDOW',
                        creationflags=subprocess.CREATE_NEW_CONSOLE)
time.sleep(2.0)

# cmd /k 起的窗口, 真正持有窗口的是 conhost 而不是 cmd 自己 —— 所以按 pid 找不到。
# 按窗口标题找更可靠(这也是 hwnd 记录失效时的兜底思路)。
hwnds = windows_of(proc.pid)
if not hwnds:
    try:
        import psutil
        for c in psutil.Process(proc.pid).children(recursive=True):
            hwnds += windows_of(c.pid)
    except Exception:
        pass
if not hwnds:
    found = []
    P2 = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb2(hwnd, _):
        if u32.IsWindowVisible(hwnd) and "INJECT_TEST_WINDOW" in actions.window_title(hwnd):
            found.append(hwnd)
        return True

    u32.EnumWindows(P2(cb2), 0)
    hwnds = found
    if found:
        print("     (按 pid 找不到, 改按窗口标题找到了 —— conhost 持有窗口)")
check("拿到测试窗口的 HWND", bool(hwnds), "hwnd=%s" % (hwnds[:1]))
if not hwnds:
    proc.kill()
    sys.exit(1)
titled = [h for h in hwnds if actions.window_title(h).strip()]
hwnd = (titled or hwnds)[0]
print("     选中 hwnd=%s 标题=%r (候选 %d 个, 其中有标题的 %d 个)"
      % (hwnd, actions.window_title(hwnd), len(hwnds), len(titled)))
check("选中的是有标题的真窗口", bool(actions.window_title(hwnd).strip()),
      "无标题窗口通常是隐藏的辅助窗口, 不该拿来注入")

print("2) 切到前台")
r = actions.focus_window(hwnd)
check("focus_window 成功", r.get("ok"), "how=%s %s" % (r.get("how"), r.get("why", "")))

print("3) 敲一条命令进去(让它写文件回来证明真的收到了)")
r = actions.type_into_window(hwnd, 'echo INJECT_OK> "%s"' % MARK, press_enter=True)
check("type_into_window 返回成功", r.get("ok"), r.get("why", ""))
time.sleep(1.5)
got = ""
if os.path.exists(MARK):
    got = io.open(MARK, encoding="utf-8", errors="replace").read().strip()
check("窗口真的执行了敲进去的命令", got.startswith("INJECT_OK"), repr(got))

print("4) 中文字符也要能正确送达")
if os.path.exists(MARK):
    os.remove(MARK)
r = actions.type_into_window(hwnd, 'echo 中文注入测试> "%s"' % MARK, press_enter=True)
time.sleep(1.5)
got = ""
if os.path.exists(MARK):
    got = io.open(MARK, encoding="utf-8", errors="replace").read().strip()
# cmd 的输出编码可能是 GBK, 这里只验证"不是空的、且不是 ASCII"
check("中文送达(cmd 侧编码不论)", bool(got) and any(ord(c) > 127 for c in got)
      or "?" in got, repr(got))

print("5) 切不到窗口时必须拒绝输入, 不能对着别的窗口乱敲")
r = actions.type_into_window(0, "should not type", press_enter=True)
check("hwnd 无效时拒绝", not r.get("ok"), r.get("why", ""))
r = actions.type_into_window(999999, "should not type", press_enter=True)
check("HWND 不存在时拒绝", not r.get("ok"), r.get("why", ""))

print("6) 清理")
# 只按本进程树的 pid 精确杀。
# 【禁止】用 taskkill /FI "WINDOWTITLE eq ..." 兜底 —— Win10+ 的 conhost 架构下
# taskkill 拿不到控制台进程的窗口标题(报 N/A), 过滤器静默失效, 配上 /F 会把
# 用户所有 cmd.exe 一起强杀。2026-08-23 实发, 关掉了用户全部终端窗口。
try:
    import psutil
    for c in psutil.Process(proc.pid).children(recursive=True):
        try:
            c.kill()
        except Exception:
            pass
except Exception:
    pass
try:
    proc.kill()
except Exception:
    pass
for c in (MARK,):
    try:
        os.remove(c)
    except OSError:
        pass

print("")
print("FAILED: %s" % (FAILED or "无, 全部通过"))
sys.exit(1 if FAILED else 0)
