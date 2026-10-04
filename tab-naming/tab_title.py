# -*- coding: utf-8 -*-
"""VS Code 终端标签 = 控制台标题(2026-10-04 用户:「如果我不挪动窗口，只是重命名，用一个实心向右三角指示这些窗口在等我…窗口位置稳定…闪动是不行的」)。

为什么不用桥的 /rename: VS Code 的 renameWithArg 只作用于「活动终端」, 改别的标签得先 show() 切过去再切回来(面板闪一下);
而且 API 改过名的标签变成静态标题, 以后进程发的标题全被忽略。
这里改走控制台标题: AttachConsole(claude 进程) + SetConsoleTitleW → ConPTY 发 OSC 0 → VS Code 把它当作进程标题显示。
不碰 VS Code 的任何命令, 不切标签, 不挪位置, 零闪动(10-04 实测: 未被 API 改过名的标签 1 s 内变成新标题)。

前提: Claude Code 自己不再写标题(settings.json env CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1, 只对新启动的会话生效);
      已被 API 改过名的老标签是静态的, 改不动(VS Code 外部没有 rename(undefined) 的入口), 随对话结束自然淘汰。

    python tab_title.py set --sid <会话 id> [--label 文字] [--waiting 1|0]   改标签(写登记 + 立刻生效)
    python tab_title.py apply --sid <会话 id> --waiting 1|0                   只改等你标记(turn_notify 调用)
    python tab_title.py raw --pid <进程 pid> --text 文字                       底层: 直接设某进程所在控制台的标题
    python tab_title.py show

标签名登记在 ~/.claude/tab_labels.json {sid: label}; 正在跑 = 标签前加「▶ 」, 等你 = 不加(10-04 用户纠正:「正在运行的才加三角，在等我的不用」)。"""
import ctypes, glob, io, json, os, subprocess, sys, time, re

HOME = os.path.expanduser("~")
REG = os.path.join(HOME, ".claude", "tab_labels.json")
SESS = os.path.join(HOME, ".claude", "sessions")
MARK = "▶ "
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def load():
    try: return json.load(io.open(REG, encoding="utf-8"))
    except Exception: return {}


def save(d):
    """临时文件带 pid(10-04 实发: 话题脉络 4 路并行写标签, 固定的 .tmp 互相撞 → WinError 5); 替换被读者占住时重试。"""
    tmp = REG + ".tmp%d" % os.getpid()
    with io.open(tmp, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=1)
    for i in range(20):
        try: os.replace(tmp, REG); return
        except PermissionError: time.sleep(0.05)
    os.replace(tmp, REG)


class _Lock:
    """跨进程锁(O_EXCL 锁文件, 10 s 过期): 读-改-写登记表期间别的进程等着, 免得互相覆盖丢名字。"""
    def __enter__(self):
        self.p = REG + ".lock"
        for i in range(200):
            try: os.close(os.open(self.p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)); return self
            except OSError:
                try:
                    if time.time() - os.path.getmtime(self.p) > 10: os.remove(self.p)
                except OSError: pass
                time.sleep(0.05)
        return self
    def __exit__(self, *a):
        try: os.remove(self.p)
        except OSError: pass


def set_label(sid, label):
    with _Lock():
        reg = load(); reg[sid] = label; save(reg)


def session(sid):
    for f in glob.glob(os.path.join(SESS, "*.json")):
        try: d = json.load(io.open(f, encoding="utf-8"))
        except Exception: continue
        if d.get("sessionId") == sid: return d
    return None


def raw_title(pid, text):
    """在本进程里 attach 到 pid 的控制台改标题。会 FreeConsole 本进程 —— 所以对外一律经 set_title() 起一个无窗口子进程来做。"""
    k = ctypes.windll.kernel32
    k.FreeConsole()
    if not k.AttachConsole(int(pid)): return False, "AttachConsole(%s) 失败 err=%d" % (pid, k.GetLastError())
    try: return bool(k.SetConsoleTitleW(text)), "ok"
    finally: k.FreeConsole()


def set_title(pid, text):
    """→ (ok, 说明)。子进程无窗口(CREATE_NO_WINDOW 没有自己的控制台, 正好能 attach)。"""
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "raw", "--pid", str(pid), "--text", text], capture_output=True,
                           timeout=10, creationflags=NOWIN, stdin=subprocess.DEVNULL)
        out = r.stdout.decode("utf-8", "replace").strip()
        return r.returncode == 0, out
    except Exception as e: return False, repr(e)


PROV = "～"   # 临时名前缀(10-04 用户: 新对话标签不许停在「claude」): 第一次提问就用提问前几个字, 第一轮答完 autotitle 换正式名


def provisional(prompt):
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", prompt or "")).strip()
    return (PROV + t[:14]) if t and not t.startswith("/") else ""


def default_label(sid, s, prompt=""):
    """没登记过的: 用 Claude 自己生成的标题(jsonl 里的 ai-title)+ [会话名(id8)]; 都没有就用提问前几个字作临时名(～开头)。"""
    title = ""
    for f in glob.glob(os.path.join(HOME, ".claude", "projects", "*", sid + ".jsonl")):
        try:
            with open(f, "rb") as fh:
                fh.seek(max(0, os.path.getsize(f) - 2_000_000)); raw = fh.read().decode("utf-8", "replace")
            for line in raw.splitlines():
                if '"ai-title"' in line:
                    try: title = json.loads(line).get("aiTitle") or title
                    except ValueError: pass
        except OSError: pass
    return "%s [%s(%s)]" % ((title or provisional(prompt) or "Claude")[:40], (s or {}).get("name") or "?", sid[:8])


TURN = os.path.join(HOME, ".claude", "turn_state.json")     # 在跑/等你 的唯一记录(对话管理器与在线对话面板都读它, 见 turn_push.py)


def record_state(sid, waiting, label=""):
    """与标签 ▶ 同一时刻写: {sid: {state: running|waiting, ts, label}}。原子替换, 失败不影响改标题。"""
    try:
        try:
            with io.open(TURN, encoding="utf-8") as f: d = json.load(f)
        except Exception: d = {}
        now = time.time()
        d = {k: v for k, v in d.items() if now - v.get("ts", 0) < 7 * 86400}
        d[sid] = {"state": "waiting" if waiting else "running", "ts": now, "label": label or (d.get(sid) or {}).get("label", "")}
        tmp = TURN + ".tmp%d" % os.getpid()
        with io.open(tmp, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, TURN)
    except Exception:
        pass


def apply(sid, waiting=None, label=None, prompt=""):
    """改 sid 那个对话的标签: label 为空用登记 / 默认; waiting None = 按会话文件 status 判断。→ (ok, 说明)"""
    s = session(sid)
    if not s: return False, "会话 %s 没在跑" % sid[:8]
    if label:
        set_label(sid, label.replace(MARK, "").strip()[:80])
    reg = load()
    lab = reg.get(sid)
    if not lab:                                   # 第一次: 算默认名并登记(之后 hook 里不再扫 jsonl)
        lab = default_label(sid, s, prompt); set_label(sid, lab)
    if waiting is None: waiting = s.get("status") in ("idle", "shell", "waiting")
    record_state(sid, waiting, lab)
    return set_title(s["pid"], ("" if waiting else MARK) + lab)   # 10-04 用户纠正: ▶ = 正在跑, 等你的不加


def main():
    import argparse
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("raw"); p.add_argument("--pid", required=True); p.add_argument("--text", required=True)
    for n in ("set", "apply"):
        p = sp.add_parser(n); p.add_argument("--sid", required=True); p.add_argument("--label"); p.add_argument("--waiting", choices=["0", "1"])
    sp.add_parser("show")
    sp.add_parser("all", help="按登记名 + 当前状态, 把所有活着的交互对话的标题重写一遍(手动解锁老标签后用)")
    a = ap.parse_args()
    if a.cmd == "all":
        if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        n = 0
        for f in glob.glob(os.path.join(SESS, "*.json")):
            try: d = json.load(io.open(f, encoding="utf-8"))
            except Exception: continue
            if d.get("kind") != "interactive" or not d.get("sessionId"): continue
            ok, why = apply(d["sessionId"]); n += ok
            print("ok " if ok else "失败", d.get("name"), d.get("status"), "" if ok else why)
        print("重写 %d 个" % n); return
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.cmd == "raw":
        ok, why = raw_title(a.pid, a.text); print(why); sys.exit(0 if ok else 1)
    if a.cmd in ("set", "apply"):
        ok, why = apply(a.sid, None if a.waiting is None else a.waiting == "1", a.label); print(ok, why); sys.exit(0 if ok else 1)
    for sid, lab in load().items(): print(sid[:8], lab)


if __name__ == "__main__":
    main()
