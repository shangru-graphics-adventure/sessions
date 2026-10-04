# -*- coding: utf-8 -*-
"""完整回放: 把一次对话按**文件顺序**逐块吐出来, 像在终端里往回滚一样。

与 server.conv_tree 的分工:
  conv_tree  给「我问了什么 / claude 大致回了什么」的轮次骨架, 用来快速找地方。
  replay     给**每一个块**: 用户原话、**插话(queue-operation)**、助手正文、思考、
             每一次工具调用的入参、每一次工具返回的原文。**数据层一律不截断**。

用户 2026-09-09 原话: 「要能复原整个对话, 也就是就好像我在 cmd 里那样, 我要事无巨细」。

★ 2026-09-09 实付的一个大漏: **用户在助手干活时插的话不是 user/text, 而是
  `{"type":"queue-operation","operation":"enqueue","content":"..."}`**。
  只认 user/assistant 的抽取器会把整类插话丢光 —— 主战役会话里有 736 条。
  症状是「顺序不对: 收到目标 → (少了我说的第二句) → 收到补充」。

★ 另一件事实: 转录里**一行 ANSI 转义都没有**(实测 0 行) —— cmd 的颜色没被记录,
  只能按内容重新着色, 那是推断不是原色, 前端必须写明。
"""
import io, json, os, glob, re, time

# 系统回显 / skill 注入: 保留(事无巨细), 但打标, 让前端能一键滤掉
SYS_PAT = re.compile(
    r"^<command-name>|^<local-command|^<command-message>|^<bash-input>|^<bash-stdout>|"
    r"^<task-notification>|^\[SYSTEM NOTIFICATION|^A session-scoped Stop hook|"
    r"^Caveat:|^<system-reminder>|^\[Request interrupted|"
    r"Base directory for this skill|^Approach this as the design lead|^You are Claude Code|"
    r"^Contents of |^This session is being continued from")


def _blocks(msg):
    c = (msg or {}).get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    if isinstance(c, list):
        return [b for b in c if isinstance(b, dict)]
    return []


def _res_text(b):
    c = b.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for x in c:
            if isinstance(x, dict):
                if x.get("type") == "text":
                    out.append(x.get("text") or "")
                elif x.get("type") == "image":
                    out.append("[图片]")
                else:
                    out.append(json.dumps(x, ensure_ascii=False))
            else:
                out.append(str(x))
        return "\n".join(out)
    return "" if c is None else json.dumps(c, ensure_ascii=False)


def scan(path):
    """按文件顺序拆块。k: user | queued | asst | think | tool | res"""
    ev = []
    try:
        fh = io.open(path, encoding="utf-8", errors="replace")
    except Exception:
        return ev
    for line in fh:
        try:
            d = json.loads(line)
        except Exception:
            continue
        ts = (d.get("timestamp") or "")[:19].replace("T", " ")

        # ★ 插话: 用户在助手干活时发的, 存成 queue-operation, 不是 user/text
        if d.get("type") == "queue-operation":
            if d.get("operation") not in (None, "enqueue"):
                continue
            txt = (d.get("content") or "").strip()
            if txt:
                ev.append(dict(k="queued", ts=ts, text=txt, side=False,
                               sys=bool(SYS_PAT.search(txt))))
            continue

        m = d.get("message") or {}
        role = m.get("role") or d.get("type") or ""
        if role not in ("user", "assistant"):
            continue
        side = bool(d.get("isSidechain"))
        for b in _blocks(m):
            t = b.get("type")
            if t == "text":
                txt = (b.get("text") or "").strip()
                if not txt:
                    continue
                ev.append(dict(k="user" if role == "user" else "asst", ts=ts, text=txt,
                               side=side, sys=bool(role == "user" and SYS_PAT.search(txt))))
            elif t == "thinking":
                ev.append(dict(k="think", ts=ts, text=(b.get("thinking") or b.get("text") or "").strip(),
                               side=side, sys=False))
            elif t == "tool_use":
                ev.append(dict(k="tool", ts=ts, name=b.get("name") or "?", id=b.get("id") or "",
                               text=json.dumps(b.get("input") or {}, ensure_ascii=False, indent=1),
                               side=side, sys=False))
            elif t == "tool_result":
                ev.append(dict(k="res", ts=ts, id=b.get("tool_use_id") or "",
                               ok=not bool(b.get("is_error")), text=_res_text(b),
                               side=side, sys=False))
    for i, e in enumerate(ev):
        e["i"] = i
    return ev


_CACHE = {}


def _ev(path):
    key = (path, os.path.getmtime(path) if os.path.exists(path) else 0)
    if _CACHE.get("key") != key:
        _CACHE["key"] = key
        _CACHE["ev"] = scan(path)
    return _CACHE["ev"]


def _counts(ev):
    c = {}
    for e in ev:
        c[e["k"]] = c.get(e["k"], 0) + 1
    c["sys"] = sum(1 for e in ev if e.get("sys"))
    return c


def _mark(ev, kw):
    kws = [x for x in (kw or "").split("|") if x]
    for e in ev:
        e["hit"] = (sum(((e.get("text") or "") + " " + (e.get("name") or "")).count(k) for k in kws)
                    if kws else 0)
    return kws


def events(path, frm=0, n=200, kinds=None, q="", kw="", nosys=False):
    ev = _ev(path)
    _mark(ev, kw)
    sel = ev
    if nosys:
        sel = [e for e in sel if not e.get("sys")]
    if kinds:
        ks = set(x for x in kinds.split(",") if x)
        sel = [e for e in sel if e["k"] in ks]
    if q:
        sel = [e for e in sel if q in (e.get("text") or "") or q in (e.get("name") or "")]
    total = len(sel)
    frm = max(0, min(frm, max(0, total - 1)))
    return dict(total=total, all=len(ev), frm=frm, n=n, rows=sel[frm:frm + n], counts=_counts(ev))


def outline(path, kw="", nosys=False):
    """左树三层: 日期 → 对话(一轮 = 一条我说的 + 它后面挂的一切) → 分步(每个块)。
    分步只给骨架(序号/类型/一行摘要), 正文仍从 /api/replay 取。"""
    ev = _ev(path)
    _mark(ev, kw)
    turns = []
    cur = None
    for e in ev:
        if e["k"] in ("user", "queued") and not e.get("side"):
            if nosys and e.get("sys"):
                pass
            else:
                cur = dict(i=e["i"], ts=e["ts"], day=(e["ts"] or "")[:10],
                           q=(e["text"] or "").replace("\n", " ")[:120],
                           k=e["k"], sys=bool(e.get("sys")),
                           n=0, tools=0, hit=e.get("hit", 0), steps=[])
                turns.append(cur)
                continue
        if cur is None:
            continue
        cur["n"] += 1
        cur["hit"] += e.get("hit", 0)
        if e["k"] == "tool":
            cur["tools"] += 1
        if len(cur["steps"]) < 400:
            lab = e.get("name") or ""
            txt = (e.get("text") or "").replace("\n", " ").strip()
            cur["steps"].append(dict(i=e["i"], k=e["k"], ts=e["ts"][11:19],
                                     name=lab, s=(lab + " " + txt).strip()[:90],
                                     hit=e.get("hit", 0), ok=e.get("ok", True)))
    days, dm = [], {}
    for t in turns:
        if t["day"] not in dm:
            dm[t["day"]] = []
            days.append(t["day"])
        dm[t["day"]].append(t)
    return dict(days=[dict(d=d, turns=dm[d],
                           n=sum(x["n"] for x in dm[d]),
                           hit=sum(x["hit"] for x in dm[d])) for d in days],
                turns=turns, counts=_counts(ev), total=len(ev),
                hits=sum(t["hit"] for t in turns))


# ---------------------------------------------------------------- 会话级★(缓存)
_STAR = {"key": None, "map": {}}


def starred(proj_dir, kw):
    """扫全部 jsonl 数关键词, 返回 {sid: 命中数}。按(文件数, 最新 mtime)缓存 ——
    否则前端每次开页都要跑三次 /api/grep, 一次 7 秒, 星要 20 秒才出来。"""
    files = glob.glob(os.path.join(proj_dir, "*", "*.jsonl"))
    key = (len(files), max([os.path.getmtime(f) for f in files] or [0]), kw)
    if _STAR["key"] == key:
        return _STAR["map"]
    kws = [x.encode("utf-8") for x in (kw or "").split("|") if x]
    out = {}
    t0 = time.time()
    for f in files:
        try:
            with open(f, "rb") as fh:
                blob = fh.read()
        except Exception:
            continue
        n = sum(blob.count(k) for k in kws)
        if n:
            out[os.path.basename(f)[:-6]] = n
    _STAR["key"] = key
    _STAR["map"] = dict(map=out, ms=int((time.time() - t0) * 1000), files=len(files))
    return _STAR["map"]
