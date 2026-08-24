# -*- coding: utf-8 -*-
"""对比几种"这些 pid 还活着吗"的做法, 选最快的。

/api/status 每 2 秒被轮询一次, 所以这一步必须是毫秒级。
"""
import os
import sys
import time
import ctypes
import ctypes.wintypes as wt

import psutil

TARGET = "claude.exe"


def t(fn, *a):
    t0 = time.time()
    r = fn(*a)
    return (time.time() - t0) * 1000, r


# --- A: psutil 全表遍历(现在用的, 慢) ---
def a_process_iter():
    m = {}
    for p in psutil.process_iter(["pid", "name", "create_time"]):
        if (p.info.get("name") or "").lower() == TARGET:
            m[p.info["pid"]] = p.info.get("create_time") or 0
    return m


# --- B: 只查我们关心的那几个 pid ---
def b_targeted(pids):
    m = {}
    for pid in pids:
        try:
            p = psutil.Process(pid)
            if p.name().lower() == TARGET:
                m[pid] = p.create_time()
        except Exception:
            pass
    return m


# --- C: Toolhelp32 快照(纯 ctypes, 不碰每个进程的句柄) ---
TH32CS_SNAPPROCESS = 0x2


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260)]


def c_toolhelp():
    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return {}
    out = {}
    e = PROCESSENTRY32()
    e.dwSize = ctypes.sizeof(PROCESSENTRY32)
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.decode("latin-1").lower() == TARGET:
            out[e.th32ProcessID] = 0
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


# --- D: Toolhelp32 拿 pid 列表, 再只对命中的 pid 取创建时间 ---
def d_hybrid():
    pids = c_toolhelp()
    out = {}
    for pid in pids:
        try:
            out[pid] = psutil.Process(pid).create_time()
        except Exception:
            out[pid] = 0
    return out


ms, ra = t(a_process_iter)
print("A psutil.process_iter 全表      : %8.1f ms   找到 %d 个 claude.exe" % (ms, len(ra)))

pids = list(ra.keys())[:60] or [os.getpid()]
ms, rb = t(b_targeted, pids)
print("B psutil 只查 %2d 个已知 pid     : %8.1f ms   确认 %d 个" % (len(pids), ms, len(rb)))

ms, rc = t(c_toolhelp)
print("C Toolhelp32 快照(纯 pid)      : %8.1f ms   找到 %d 个" % (ms, len(rc)))

ms, rd = t(d_hybrid)
print("D Toolhelp32 + 逐个取创建时间   : %8.1f ms   找到 %d 个" % (ms, len(rd)))

print("")
print("A 与 C 找到的 pid 集合一致: %s" % (set(ra) == set(rc)))
print("D 的创建时间与 A 一致    : %s" % all(
    abs(rd.get(p, -1) - ra.get(p, -2)) < 0.01 for p in ra))
