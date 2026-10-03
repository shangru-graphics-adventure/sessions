# -*- coding: utf-8 -*-
"""「是否轮到用户」判断 + 响铃 + VS Code 标签分割线位置(用户 2026-10-03:「每次工具运行完响声音的时候判断是否轮到我回答，
如果轮到我就把那个会话移动到分割线以下……有时工具运行完还没轮到我也会响……我只希望在要我回答的时候响+移动位置」)。

    python turn_notify.py Stop | Notification | UserPromptSubmit     < hook payload
    python turn_notify.py --selftest

轮到用户 =
  Stop: 交互会话(~/.claude/sessions/<pid>.json 里 kind=interactive) 且 没有在跑的后台任务(payload.background_tasks 里没有 running / pending)
        —— 把提示音直接挂在所有 Stop 上的问题: 脚本起的 headless `claude -p`、放后台任务后结束本轮(之后会被唤醒接着干)都会响, 这是误响来源。
  Notification: 权限确认 / 需要输入(message 含 permission / 权限 / input / 等待) → 轮到用户; 其余通知不动。
不轮到: UserPromptSubmit(用户刚答完) → 挪回分割线上。
动作: 轮到 → 响铃($CLAUDE_TURN_SOUND 指向的 .wav, 没有就用系统提示音) + 桥 /place below; UserPromptSubmit → /place above。
开关: 存在 ~/.claude/scripts/turn_notify_noplace 则只响不挪。铁律: 吞掉一切异常, exit 0, stdout 不写。"""
import io, json, os, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "turn_notify.log")
SESS = os.path.join(os.path.expanduser("~"), ".claude", "sessions")
SOUND = os.environ.get("CLAUDE_TURN_SOUND", "")
NOPLACE = os.path.join(HERE, "turn_notify_noplace")
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ACTIVE = {"running", "pending", "in_progress", "started", "queued"}


def log(m):
    try:
        with io.open(LOG, "a", encoding="utf-8") as f: f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), m))
    except Exception: pass


def session_file(sid):
    try:
        for n in os.listdir(SESS):
            if not n.endswith(".json"): continue
            try: d = json.load(io.open(os.path.join(SESS, n), encoding="utf-8"))
            except Exception: continue
            if d.get("sessionId") == sid: return d
    except OSError: pass
    return None


def active_bg(bt):
    """background_tasks 的确切结构未文档化: 列表/字典里每项有 status 就看 status, 没有就算在跑。→ 在跑的个数"""
    if not bt: return 0
    items = bt.values() if isinstance(bt, dict) else bt if isinstance(bt, list) else [bt]
    n = 0
    for x in items:
        st = str((x.get("status") or x.get("state") or "")).lower() if isinstance(x, dict) else ""
        if not st or st in ACTIVE: n += 1
    return n


def decide(event, p, sess):
    """→ (轮到用户?, 原因, 挪动方向 None|below|above)"""
    if event == "UserPromptSubmit":
        return False, "用户刚提问", "above"
    if sess is None or sess.get("kind") != "interactive":
        return False, "非交互会话(headless / 子进程)", None
    if event == "Stop":
        n = active_bg(p.get("background_tasks"))
        if n: return False, "还有 %d 个后台任务在跑, 之后会被唤醒" % n, None
        if p.get("stop_hook_active"): return False, "stop hook 链中(会继续)", None
        return True, "本轮结束且无后台任务", "below"
    if event == "Notification":
        m = str(p.get("message") or "").lower()
        if any(k in m for k in ("permission", "权限", "approve", "批准")): return True, "权限确认", "below"
        if any(k in m for k in ("waiting for your input", "input", "等待")): return False, "空闲提醒(已在等, 不再响)", "below"
        return False, "其他通知: " + m[:60], None
    return False, "未处理事件 " + event, None


def live_ports():
    """只返回在监听的桥端口: 本机连未监听端口 Windows 要 ~2 s 才报拒绝, 先用 0.15 s socket 探测(10-03 实测 8 端口串行 14.9 s)。"""
    import socket
    out = []
    for port in range(8721, 8729):
        s = socket.socket(); s.settimeout(0.15)
        try:
            if s.connect_ex(("127.0.0.1", port)) == 0: out.append(port)
        except Exception: pass
        finally: s.close()
    return out


def place(sess, where):
    if os.path.exists(NOPLACE) or not sess: return "关闭挪动"
    try:
        import psutil
        sp = psutil.Process(int(sess["pid"])).ppid()
    except Exception as e:
        return "找不到 shell pid: %r" % e
    for port in live_ports():
        req = urllib.request.Request("http://127.0.0.1:%d/place" % port, method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"pid": sp, "where": where}).encode("utf-8"))
        try:
            with urllib.request.urlopen(req, timeout=4) as r: d = json.loads(r.read().decode("utf-8"))
        except Exception: continue
        if d.get("ok"): return "port %d moved=%s" % (port, d.get("moved"))
    return "没有桥认领 pid %s(或桥是旧版, 需重启扩展宿主)" % sp


def ding():
    """不阻塞 hook: 起一个无窗口的子进程放声音(winsound 同步播放会卡住 hook)。"""
    code = ("import winsound,sys; f=sys.argv[1]; "
            "winsound.PlaySound(f, winsound.SND_FILENAME) if f else winsound.MessageBeep(winsound.MB_ICONASTERISK)")
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    subprocess.Popen([pyw if os.path.exists(pyw) else sys.executable, "-c", code, SOUND],
                     creationflags=NOWIN | getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(event):
    try:
        p = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        sid = p.get("session_id") or ""
        sess = session_file(sid)
        turn, why, where = decide(event, p, sess)
        if turn: ding()
        res = place(sess, where) if where else "-"
        bt = p.get("background_tasks")
        log("%s %s %s turn=%s %s | place %s | bg=%s msg=%r keys=%s" % (event, sid[:8], (sess or {}).get("name"), turn, why, res,
            json.dumps(bt, ensure_ascii=False)[:300] if bt is not None else None, str(p.get("message") or "")[:80], sorted(p.keys())))
    except Exception as e:  # noqa: BLE001
        log("error %s %r" % (event, e))


def selftest():
    S = {"kind": "interactive", "pid": 1}
    ok = [decide("Stop", {}, S)[0] is True,
          decide("Stop", {"background_tasks": [{"id": "x", "status": "running"}]}, S)[0] is False,
          decide("Stop", {"background_tasks": [{"id": "x", "status": "completed"}]}, S)[0] is True,
          decide("Stop", {}, None)[0] is False,
          decide("Stop", {}, {"kind": "print"})[0] is False,
          decide("Notification", {"message": "Claude needs your permission to use Bash"}, S)[0] is True,
          decide("Notification", {"message": "Claude is waiting for your input"}, S)[0] is False,
          decide("UserPromptSubmit", {}, S)[2] == "above"]
    print("自测", ok); return all(ok)


if __name__ == "__main__":
    if "--selftest" in sys.argv: sys.exit(0 if selftest() else 1)
    main(sys.argv[1] if len(sys.argv) > 1 else "Stop")
    sys.exit(0)
