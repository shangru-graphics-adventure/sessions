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


def handle(sid, state=STATE, launch=True):
    """→ True 表示本次启动了 autotitle(第一次提问), False 表示已经启动过。"""
    if not sid or len(sid) < 32: return False
    os.makedirs(state, exist_ok=True)
    mk = os.path.join(state, sid)
    if os.path.exists(mk): return False
    with open(mk, "w") as f: f.write(str(time.time()))
    if launch:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen([pyw if os.path.exists(pyw) else sys.executable, TAB, "autotitle", "--sid", sid],
                         creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log("%s 第一轮答完 → autotitle" % sid[:8])
    return True


def main():
    """10-04 用户:「应该在第一次回答结束的时候，才去更新标题」—— 起名改由 turn_notify.py 在 Stop(第一轮答完)时调 handle()。
    UserPromptSubmit 上的这个 hook 保留为空操作(settings.json 不必改)。"""
    try:
        p = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        if (p.get("hook_event_name") or "UserPromptSubmit") == "Stop": handle(p.get("session_id") or "")
    except Exception as e:  # noqa: BLE001
        log("error %r" % e)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import tempfile
        d = tempfile.mkdtemp(); sid = "selftest-0000-0000-0000-%012d" % os.getpid()
        ok = [handle(sid, d, launch=False) is True, handle(sid, d, launch=False) is False, handle("", d, launch=False) is False]
        print("自测", ok); sys.exit(0 if all(ok) else 1)
    main()
    sys.exit(0)
