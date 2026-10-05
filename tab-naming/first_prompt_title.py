# -*- coding: utf-8 -*-
"""UserPromptSubmit hook: 每个会话第一次提问时, 在后台(无窗口)启动 tabname.py autotitle ——
等 Claude Code 自动生成窗口标题后, 把 VS Code 标签改成「<标题> [<会话名>(<id 前 8 位>)]」(用户 2026-10-03; 暂不写版本号,
交接链上的会话由 handoff 命名, autotitle 会跳过)。

铁律: 任何异常都吞掉并 exit 0; stdout 不写任何东西; 本 hook 只做「记一笔 + 起后台进程」, 不等任何东西(< 50 ms)。
  python first_prompt_title.py --selftest     阳性对照: 同一会话第二次调用不再启动"""
import io, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "first_prompt_state")
TAB = os.path.join(HERE, "tabname.py")
LOG = os.path.join(HERE, "first_prompt_title.log")


def log(m):
    try:
        with io.open(LOG, "a", encoding="utf-8") as f: f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), m))
    except Exception: pass


def handle(sid, state=STATE, launch=True, early=False):
    """→ True 表示本次启动了 autotitle, False 表示已经启动过。
    early=True: 第一次提问一提交就起(10-04 用户「新对话的标题生成也太马虎了，都是截断的」—— 以前要等第一轮答完才起正式名,
    第一轮常跑好几分钟, 这期间标签一直是「～提问前 14 字」); 第一轮答完(early=False)再按「助手第一次回答」精修一次。"""
    if not sid or len(sid) < 32: return False
    os.makedirs(state, exist_ok=True)
    mk = os.path.join(state, sid + (".early" if early else ""))
    if os.path.exists(mk): return False
    with open(mk, "w") as f: f.write(str(time.time()))
    if launch:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen([pyw if os.path.exists(pyw) else sys.executable, TAB, "autotitle", "--sid", sid] + (["--early"] if early else []),
                         creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log("%s %s → autotitle" % (sid[:8], "第一次提问" if early else "第一轮答完"))
    return True


def interactive(sid):
    """只给交互会话起名: 起名本身跑的 claude -p(haiku)也会触发本 hook, 不挡就会无限套娃。"""
    if os.environ.get("TABNAME_TITLER"): return False
    d = os.path.join(os.path.expanduser("~"), ".claude", "sessions")
    try:
        for n in os.listdir(d):
            if not n.endswith(".json"): continue
            try: s = json.load(io.open(os.path.join(d, n), encoding="utf-8"))
            except Exception: continue
            if s.get("sessionId") == sid: return s.get("kind") == "interactive"
    except OSError: pass
    return False


def main():
    """10-04 用户:「应该在第一次回答结束的时候，才去更新标题」—— 起名改由 turn_notify.py 在 Stop(第一轮答完)时调 handle()。
    UserPromptSubmit 上的这个 hook 保留为空操作(settings.json 不必改)。"""
    try:
        p = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        ev, sid = p.get("hook_event_name") or "UserPromptSubmit", p.get("session_id") or ""
        if ev == "Stop": handle(sid)
        elif ev == "UserPromptSubmit" and not os.path.exists(os.path.join(STATE, sid)) and interactive(sid): handle(sid, early=True)
    except Exception as e:  # noqa: BLE001
        log("error %r" % e)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import tempfile
        d = tempfile.mkdtemp(); sid = "selftest-0000-0000-0000-%012d" % os.getpid()
        ok = [handle(sid, d, launch=False) is True, handle(sid, d, launch=False) is False, handle("", d, launch=False) is False,
              handle(sid, d, launch=False, early=True) is True, handle(sid, d, launch=False, early=True) is False,
              interactive("not-a-real-session") is False]
        print("自测", ok); sys.exit(0 if all(ok) else 1)
    main()
    sys.exit(0)
