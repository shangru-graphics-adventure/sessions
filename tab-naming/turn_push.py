# -*- coding: utf-8 -*-
"""对话「在跑 / 等你」状态的事件推送(对话管理器 :8720 与任何本地看板共用)。
目标: 标签上的 ▶、对话管理器、在线对话面板三处状态同源、同时切换, 而且用事件推送代替轮询。

状态唯一来源: tab_title.record_state 写的 ~/.claude/turn_state.json(与标签 ▶ 同一时刻), 加上 ~/.claude/sessions/*.json(进程开/关)。
服务端一个后台线程每 0.25 s 看这些文件的修改时刻(只 stat, 微秒级), 变了就唤醒所有 SSE 连接; 浏览器 EventSource 收到才重拉。
为什么不让钩子直接 POST 给服务: 本机连未监听端口约 2 s 才报拒绝, 服务没开时会拖慢每次切 ▶。

用法(服务端, BaseHTTPRequestHandler + ThreadingHTTPServer):
    import turn_push
    W = turn_push.watcher()                       # 进程内单例
    if path == "/api/live/stream": return turn_push.sse(self, W)
    if path == "/api/live/ver":    return self.send(200, {"v": turn_push.version()})
浏览器:
    new EventSource("/api/live/stream").addEventListener("live", e => reload())
"""
import glob, json, os, threading, time

HOME = os.path.expanduser("~")
TURN = os.path.join(HOME, ".claude", "turn_state.json")
SESS = os.path.join(HOME, ".claude", "sessions")


def _dir_max(d, suffix=".json"):
    """目录里 *suffix 文件的最新修改时刻。Windows 上 scandir 的 stat 来自目录枚举本身, 不再逐个开文件(350 个 4.8 ms → 见 README 实测)。"""
    v = 0.0
    try:
        with os.scandir(d) as it:
            for e in it:
                if e.name.endswith(suffix):
                    try: v = max(v, e.stat().st_mtime)
                    except OSError: pass
    except OSError:
        pass
    return v


def version(extra=()):
    """extra: 额外要盯的目录(对话管理器把自己的 state/ 加进来), 只看 *.json。"""
    try: v = os.stat(TURN).st_mtime
    except OSError: v = 0.0
    for d in (SESS,) + tuple(extra):
        v = max(v, _dir_max(d))
    return v


def states():
    try:
        with open(TURN, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}


def manager_state(st, T, rec_ts):
    """对话管理器的四态(running/done/waiting/closed)按同一来源校正: 钩子记录不比管理器自己的账旧(2 s 容差)就以它为准。"""
    if st == "closed" or not T or T.get("ts", 0) < (rec_ts or 0) - 2:
        return st
    if T.get("state") == "running":
        return "running"
    return "waiting" if st == "waiting" else "done"


def status_of(sess, T):
    """sess = ~/.claude/sessions/<pid>.json 的内容(或 None), T = states()[sid]。
    以钩子记录为准(它决定标签 ▶); 会话文件更新得更晚(新开 / 退出)才退回会话文件的 idle/busy。→ busy | idle | shell | closed"""
    st = sess.get("status") if sess else "closed"
    if st == "waiting":                           # Claude Code 新状态值(10-04 实见): 在等你 —— 旧代码不认识它, 落进了「在跑」组
        st = "idle"
    if sess and T and T.get("ts", 0) >= (sess.get("updatedAt") or 0) / 1000 - 2:
        st = "busy" if T.get("state") == "running" else "idle"
    return st


class Watch:
    def __init__(self, period=0.25, extra=()):
        self.extra = tuple(extra); self.v = version(self.extra); self.cond = threading.Condition()
        threading.Thread(target=self._run, args=(period,), daemon=True, name="turn_push").start()

    def _run(self, period):
        while True:
            time.sleep(period)
            try: v = version(self.extra)
            except Exception: continue
            if v != self.v:
                with self.cond: self.v = v; self.cond.notify_all()

    def wait(self, last, timeout):
        with self.cond:
            self.cond.wait_for(lambda: self.v != last, timeout)
            return self.v


_W = None
_L = threading.Lock()


def watcher(extra=()):
    global _W
    with _L:
        if _W is None: _W = Watch(extra=extra)
        return _W


def sse(h, W=None, event="live", ping_s=15):
    """把当前请求变成 SSE 长连接: 一连上先发一次当前版本, 之后每次变化发 event, 空闲每 15 s 发注释保活。客户端断开即返回。"""
    W = W or watcher()
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream; charset=utf-8")
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Accel-Buffering", "no")
    h.end_headers()
    last = None
    try:
        while True:
            v = W.v if last is None else W.wait(last, ping_s)
            if v != last:
                h.wfile.write(("event: %s\ndata: %s\n\n" % (event, json.dumps({"v": v}))).encode("utf-8")); last = v
            else:
                h.wfile.write(b": ping\n\n")
            h.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        return
