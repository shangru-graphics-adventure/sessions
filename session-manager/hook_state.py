# -*- coding: utf-8 -*-
"""Claude 对话实时状态 hook — 由 settings.json 的三个 hook 调用。

    python hook_state.py SessionStart       < payload.json
    python hook_state.py UserPromptSubmit   < payload.json
    python hook_state.py Stop               < payload.json
    python hook_state.py Notification       < payload.json
    python hook_state.py SessionEnd         < payload.json

每个会话一个状态文件 state/<session_id>.json, 独立文件所以多会话并发不会互相踩。

铁律: 这个脚本挂在**每一个**对话的每一回合上, 所以
  1. 任何异常都吞掉, 永远 exit 0 —— 绝不能因为状态记账失败而中断你干活;
  2. **绝不往 stdout 写任何东西** —— UserPromptSubmit 的 stdout 会被注入进对话上下文。
     调试信息一律进 hook_state.log。
"""
import io
import os
import sys
import json
import time

import utf8_console
utf8_console.enable()

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, "state")
LOG = os.path.join(HERE, "hook_state.log")


def log(msg):
    try:
        with io.open(LOG, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def clean(t, n=140):
    return " ".join((t or "").split())[:n]


def find_owner():
    """顺着父进程链找到"承载本对话的那个 claude.exe", 以及它所在的终端窗口进程。

    实测的链形如:
        python(本脚本) -> ... -> claude.exe -> powershell -> WindowsTerminal.exe
    claude.exe 的 pid + 创建时间就是"这个对话还开着没"的唯一可靠信号 ——
    SessionEnd hook 在窗口被直接关掉时基本不触发(实测 2299 个会话里只有 148 个有记录),
    所以存活判定只能靠进程本身。
    """
    out = {"pid": None, "pid_ctime": None, "term_pid": None, "term_name": None}
    try:
        import psutil
    except Exception:
        return out
    TERMS = ("WindowsTerminal.exe", "conhost.exe", "cmd.exe", "powershell.exe",
             "pwsh.exe", "Code.exe", "explorer.exe")
    try:
        p = psutil.Process()
        seen_claude = False
        for _ in range(16):
            p = p.parent()
            if p is None:
                break
            name = p.name()
            if not seen_claude and name.lower() == "claude.exe":
                out["pid"] = p.pid
                out["pid_ctime"] = round(p.create_time(), 3)
                seen_claude = True
                continue
            if seen_claude and name in TERMS and name != "powershell.exe":
                out["term_pid"] = p.pid
                out["term_name"] = name
                break
    except Exception as e:
        log("find_owner failed: %s" % e)
    return out


def capture_window(term_pid):
    """记下"这个对话所在的终端窗口"的 HWND 和它此刻的标题。

    两件事都靠它:
      · 标题 —— Claude Code 会把自己生成的对话摘要写进终端窗口标题(形如
        "◐ 获得当前对话id"), 这是现成的一句话总结, 免费且实时。窗口一关就没了,
        所以每次 hook 都存一份快照。
      · HWND —— Windows Terminal 是多窗口单进程, 光有 pid 认不出是哪个窗口。
        但 UserPromptSubmit 触发时用户刚敲完回车, **前台窗口就是它**, 抓这一刻最准。

    只在前台窗口确实属于该终端进程时才认; 否则退而求其次: 该进程只有一个窗口就用它,
    有多个就老实记 None —— 宁可没有, 不能记错窗口(切错窗口比不切更烦人)。
    """
    out = {"hwnd": None, "win_title": ""}
    if not term_pid or os.name != "nt":
        return out
    try:
        import ctypes
        import ctypes.wintypes as wt
        u32 = ctypes.windll.user32

        def pid_of(hwnd):
            p = wt.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            return p.value

        def title_of(hwnd):
            n = u32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            return buf.value

        fg = u32.GetForegroundWindow()
        if fg and pid_of(fg) == term_pid:
            return {"hwnd": int(fg), "win_title": title_of(fg)}

        found = []
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

        def cb(hwnd, _):
            if pid_of(hwnd) == term_pid and u32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        u32.EnumWindows(EnumProc(cb), 0)
        if len(found) == 1:
            return {"hwnd": int(found[0]), "win_title": title_of(found[0])}
    except Exception as e:
        log("capture_window failed: %s" % e)
    return out


def merge_proc(rec, info):
    """把这一回合观察到的「进程 + 它的终端窗口」并进 rec["procs"]。

    为什么要一个数组而不是一组顶层字段: **同一个对话可以同时被 resume 到好几个
    窗口里** —— 它们共用一个 session_id, 于是写的是同一个 state 文件。只留一个
    pid 的话, 后开的那个会把先开的顶掉, 页面上就永远只看得见最后活动的那个,
    另外几个既切不过去也关不掉(而它们恰恰是要提醒你去关掉的那些)。

    同一个 pid 再次出现只更新(窗口标题会跟着对话内容变), 不追加。
    顶层的 pid/hwnd 仍然保留 = **最近活动的那个**, 老的 state 文件与既有 UI 因此
    不用改也能继续工作。
    """
    pid = info.get("pid")
    if not pid:
        return
    procs = rec.get("procs")
    if not isinstance(procs, list):
        # 从旧格式(只有顶层 pid)升上来: 先把那一个塞进数组, 不丢历史
        procs = []
        if rec.get("pid"):
            procs.append({k: rec.get(k) for k in
                          ("pid", "pid_ctime", "term_pid", "term_name", "hwnd", "win_title")})
    now = time.time()
    for e in procs:
        if e.get("pid") == pid:
            e.update({k: v for k, v in info.items() if v})
            e["ts"] = now
            break
    else:
        e = dict(info)
        e["ts"] = e["first_seen"] = now
        procs.append(e)
    # 只留最近 8 个, 死掉的那些由 server 端按进程存活过滤, 这里不做进程查询
    rec["procs"] = procs[-8:]


def last_assistant_text(transcript_path, tail_bytes=300_000):
    """取我这一回合最后说的那段话的开头 —— 通常就是结论。"""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        size = os.path.getsize(transcript_path)
        with io.open(transcript_path, "rb") as fh:
            if size > tail_bytes:
                fh.seek(size - tail_bytes)
                fh.readline()
            chunk = fh.read().decode("utf-8", "replace")
        for line in reversed(chunk.splitlines()):
            if '"type":"assistant"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") != "assistant":
                continue
            c = d.get("message", {}).get("content")
            if isinstance(c, str):
                t = c
            elif isinstance(c, list):
                t = "".join(b.get("text", "") for b in c
                            if isinstance(b, dict) and b.get("type") == "text")
            else:
                t = ""
            t = t.strip()
            if t:
                return clean(t, 200)
    except Exception as e:
        log("last_assistant_text failed: %s" % e)
    return ""


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    # **必须读原始字节再按 UTF-8 解码**, 不能用 sys.stdin.read()。
    # sys.stdin 的解码器跟随控制台 code page: 我的测试环境是 65001(utf-8), 而 claude
    # 派生出来的 hook 环境是 cp936, 于是 payload 里的 UTF-8 中文被当 GBK 读成
    # "璇诲苟琛ラ綈" 这种乱码(实测: \xe8\xaf\xbb\xe5\xb9\xb6 = "读并" 被 GBK 解读)。
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception:
        try:
            raw = sys.stdin.read()
        except Exception:
            raw = ""
    if not raw.strip():
        log("%s: stdin empty" % event)
        return
    try:
        data = json.loads(raw)
    except Exception as e:
        log("%s: bad json (%s)" % (event, e))
        return

    sid = data.get("session_id")
    if not sid:
        log("%s: no session_id, keys=%s" % (event, sorted(data.keys())))
        return

    try:
        os.makedirs(STATE_DIR, exist_ok=True)
    except Exception:
        pass
    path = os.path.join(STATE_DIR, "%s.json" % sid)

    rec = {}
    try:
        with io.open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except Exception:
        rec = {}

    now = time.time()
    rec["sid"] = sid
    rec["cwd"] = data.get("cwd") or rec.get("cwd") or ""
    rec["ts"] = now
    rec["last_event"] = event
    # 留一份实际字段名, 方便日后核对 payload 结构变没变
    rec["payload_keys"] = sorted(data.keys())

    if event == "UserPromptSubmit":
        rec["state"] = "running"
        rec["goal"] = clean(data.get("prompt") or data.get("user_prompt") or "")
        rec["started"] = now
        rec["result"] = ""
        rec["note"] = ""
        rec.update(find_owner())
        rec.update(capture_window(rec.get("term_pid")))
        merge_proc(rec, {k: rec.get(k) for k in
                         ("pid", "pid_ctime", "term_pid", "term_name", "hwnd", "win_title")})
    elif event == "SessionStart":
        # 会话刚起来(启动 / --resume / /clear) —— 这是"又多开了一个窗口"的唯一
        # 早期信号: 用户还没说话, 别的事件都不会触发。没有它, 一个刚 resume 出来
        # 的窗口要等到第一次提交才被记上, 而"别重复 resume"的提醒恰恰要在那之前给。
        rec.setdefault("state", "done")
        rec["source"] = clean(data.get("source") or "", 20)
        rec.update(find_owner())
        rec.update(capture_window(rec.get("term_pid")))
        merge_proc(rec, {k: rec.get(k) for k in
                         ("pid", "pid_ctime", "term_pid", "term_name", "hwnd", "win_title")})
    elif event == "Stop":
        rec["state"] = "done"
        rec["note"] = ""
        rec["result"] = last_assistant_text(data.get("transcript_path"))
        rec["took"] = round(now - rec.get("started", now), 1)
        if not rec.get("pid"):
            rec.update(find_owner())
        w = capture_window(rec.get("term_pid"))
        if w.get("win_title"):          # 标题是 Claude Code 自己写的对话摘要, 存最新的
            rec["win_title"] = w["win_title"]
        if w.get("hwnd"):
            rec["hwnd"] = w["hwnd"]
        merge_proc(rec, {k: rec.get(k) for k in
                         ("pid", "pid_ctime", "term_pid", "term_name", "hwnd", "win_title")})
    elif event == "Notification":
        # Claude 需要你的注意: 权限确认、选择、长时间空闲
        rec["state"] = "waiting"
        rec["note"] = clean(data.get("message") or "")
        if not rec.get("pid"):
            rec.update(find_owner())
        merge_proc(rec, {k: rec.get(k) for k in
                         ("pid", "pid_ctime", "term_pid", "term_name", "hwnd", "win_title")})
    elif event == "SessionEnd":
        rec["state"] = "closed"
        rec["reason"] = clean(data.get("reason") or "", 40)

    try:
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        log("%s: write failed: %s" % (event, e))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:      # 绝不让状态记账拖垮真正的工作
        log("fatal: %s" % e)
    sys.exit(0)
