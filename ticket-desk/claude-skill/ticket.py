"""ticket skill 的命令行腿 —— 往本机 Ticket 台 (127.0.0.1:8730) 建/改 ticket。

服务没起会自动用 pythonw 静默拉起（不弹黑窗），起之前先探端口，绝不双绑。

    python ticket.py add "标题" [--parent 关键词] [--todo] [--note "上下文"]
    python ticket.py start|pause|done|undone|archive <关键词或id>
    python ticket.py list [--all]

关键词按标题子串匹配（不分大小写），命中多条会列出来让你重说得更具体。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("TICKET_PORT") or 8730)
BASE = "http://127.0.0.1:%d" % PORT

# Where the Ticket Desk lives.  Resolution order:
#   1. env TICKET_DESK_DIR
#   2. ../  (this skill sits in <repo>/ticket-desk/claude-skill/)
#   3. a one-line path in `desk_path.txt` next to this script
def _find_app() -> Path:
    here = Path(__file__).resolve().parent
    hint = os.environ.get("TICKET_DESK_DIR")
    cands = [Path(hint) / "server.py"] if hint else []
    cands.append(here.parent / "server.py")
    txt = here / "desk_path.txt"
    if txt.exists():
        cands.append(Path(txt.read_text(encoding="utf-8").strip()) / "server.py")
    for c in cands:
        if c.exists():
            return c
    sys.exit("找不到 Ticket 台的 server.py。设一个环境变量 TICKET_DESK_DIR 指向它所在目录, "
             "或在 %s 里写一行路径。" % txt)


APP = _find_app()
CREATE_NO_WINDOW = 0x08000000

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------- 服务

def port_alive() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.settimeout(0.35)
        return sk.connect_ex(("127.0.0.1", PORT)) == 0


def ensure_server() -> None:
    if port_alive():
        return
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw) if pythonw.exists() else sys.executable
    subprocess.Popen([exe, str(APP)], cwd=str(APP.parent),
                     creationflags=CREATE_NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(0.2)
        if port_alive():
            print("（Ticket 台没在跑，已静默拉起 → %s/）" % BASE)
            return
    sys.exit("拉不起 Ticket 台，手动跑: python %s" % APP)


def call(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=8).read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


# ---------------------------------------------------------------- 工具

STATUS = {"todo": "未开始", "working": "工作中", "paused": "暂停", "done": "完成"}


def tickets() -> list:
    return call("/api/state")["tickets"]


def pick(key: str, pool: list) -> dict:
    """按 id 精确 / 标题子串匹配，唯一命中才返回。"""
    exact = [t for t in pool if t["id"] == key]
    if exact:
        return exact[0]
    hits = [t for t in pool if key.lower() in t["title"].lower()]
    if not hits:
        sys.exit("没找到匹配「%s」的 ticket。跑 `list` 看看现有的。" % key)
    if len(hits) > 1:
        print("「%s」匹配到多条，说得更具体些：" % key)
        for t in hits:
            print("  - %s  [%s]  id=%s" % (t["title"], STATUS[t["status"]], t["id"]))
        sys.exit(1)
    return hits[0]


def parse_plan(txt):
    """把 "8/24 09:00" / "09:00" / "明天 14:30" 解析成 epoch 秒。解析不了就报错退出。"""
    if not txt:
        return None
    t = txt.strip().replace("：", ":")
    now = time.localtime()
    day = None
    for word, delta in (("今天", 0), ("明天", 1), ("后天", 2)):
        if t.startswith(word):
            day = time.time() + delta * 86400
            t = t[len(word):].strip()
            break
    if day is None and "/" in t.split()[0]:
        md, _, rest = t.partition(" ")
        try:
            mo, dd = (int(x) for x in md.split("/"))
        except ValueError:
            sys.exit("看不懂的时间: %s（要 \"8/24 09:00\" 或 \"09:00\" 这种）" % txt)
        day = time.mktime((now.tm_year, mo, dd, 12, 0, 0, 0, 0, -1))
        t = rest.strip()
    base = time.localtime(day if day is not None else time.time())
    try:
        hh, _, mm = t.partition(":")
        hh, mm = int(hh), int(mm or 0)
    except ValueError:
        sys.exit("看不懂的时间: %s（要 \"8/24 09:00\" 或 \"09:00\" 这种）" % txt)
    return time.mktime((base.tm_year, base.tm_mon, base.tm_mday, hh, mm, 0, 0, 0, -1))


def fmt_dur(sec: float) -> str:
    sec = int(max(0, sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def union_sec(ivs: list, now: float) -> float:
    a = sorted([[s, (e if e is not None else now)] for s, e in ivs if (e or now) > s])
    total, cs, ce = 0.0, None, None
    for s, e in a:
        if cs is None:
            cs, ce = s, e
        elif s <= ce:
            ce = max(ce, e)
        else:
            total += ce - cs
            cs, ce = s, e
    return total + (ce - cs if cs is not None else 0)


def show(t: dict, pool: list, now: float, indent: int = 0) -> None:
    kids = [x for x in pool if x.get("parent") == t["id"]]
    sess = list(t["sessions"])
    stack = list(kids)
    while stack:
        k = stack.pop()
        sess += k["sessions"]
        stack += [x for x in pool if x.get("parent") == k["id"]]
    when = time.strftime("%m/%d %H:%M", time.localtime(t["created"]))
    plan = ""
    if t.get("planStart") or t.get("planEnd"):
        f = lambda ts: time.strftime("%m/%d %H:%M", time.localtime(ts))
        plan = "  计划 " + ("%s→%s" % (f(t["planStart"]), f(t["planEnd"])[-5:])
                           if t.get("planStart") and t.get("planEnd")
                           else ("%s起" % f(t["planStart"]) if t.get("planStart") else "%s前" % f(t["planEnd"])))
    print("%s%-34s %-4s %8s  下达 %s%s%s" % (
        "  " * indent, t["title"][:34], STATUS[t["status"]],
        fmt_dur(union_sec(sess, now)), when, plan,
        "  id=" + t["id"] if indent == 0 else ""))
    for k in kids:
        show(k, pool, now, indent + 1)


# ---------------------------------------------------------------- 命令

def main() -> None:
    ap = argparse.ArgumentParser(description="Ticket 台命令行")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新建 ticket（默认建完就开始计时）")
    a.add_argument("title")
    a.add_argument("--parent", help="挂到哪个 ticket 下（标题关键词或 id）")
    a.add_argument("--note", default="", help="上下文备注（页面上悬停标题可见）")
    a.add_argument("--todo", action="store_true", help="只登记，不开始计时")
    a.add_argument("--plan-start", help='计划开始（参考值，不影响计时）："8/24 09:00" / "09:00" / "明天 14:30"')
    a.add_argument("--plan-end", help="计划结束，格式同上")

    for name, helptext in [("start", "开始/继续计时"), ("pause", "暂停"), ("done", "标记完成"),
                           ("undone", "退回未完成"), ("archive", "归档（连子树）")]:
        s = sub.add_parser(name, help=helptext)
        s.add_argument("key", help="标题关键词或 id")

    l = sub.add_parser("list", help="列出 ticket")
    l.add_argument("--all", action="store_true", help="连已完成/已归档一起列")

    args = ap.parse_args()
    ensure_server()

    if args.cmd == "add":
        parent = None
        if args.parent:
            parent = pick(args.parent, tickets())["id"]
        plan = {"planStart": parse_plan(args.plan_start), "planEnd": parse_plan(args.plan_end)}
        r = call("/api/create", {"title": args.title, "parent": parent, "note": args.note, **plan})
        if not r.get("ok"):
            sys.exit("建失败: " + r.get("msg", "?"))
        tid = r["id"]
        if not args.todo:
            call("/api/action", {"id": tid, "action": "start"})
        plan_txt = ""
        if plan["planStart"] or plan["planEnd"]:
            f = lambda ts: time.strftime("%m/%d %H:%M", time.localtime(ts))
            plan_txt = "  计划 " + (
                "%s → %s" % (f(plan["planStart"]), f(plan["planEnd"])) if plan["planStart"] and plan["planEnd"]
                else ("%s 起" % f(plan["planStart"]) if plan["planStart"] else "%s 前" % f(plan["planEnd"])))
        print("已建%s: %s%s  (%s)%s" % (
            "并开始计时" if not args.todo else "（未开始）",
            args.title, "  ← 子 ticket" if parent else "",
            time.strftime("%m/%d %H:%M"), plan_txt))
        print(BASE + "/")
        return

    if args.cmd == "list":
        st = call("/api/state")
        pool = [t for t in st["tickets"] if args.all or (not t["archived"] and t["status"] != "done")]
        if not pool:
            print("（没有 ticket）")
            return
        ids = {t["id"] for t in pool}
        for t in [x for x in pool if not x.get("parent") or x["parent"] not in ids]:
            show(t, pool, st["now"])
        return

    t = pick(args.key, tickets())
    r = call("/api/action", {"id": t["id"], "action": args.cmd})
    if not r.get("ok"):
        sys.exit("失败: " + r.get("msg", "?"))
    nt = next(x for x in r["tickets"] if x["id"] == t["id"])
    tail = ""
    if nt["status"] in ("paused", "done") and (nt.get("doneAt") or nt.get("pausedAt")):
        tail = "  于 " + time.strftime("%m/%d %H:%M",
                                       time.localtime(nt.get("doneAt") or nt.get("pausedAt")))
    print("%s → %s%s   累计 %s" % (nt["title"], STATUS[nt["status"]], tail,
                                  fmt_dur(union_sec(nt["sessions"], r["now"]))))


if __name__ == "__main__":
    main()
