"""个人 ticket 管理台 —— 记录下达时间、跑秒表、子 ticket 时间并入父级（不重复计）。

只绑 127.0.0.1（不对外），数据落本目录的 tickets.json（原子写）。

用法:
    python server.py            # 端口默认 8730, 用环境变量 TICKET_PORT 改
    然后开 http://localhost:8730/

设计要点（时间口径，改代码前必读 README.md）:
    每个 ticket 不存"累计秒数"，只存运行区间 sessions = [[start, end|null], ...]。
    自身耗时 = Σ(end-start)；子树耗时 = 该子树全部区间取【并集】后的总长度。
    并集 = 集合测度，所以多个子 ticket 同时在跑、或父子同时在跑，重叠部分天然只算一份。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("TICKET_DATA") or (HERE / "tickets.json"))
PORT = int(os.environ.get("TICKET_PORT") or 8730)

_lock = threading.Lock()

TODO, WORKING, PAUSED, DONE = "todo", "working", "paused", "done"


# ---------------------------------------------------------------- 存储

def load() -> dict:
    if not DATA.exists():
        return {"tickets": []}
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except Exception:
        # 坏档不覆盖，改名留证据
        DATA.replace(DATA.with_name("tickets.broken.%d.json" % int(time.time())))
        return {"tickets": []}


def save(state: dict) -> None:
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(DATA)


def _ts(v):
    """计划时间：epoch 秒或 None。空串/0/null 一律当没设。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def find(state: dict, tid):
    return next((t for t in state["tickets"] if t["id"] == tid), None)


def descendants(state: dict, tid: str) -> list:
    """tid 的全部后代（不含自己）。"""
    kids = [t for t in state["tickets"] if t.get("parent") == tid]
    out = list(kids)
    for k in kids:
        out.extend(descendants(state, k["id"]))
    return out


# ---------------------------------------------------------------- 状态机

def open_session(t: dict):
    return next((s for s in t["sessions"] if s[1] is None), None)


def close_open(t: dict, now: float):
    s = open_session(t)
    if s:
        s[1] = now


def act(state: dict, tid, action) -> dict:
    t = find(state, tid)
    if not t:
        return {"ok": False, "msg": "没有这个 ticket"}
    now = time.time()

    if action == "start":
        if t["status"] != WORKING:
            close_open(t, now)              # 防御：不该有开区间，有就先收口
            t["sessions"].append([now, None])
            t["status"] = WORKING
            t["doneAt"] = None
    elif action == "pause":
        if t["status"] == WORKING:
            close_open(t, now)
            t["status"] = PAUSED
            t["pausedAt"] = now
    elif action == "done":
        close_open(t, now)
        t["status"] = DONE
        t["doneAt"] = now
    elif action == "undone":
        if t["status"] == DONE:
            t["doneAt"] = None
            t["status"] = PAUSED if t["sessions"] else TODO
    elif action == "archive":
        if t["status"] != DONE:
            return {"ok": False, "msg": "只有已完成的才能归档"}
        t["archived"] = True
        for d in descendants(state, tid):
            d["archived"] = True
    elif action == "unarchive":
        t["archived"] = False
        for d in descendants(state, tid):
            d["archived"] = False
    else:
        return {"ok": False, "msg": "未知动作: %s" % action}
    return {"ok": True}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "TicketDesk/1.0"

    def log_message(self, *a):  # 别刷屏
        pass

    # ---- 工具
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---- 路由
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            with _lock:
                st = load()
            self._json({"ok": True, "now": time.time(), "tickets": st["tickets"]})
        else:
            self._json({"ok": False, "msg": "404"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        with _lock:
            st = load()
            res = self._handle(path, body, st)
            if res.get("ok"):
                save(st)
            res["now"] = time.time()
            res["tickets"] = st["tickets"]
        self._json(res, 200 if res.get("ok") else 400)

    def _handle(self, path: str, b: dict, st: dict) -> dict:
        if path == "/api/create":
            title = (b.get("title") or "").strip()
            if not title:
                return {"ok": False, "msg": "标题不能为空"}
            parent = b.get("parent") or None
            if parent and not find(st, parent):
                return {"ok": False, "msg": "父 ticket 不存在"}
            t = {
                "id": "t" + uuid.uuid4().hex[:10],
                "title": title,
                "note": (b.get("note") or "").strip(),
                "parent": parent,
                "created": time.time(),      # 命令下达时间
                "status": TODO,
                "sessions": [],
                "doneAt": None,
                "pausedAt": None,
                "archived": False,
                # 计划时间：纯参考，不参与任何计时。None = 没勾选
                "planStart": _ts(b.get("planStart")),
                "planEnd": _ts(b.get("planEnd")),
            }
            st["tickets"].insert(self._tail_index(st, parent), t)
            return {"ok": True, "id": t["id"]}

        if path == "/api/edit":
            t = find(st, b.get("id"))
            if not t:
                return {"ok": False, "msg": "没有这个 ticket"}
            if "title" in b:
                title = (b["title"] or "").strip()
                if not title:
                    return {"ok": False, "msg": "标题不能为空"}
                t["title"] = title
            if "note" in b:
                t["note"] = (b["note"] or "").strip()
            for k in ("planStart", "planEnd"):
                if k in b:
                    t[k] = _ts(b[k])
            return {"ok": True}

        if path == "/api/action":
            return act(st, b.get("id"), b.get("action"))

        if path == "/api/delete":
            t = find(st, b.get("id"))
            if not t:
                return {"ok": False, "msg": "没有这个 ticket"}
            doomed = {t["id"]} | {d["id"] for d in descendants(st, t["id"])}
            st["tickets"] = [x for x in st["tickets"] if x["id"] not in doomed]
            return {"ok": True, "removed": len(doomed)}

        if path == "/api/move":
            # 同层重排：把 id 移到 before 之前（before=null → 该层末尾）
            t = find(st, b.get("id"))
            if not t:
                return {"ok": False, "msg": "没有这个 ticket"}
            before = b.get("before")
            tgt = find(st, before) if before else None
            if before and (not tgt or tgt.get("parent") != t.get("parent") or tgt is t):
                return {"ok": False, "msg": "只能在同一层内拖动"}
            st["tickets"].remove(t)
            if tgt is not None:
                st["tickets"].insert(st["tickets"].index(tgt), t)
            else:
                st["tickets"].insert(self._tail_index(st, t.get("parent")), t)
            return {"ok": True}

        if path == "/api/reparent":
            # 层级调整：⇥ 变成上一个兄弟的子 / ⇤ 升到父的同层
            t = find(st, b.get("id"))
            if not t:
                return {"ok": False, "msg": "没有这个 ticket"}
            new_parent = b.get("parent") or None
            if new_parent:
                if new_parent == t["id"]:
                    return {"ok": False, "msg": "不能挂到自己下面"}
                if new_parent in {d["id"] for d in descendants(st, t["id"])}:
                    return {"ok": False, "msg": "不能挂到自己的子孙下面"}
                if not find(st, new_parent):
                    return {"ok": False, "msg": "父 ticket 不存在"}
            t["parent"] = new_parent
            st["tickets"].remove(t)
            st["tickets"].insert(self._tail_index(st, new_parent), t)
            return {"ok": True}

        return {"ok": False, "msg": "404: " + path}

    @staticmethod
    def _tail_index(st: dict, parent) -> int:
        """同 parent 的最后一个元素之后的下标（该层没人则放数组末尾）。"""
        idx = len(st["tickets"])
        for i, x in enumerate(st["tickets"]):
            if x.get("parent") == parent:
                idx = i + 1
        return idx


def main():
    if not DATA.exists():
        save({"tickets": []})
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("ticket 台 -> http://localhost:%d/   (data: %s)" % (PORT, DATA))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n收工")


if __name__ == "__main__":
    main()
