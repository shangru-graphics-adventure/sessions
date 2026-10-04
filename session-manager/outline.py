# -*- coding: utf-8 -*-
"""话题脉络 —— 每个对话一棵小折叠树: 谈了几个话题、各在第几轮、每个话题几句要点。

用户 2026-10-04:「我有时会在对话里谈另一个话题，这时候话题偏移了，我很容易忘记这个对话一开始在讲什么。
所以在管理器里应该有个小折叠树，上面寥寥数语大致记录整个对话的大致流程，谈了几个话题之类的，
每次回答都有个hook去判定话题有没有变化，有没必要去更新这个小部分」

触发: hook_state.py 在 Stop 时以无窗口后台进程拉起 `pythonw outline.py <sid>`; 网页「生成脉络」按钮走 POST /api/outline。
增量: 只把「上次脉络 + 之后新增的几轮(提问 + 回复开头)」交给模型, 让它判定话题变没变; 没变只挪水位, 不改字。
      第一次(没有旧脉络)给全部轮次的提问摘要。所以每次回答的成本是一次 haiku 小调用(~1–3k 输入 token)。
存放: state/outline/<sid>.json  {sid, upto(已覆盖到第几轮), topics:[{t, from, to, pts[]}], ts, ms, model, changed}
同一会话同时只跑一个(锁文件, 180 s 过期); 没赶上的新轮次下次 Stop 自然补上(按 upto 水位)。

    python outline.py <sid> [--force]     # --force: 不管水位, 整份重写
"""
import io
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402

OUT_DIR = os.path.join(HERE, "state", "outline")
TITLER = os.path.join(HERE, "titler")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MODEL = (os.environ.get("SESSIONS_OUTLINE_MODEL")
         or getattr(config, "_cfg", {}).get("outline_model") or "haiku")
TIMEOUT = 240                # 10-04: 大对话 claude -p 实测 50–110 s, 120 s 超时过 2 个
CORPUS_MAX = 16000          # 给模型的记录最多多少字(超了中间轮次只留提问开头)

SYSP = "你是一个对话脉络记录员。只输出一个 JSON 对象，不做任何其他事，不使用任何工具。"

PROMPT = u"""你在维护一个 Claude Code 对话的「话题脉络」。读者是对话的主人：他隔了几天回来，打开这个对话，
要在 10 秒内看懂「这个对话一共做了几件事、每件事要解决什么、为什么做、最后怎么样了」。

{old}

{new}

输出一个 JSON 对象：
{{"changed": true 或 false, "title": "对话标题", "summary": "白话总结", "next": "等你回复" 或 "可以关了", "topics": [{{"t": "事情名(白话, ≤14字)", "from": 起始轮号, "to": 结束轮号, "q": "问题: 要解决什么(一句话)", "why": "动机: 为什么要做(一句话)", "res": "结论: 最后怎么样了 / 现在卡在哪(一句话)"}}]}}

规则：
1. 一个 topic = 用户要做成的一件事。同一件事的追问、修正、验收、排错都并进这件事；用户转去做另一件事才开新的。
   一个对话通常 2–6 件事；用户在 AI 干活中途插话提出的新要求，也算一件事。
2. q / why / res 各一句话（≤50 字），**写给不懂技术细节的人看**：
   - 不许出现编号（如 T012、P003、#123）、提交号、文件名、函数名、命令、英文缩写或术语（mypy、ruff、xfail、sha、hook、runner 之类）——
     一律换成白话，例如「代码类型检查」「自动检查脚本」「登录页」「后台同步程序」。
   - 数字只在它本身就是结论时才写，并说清是什么的数字（如「准确率只有三成」），不写一串指标。
   - why 写用户真正关心的目的（如「周一上线前必须修好，否则程序重启会出错」），不要复述 q。
   - res 写结果和现状：做成了 / 没做成为什么 / 等用户决定什么。
3. 若新增轮次仍属最后一件事且结论没变：changed=false，topics 原样返回（只把最后一件的 to 改成最新轮号）。
   否则 changed=true：更新已有事情的 res、开新的事情；已有事情的名字尽量不改。
4. 事情按时间顺序，轮号连续覆盖 1 到最新轮号。全部中文，只输出 JSON 本身。
6. title：18–26 字，格式「对象：做了什么、做了什么（现状）」，写**具体动作和产物**，以对话当前重心为准。
   禁用空词：改进、优化、需求、执行、处理、整理、讨论、相关、功能、问题。
   好例：「管理器：网页聊天入口、话题树、自动链接（已验收）」「#123：登录超时改为重试三次，单测全过」
   坏例：「对话管理器改进需求与HANDOFF执行」「代码优化」。
   对话主要围绕某个编号（如 #123、T012）就以它开头作「对象」，编号后加括号 3–8 字概括，如「T012(登录重试)：…」。
   现有标题仍贴切就原样返回。
7. next：只看**最后一轮 AI 的回复结尾**。在问用户问题、请用户决定/批准/验收、或说还有没做完的下一步 → "等你回复"；
   活已干完、明确收尾、没有任何要用户回的 → "可以关了"。拿不准就 "等你回复"。
8. summary：2–3 句白话。第一句说这个对话总体在做什么（接手交接的写「接手上一个对话的工作，…」）；
   第二句说最重要的发现或意外；第三句说现在卡在哪 / 等用户做什么。
   与第 2 条同样不许出现编号、文件名、英文缩写和术语；不写过程流水（「先看了…再看了…」）；总长 ≤ 120 字。每次都按最新进展重写。
"""


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def path_of(sid):
    return os.path.join(OUT_DIR, sid + ".json")


def load(sid):
    try:
        with io.open(path_of(sid), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _said(a, n):
    """回复里的人话(工具流水行去掉): 开头 1/3 + 结尾 2/3 —— 结论在最后; 只取开头时, 一个长轮次的标题会停在「读交接文件」(10-04 实测)。"""
    s = " ".join(l for l in (a or "").split("\n") if l and not l.startswith("· "))
    s = " ".join(s.split())
    if len(s) <= n:
        return s
    h = n // 3
    return s[:h] + " … " + s[-(n - h):]


def _turn_text(i, t, qn, an):
    """一轮 = 提问 + 运行中插的话(插问; 用户经常在一轮里连发好几件事, 这才是话题转移所在) + 回复。"""
    q = " ".join((t.get("q") or "").split())[:qn]
    mid = "".join("\n   【我·插问】" + " ".join(m.get("q", "").split())[:qn] for m in (t.get("mid") or []))
    a = _digest(t.get("a"), an)
    return "#%d【我】%s%s\n   【AI】%s" % (i, q, mid, a or "(只有工具调用)")


HANDOFF_BG_MAX = 1800       # 交接背景最多给模型多少字
GROW_CHARS = 4000           # 最后一轮(可能还在进行中)回复又长了这么多字, 就把它当新内容重写(10-04 用户「每次较长思考之后更新脉络」)


def handoff_background(turns):
    """对话第一句若是「读取 …HANDOFF_START_*.md 并照做」: 读出它指向的交接文件, 取任务 / 当前状态 / 下一步几节作背景。
    10-04 用户「话题脉络里没法理解这个对话在做什么」—— 接手对话的第一句只有一个路径, 模型看不到接的是什么活。"""
    import re
    q = (turns[0].get("q") or "") if turns else ""
    m = re.search(r"([A-Za-z]:[/\\][^\s\"'`]*HANDOFF_START_[^\s\"'`]*\.md)", q)
    if not m:
        return ""
    try:
        start = io.open(m.group(1), encoding="utf-8").read()
        m2 = re.search(r"([A-Za-z]:[/\\][^\s\"'`]*HANDOFF_\d{8}_\d{4}\.md)", start)
        body = io.open(m2.group(1), encoding="utf-8").read() if m2 else start
    except Exception:
        return ""
    head = body.split("\n", 1)[0].lstrip("# ").strip()
    keep = []
    for sec in re.split(r"\n(?=## )", body):
        if re.search(r"任务|目标|未决|下一步|当前状态", sec.split("\n", 1)[0]):
            keep.append(" ".join(sec.split()))
    bg = (head + "\n" + "\n".join(keep))[:HANDOFF_BG_MAX]
    return ("\n交接背景（本对话第一句是接手交接；以下摘自交接文件，用来理解「接的是什么活」，不是本对话自己做的事）：\n"
            + bg + "\n")


def _handoff_file(turns):
    """对话第一句指向的 HANDOFF_START → 它指向的交接文件路径(没有就 None)。"""
    import re
    q = (turns[0].get("q") or "") if turns else ""
    m = re.search(r"([A-Za-z]:[/\\][^\s\"'`]*HANDOFF_START_[^\s\"'`]*\.md)", q)
    if not m:
        return None
    try:
        start = io.open(m.group(1), encoding="utf-8").read()
    except Exception:
        return None
    m2 = re.search(r"([A-Za-z]:[/\\][^\s\"'`]*HANDOFF_\d{8}_\d{4}\.md)", start)
    return m2.group(1) if m2 else None


def handoff_story(turns):
    """接手对话的「来龙去脉」: 交接文件里那一小段白话, 原样返回(不经模型转述)。
    10-04 用户「handoff的时候，应该用一小段话讲一下来龙去脉，不然每次handoff我都很懵前面的语境是什么」。
    → {"text", "file", "from"}; 老交接文件没有这一节就退回「任务与目标」开头, from 写明。不是接手对话 → None。"""
    import re
    fp = _handoff_file(turns)
    if not fp:
        return None
    try:
        body = io.open(fp, encoding="utf-8").read()
    except Exception:
        return None
    secs = re.split(r"\n(?=## )", body)
    for want, cut in (("来龙去脉", 1200), ("任务", 500)):
        for sec in secs:
            head, _, rest = sec.partition("\n")
            if head.startswith("## ") and want in head:
                txt = rest.strip()
                if len(txt) > cut:
                    txt = txt[:cut] + " …"
                return {"text": txt, "file": fp.replace("\\", "/"),
                        "from": "" if want == "来龙去脉" else "这份交接文件没写「来龙去脉」, 下面摘自「任务与目标」"}
    return {"text": "", "file": fp.replace("\\", "/"), "from": "交接文件里没找到「来龙去脉」或「任务」一节"}


def _digest(a, n):
    """长回复的摘要: 去掉工具流水行后, 每段进展说明取第一句, 均匀抽样填满 n 字, 最后一段多给 ——
    一轮干几个小时时, 只取头尾会漏掉中间所有结论(10-04 实测: 一轮里十几项结论被概括成「读交接与规划」)。"""
    paras = [" ".join(l.split()) for l in (a or "").split("\n") if l.strip() and not l.startswith("· ")]
    s = " ".join(paras)
    if len(s) <= n:
        return s
    tail = paras[-1][:max(200, n // 4)] if paras else ""
    budget = n - len(tail) - 10
    firsts = []
    for p in paras[:-1]:
        cut = min([i for i in (p.find("。"), p.find("："), p.find("；")) if i > 0] or [len(p)])
        firsts.append(p[:min(cut + 1, 110)])
    if not firsts or budget <= 60:
        return _said(a, n)
    k = max(1, min(len(firsts), budget // 60))
    step = len(firsts) / k
    picked = [firsts[int(i * step)] for i in range(k)]
    return " / ".join(picked)[:budget] + " … " + tail


def _alen(t):
    return len(t.get("a") or "")


def build(turns, old, force):
    n = len(turns)
    bg = handoff_background(turns)
    if old and old.get("topics") and not all("res" in t for t in old["topics"]):
        force = True                                 # 旧格式(只有要点、没有问题/动机/结论三栏): 整份重写
    if old and not force and old.get("topics"):
        start = int(old.get("upto") or 0)
        if start >= n:                               # 没有新轮次, 但进行中的最后一轮又长了: 重写最后一轮
            start = n - 1
        oldtxt = u"现有标题：%s\n现有总结：%s\n现有脉络（已覆盖到第 %d 轮）：\n%s" % (
            old.get("title") or "（无）", old.get("summary") or "（无）", start, json.dumps(old["topics"], ensure_ascii=False))
        newtxt = u"新增轮次（第 %d–%d 轮；最后一轮可能还在进行中）：\n" % (start + 1, n) + "\n".join(
            _turn_text(i + 1, turns[i], 500, 2400) for i in range(start, n))
    else:
        oldtxt = u"现有脉络：无（第一次生成，请从头整理）。"
        per = max(200, min(2400, CORPUS_MAX // max(1, n)))       # 轮次少时每轮多给(长轮次的结论在中间)
        lines = [_turn_text(i + 1, t, 300, per) for i, t in enumerate(turns)]
        if sum(map(len, lines)) > CORPUS_MAX:          # 太长: 开头 6 轮与最后 10 轮给全, 中间只留提问开头
            lines = [(_turn_text(i + 1, t, 300, 200) if i < 6 or i >= n - 10 else
                      "#%d【我】%s" % (i + 1, " ".join((t.get("q") or "").split())[:90])) for i, t in enumerate(turns)]
        newtxt = u"全部轮次（共 %d 轮）：\n" % n + "\n".join(lines)
    return PROMPT.format(old=oldtxt + bg, new=newtxt[:CORPUS_MAX * 2]) + gloss_block(oldtxt + newtxt)


def id_titles(text):
    """语料里出现的编号 → 它的标题(按 config.json 的 xref 规则里带 hover 的那些去本机接口查; 没配就空)。
    10-04 用户「标题上的qxxx(也应该加括号几个字说明是关于什么的)」: 交给模型当释义, 让它写成「T012(登录重试)」这样。"""
    import re
    out = {}
    for r in config._cfg.get("xref", []):
        if not r.get("hover") or not r.get("re"):
            continue
        try:
            rx = re.compile(r["re"])
        except re.error:
            continue
        ids = []
        for m in rx.finditer(text):
            if m.group(0) not in ids:
                ids.append(m.group(0))
        for k in ids[:25]:
            try:
                import server
                x = server.xref_fetch(r["hover"].replace("{0}", k)) or {}
            except Exception:
                x = {}
            t = x.get("title") or x.get("metric") or ""
            if t:
                out[k] = " ".join(str(t).split())[:60]
    return out


def gloss_block(text):
    T = id_titles(text)
    if not T:
        return ""
    return ("\n编号释义（标题和要点里出现编号时，一律在编号后加括号写 3–8 字概括，如「T012(登录重试)」，不许光写编号）：\n"
            + "\n".join("%s = %s" % kv for kv in T.items()))


def bare_ids(title):
    """标题里没跟括号概括的编号(字母 + 3–4 位数字, 后面不是「(」或「（」)。"""
    import re
    return re.findall(r"(?<![A-Za-z0-9])[QPC]\d{3,4}(?![0-9(（])", title or "")      # 不用 : 汉字也算单词字符, Q027完成 之间没有词边界(10-04 漏过 2 个)


def run_model(prompt):
    import advisor                                  # 复用它的括号配平 JSON 解析
    # --setting-sources project,local: 不加载用户级 settings —— 一次性会话不跑用户钩子(10-04 实测单次 8.5–9 s → 3.7–4.1 s,
    # 且不再给它起 VS Code 标签、导出存档、触发脉络); 登录(OAuth)不受影响
    cmd = ["claude", "-p", "--setting-sources", "project,local", "--model", MODEL,
           "--strict-mcp-config", "--mcp-config", os.path.join(TITLER, "empty_mcp.json"),
           "--settings", os.path.join(TITLER, "empty_settings.json"),
           "--system-prompt", SYSP]
    # cwd 钉在 titler/: 这次一次性会话落在已被 IGNORE_PROJ 排掉的 slug 下; hook_state 也据此不再给它生成脉络(防自激)
    r = subprocess.run(cmd, input=prompt.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=TIMEOUT, cwd=TITLER, creationflags=NO_WINDOW)
    raw = r.stdout.decode("utf-8", "replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[:300] or "claude 退出码 %d" % r.returncode)
    import re
    frag = advisor._balanced(re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip(), flags=re.M))
    d = None
    for cand in (frag, advisor._repair(frag) if frag else ""):
        try:
            d = json.loads(cand); break
        except Exception:
            continue
    if not isinstance(d, dict) or not isinstance(d.get("topics"), list):
        raise RuntimeError("模型没给出可解析的 JSON: " + " ".join(raw.split())[:200])
    return d


def clean_topics(ts, n):
    out = []
    for t in ts:
        if not isinstance(t, dict) or not str(t.get("t") or "").strip():
            continue
        try:
            a, b = int(t.get("from") or 1), int(t.get("to") or n)
        except (TypeError, ValueError):
            a, b = 1, n
        rec = {"t": str(t["t"]).strip()[:30], "from": max(1, min(a, n)), "to": max(1, min(b, n))}
        for k in ("q", "why", "res"):                      # 10-04 用户「问题是什么，动机是什么，结论是什么」
            v = " ".join(str(t.get(k) or "").split())[:90]
            if v:
                rec[k] = v
        pts = [str(p).strip()[:60] for p in (t.get("pts") or []) if str(p).strip()][:4]
        if pts:
            rec["pts"] = pts
        out.append(rec)
    return out


def update(sid, force=False):
    """→ (rec, 说明)。没有新轮次直接返回旧的。"""
    import server
    os.makedirs(OUT_DIR, exist_ok=True)
    tree = server.conv_tree(sid)
    if not tree or not tree.get("turns"):
        return None, "没有可整理的轮次"
    turns = tree["turns"]
    n = len(turns)
    old = load(sid)
    old_fmt = bool(old and old.get("topics") and not all("res" in t for t in old["topics"]))
    if old and not force and not old_fmt and int(old.get("upto") or 0) >= n and _alen(turns[-1]) - int(old.get("alen") or 0) < GROW_CHARS:
        return old, "没有新轮次"
    lock = path_of(sid) + ".lock"
    try:
        if os.path.exists(lock) and time.time() - os.path.getmtime(lock) > 180:
            os.remove(lock)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except OSError:
        return old, "已有一个在生成"
    try:
        t0 = time.time()
        prompt = build(turns, old, force)
        d = run_model(prompt)
        if bare_ids(d.get("title")):                # 光秃编号: 带着反馈重来一次
            d2 = run_model(prompt + "\n上次给的标题「%s」里 %s 后面没加括号概括，按规则改正后重新输出整个 JSON。" % (d.get("title"), "、".join(bare_ids(d.get("title")))))
            if d2.get("title") and not bare_ids(d2.get("title")):
                d = d2
        topics = clean_topics(d["topics"], n) or (old or {}).get("topics") or []
        title = " ".join(str(d.get("title") or "").split())[:40] or (old or {}).get("title", "")
        nxt = "可以关了" if str(d.get("next") or "").strip() == "可以关了" else "等你回复"
        summary = " ".join(str(d.get("summary") or "").split())[:240] or (old or {}).get("summary", "")
        rec = {"sid": sid, "upto": n, "alen": _alen(turns[-1]), "summary": summary,
               "topics": topics, "title": title, "next": nxt, "changed": bool(d.get("changed", True)),
               "ts": _now(), "ms": int((time.time() - t0) * 1000), "model": MODEL}
        tmp = path_of(sid) + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False)
        os.replace(tmp, path_of(sid))
        try:
            lab = push_label(sid, title)
        except Exception as e:                      # noqa: BLE001
            lab = "写标签失败 %s" % e
        return rec, ("已更新" if rec["changed"] else "话题没变, 只挪水位") + (" · 标签: " + lab if lab else "")
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def push_label(sid, title):
    """把标题写成 VS Code 标签正文(10-04 用户「vs控制台和对话管理器、任务台里面的标题要统一」)。
    唯一来源 = ~/.claude/tab_labels.json; 对话管理器与其他看板都读它。保留末尾「[版本, 会话名(id8)]」。
    你在对话管理器里手填过标题(notes.json 的 title)就用手填的, 脉络不覆盖。没有 tab_title 模块(公开版没装标签命名)就跳过。
    → 写入的标签或 ""。"""
    sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "scripts"))
    try:
        import tab_title
    except Exception:
        return ""
    try:
        with io.open(os.path.join(HERE, "notes.json"), encoding="utf-8") as fh:
            manual = ((json.load(fh).get(sid) or {}).get("title") or "").strip()
    except Exception:
        manual = ""
    body = manual or title
    if not body:
        return ""
    import re
    reg = tab_title.load()
    cur = reg.get(sid) or ""
    cur = (cur.get("label") or "") if isinstance(cur, dict) else cur
    m = re.search(r"\[[^\[\]]*\]\s*$", cur)
    tag = m.group(0).strip() if m else ""
    if not tag:
        s = tab_title.session(sid)
        tag = "[%s(%s)]" % ((s or {}).get("name") or "?", sid[:8])
    new = "%s %s" % (body, tag)
    if cur.replace(tab_title.MARK, "").strip() == new:
        return new
    if tab_title.session(sid):
        tab_title.apply(sid, label=new)         # 登记 + 立刻改控制台标题(零闪动)
    else:
        (tab_title.set_label(sid, new) if hasattr(tab_title, "set_label") else tab_title.save(dict(tab_title.load(), **{sid: new})))
    return new


def spawn(sid):
    """无窗口后台拉起(钩子与服务端用): pythonw + DETACHED_PROCESS | CREATE_NO_WINDOW。"""
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    subprocess.Popen([w if os.path.exists(w) else exe, os.path.abspath(__file__), sid],
                     cwd=HERE, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | NO_WINDOW,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        rec, why = update(sid, "--force" in sys.argv)
        msg = "%s %s %s" % (_now(), sid[:8], why)
    except Exception as e:                          # noqa: BLE001
        msg = "%s %s 失败: %s" % (_now(), sid[:8], str(e)[:300])
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with io.open(os.path.join(OUT_DIR, "_log.txt"), "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass
    if sys.stdout:
        try:
            print(msg)
        except Exception:
            pass
