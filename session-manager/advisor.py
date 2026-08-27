# -*- coding: utf-8 -*-
"""「建议」—— 对一个已经跑长了的对话做复盘: 讲了什么 / 初衷是什么 / 达到没有 / 接着做什么。

为什么单独一个模块而不是塞进 titler:
  · 标题只看**用户发言**就够(标题是索引)。复盘必须看**双方**, 否则判不了"目的达到没有"。
  · 标题用 haiku(20 秒), 复盘要读几万字并下判断, 模型档次不同。

语料构造(写死, 改了要同步改 README):
  · 开头 HEAD 轮给全的 —— **初衷只在这里**, 后面几轮的收敛不是初衷
  · 最近 TAIL 轮给全的 —— "现在卡在哪 / 接着做什么"只在这里
  · 中间每轮只留我的提问前 MID_Q 字 —— 保住脉络, 不付全文的钱
  · 工具流水(`· Bash …`)整行丢掉 —— 几百行 Bash 会把真正的判断句挤出窗口

⚠ 每次调用都会起一个 `claude -p` 子进程, 会触发 user-level 的 Stop hook(响铃/归档),
  并在 <titler>/ 的 project slug 下留一个一次性会话(已在 IGNORE_PROJ 里排掉)。
"""
import io
import json
import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

HERE = os.path.dirname(os.path.abspath(__file__))
TITLER = os.path.join(HERE, "titler")
CACHE = os.path.join(HERE, "advice.jsonl")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 复盘要读几万字并下判断, haiku 会糊成"你们讨论了一些问题"。默认 sonnet。
MODEL = (os.environ.get("SESSIONS_ADVICE_MODEL")
         or getattr(config, "_cfg", {}).get("advice_model") or "sonnet")

HEAD = 8            # 开头留几轮全文(定"初衷")
TAIL = 20           # 结尾留几轮全文(定"现状")
MID_Q = 80          # 中间轮次只留提问的前多少字
Q_CHARS = 700       # 单轮提问最多给多少字
A_CHARS = 900       # 单轮回复最多给多少字
TIMEOUT = 300

SYSP = ("你是一个对话复盘助手。只输出一个 JSON 对象，不做任何其他事，不使用任何工具。")

PROMPT = u"""下面是一个 Claude Code 对话的记录（【我】= 用户，【AI】= 助手，均已截断，
工具调用流水已剔除）。请复盘它，输出**一个 JSON 对象**，恰好四个字段：

{
  "topic":  "这个对话都干了什么 —— 2-4 句，按时间顺序，点名具体的文件/工具/数字/结论",
  "goal":   "用户最开始想达成什么 —— 1-2 句。以**最早几轮**为准，不要把后来的收敛当初衷",
  "status": "初衷达到了没有 —— 开头先给一个明确判词：已达成 / 部分达成 / 未达成 / 已转向，再用 1-3 句摆证据；没达成就说卡在哪一步",
  "next":   ["现在建议做什么 —— 3-5 条，每条一句，具体到可执行的下一步动作，按优先级排。若对话已跑偏，第一条说怎么拉回初衷"]
}

硬要求：
1. 只输出 JSON 本身，不要 ``` 包裹、不要任何解释文字。
2. 全部中文。禁止"关于/相关/进一步/深入探讨"这类零信息量的词。
3. **有证据才写**：能引用记录里出现过的具体数字、文件名、结论就引用。
4. 记录里没写的事，明说"记录里没写"，**不要编**。判断不了"达到没有"就写"判断不了，因为…"。
5. "next" 是数组，每个元素是一条建议，别写成一整段。

对话记录：
"""

_write_lock = threading.Lock()


def build_corpus(turns):
    """把 conv_tree 的轮次压成喂给模型的文本。返回 (文本, 实际给了几轮全文)。"""
    n = len(turns)
    out = []
    full = 0
    for i, t in enumerate(turns):
        q = (t.get("q") or "").strip()
        a = "\n".join(l for l in (t.get("a") or "").split("\n")
                      if l and not l.startswith(u"· ")).strip()
        is_full = i < HEAD or i >= n - TAIL
        if is_full:
            full += 1
            out.append(u"#%d 【我】%s" % (i + 1, q[:Q_CHARS]))
            if a:
                out.append(u"   【AI】%s" % a[:A_CHARS])
            elif t.get("tools"):
                out.append(u"   【AI】(只跑了 %d 次工具, 没有文字回复)" % t["tools"])
        else:
            out.append(u"#%d 【我】%s" % (i + 1, q[:MID_Q]))
    if n > HEAD + TAIL:
        out.insert(HEAD, u"…(中间 %d 轮只给了提问, 没给回复)…" % (n - HEAD - TAIL))
    return "\n".join(out), full


def _balanced(s):
    """从第一个 { 起取一个括号配平的片段(忽略字符串里的括号)。

    rfind("}") 不够: 模型偶尔会在 JSON 之后再补一段话, 里面带 } 就把尾巴吃进来了。
    """
    i = s.find("{")
    if i < 0:
        return ""
    depth = 0
    instr = False
    esc = False
    for j in range(i, len(s)):
        c = s[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
            continue
        if c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i:j + 1]
    return s[i:]          # 被截断了, 交给下面的修补去试


def _repair(s):
    """两种见过的模型手抖: 字符串里塞了裸换行 / 输出被截断少了收尾括号。

    括号用栈补, 不能只数 { —— 截断最常发生在 "next": [ 那个数组里。
    """
    out = []
    stack = []
    instr = esc = False
    for c in s:
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
            elif c in "\r\n":
                out.append("\\n")     # 裸换行 -> 转义, 否则 json.loads 直接拒收
                continue
            elif c == "\t":
                out.append("\\t")
                continue
        else:
            if c == '"':
                instr = True
            elif c in "{[":
                stack.append(c)
            elif c in "}]":
                if stack:
                    stack.pop()
        out.append(c)
    if instr:
        out.append('"')
    # 截在 `"next": ["a",` 这种地方时, 尾逗号也得去掉
    tail = "".join(out).rstrip()
    if tail.endswith(","):
        tail = tail[:-1]
    return tail + "".join("}" if c == "{" else "]" for c in reversed(stack))


def _parse(raw):
    """模型偶尔会裹 ```、在 JSON 前后加话、字符串里塞裸换行。三层容错。"""
    s = (raw or "").strip()
    s = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", s, flags=re.M).strip()
    frag = _balanced(s)
    if not frag:
        return None
    d = None
    for cand in (frag, _repair(frag)):
        try:
            d = json.loads(cand)
            break
        except Exception:
            continue
    if not isinstance(d, dict):
        return None
    nxt = d.get("next")
    if isinstance(nxt, str):
        nxt = [x.strip(" -·") for x in nxt.split("\n") if x.strip()]
    d["next"] = [str(x) for x in (nxt or [])][:8]
    for k in ("topic", "goal", "status"):
        d[k] = str(d.get(k) or "").strip()
    return d if (d["topic"] or d["status"]) else None


RAW_DUMP = os.path.join(HERE, "advice_last_raw.txt")


def _run_once(corpus, timeout):
    cmd = ["claude", "-p", "--model", MODEL,
           "--strict-mcp-config", "--mcp-config", os.path.join(TITLER, "empty_mcp.json"),
           "--settings", os.path.join(TITLER, "empty_settings.json"),
           "--system-prompt", SYSP]
    try:
        # cwd 钉在 titler/ —— 这样这些一次性会话落在已被 IGNORE_PROJ 排掉的 slug 下,
        # 不会混进用户自己的对话列表里。
        r = subprocess.run(cmd, input=(PROMPT + corpus).encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, cwd=TITLER, creationflags=NO_WINDOW)
    except Exception as e:
        return None, str(e)[:300], ""
    raw = r.stdout.decode("utf-8", "replace")
    if r.returncode != 0:
        return None, (r.stderr.decode("utf-8", "replace")[:300]
                      or "claude 退出码 %d" % r.returncode), raw
    return _parse(raw), "", raw


def run_model(corpus, timeout=TIMEOUT):
    """跑模型, 解析失败就再来一次 —— 输出是随机的, 一次手抖不该让整个按钮失败。

    失败时把原始输出落到 advice_last_raw.txt, 否则"模型没给出 JSON"这句话
    没法查证到底是模型的问题还是解析器的问题。
    """
    last_err = last_raw = ""
    for attempt in (1, 2):
        d, err, raw = _run_once(corpus, timeout)
        if d is not None:
            return d, ""
        last_err, last_raw = err, raw
        if err and not raw:
            break                       # 起不来/超时, 重试也没用
    try:
        io.open(RAW_DUMP, "w", encoding="utf-8").write(last_raw or "(没有 stdout)")
    except Exception:
        pass
    if last_err:
        return None, last_err
    return None, ("模型两次都没给出可解析的 JSON, 原始输出已存到 %s: %s"
                  % (os.path.basename(RAW_DUMP), " ".join((last_raw or "").split())[:200]))


def load_cache():
    """sid -> 最后一次的复盘记录。追加写, 后写的赢。"""
    out = {}
    try:
        with io.open(CACHE, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("sid"):
                    out[d["sid"]] = d
    except Exception:
        pass
    return out


def cached(sid):
    return load_cache().get(sid)


def hist(n=12):
    """最近 n 次真实生成的 (轮数, 秒), 给前端拟"这次大概要多久"。

    耗时随对话长短差一倍(实测 3 轮 18s / 71 轮 34s), 所以估时必须带上轮数这个自变量;
    只存一个平均值对长短两头都是错的。从这里回传, 换个浏览器也不用从零重学。
    """
    rows = []
    try:
        with io.open(CACHE, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("ms") and d.get("turns"):
                    rows.append([d["turns"], round(d["ms"] / 1000.0)])
    except Exception:
        pass
    return rows[-n:]


def advise(sid, tree):
    """tree = server.conv_tree(sid) 的返回值。生成一份复盘并落盘。"""
    t0 = time.time()
    turns = (tree or {}).get("turns") or []
    if not turns:
        return {"ok": False, "error": "这个对话里没有可用的发言"}
    corpus, full = build_corpus(turns)
    d, err = run_model(corpus)
    ms = int((time.time() - t0) * 1000)
    if d is None:
        return {"ok": False, "error": err, "ms": ms}
    rec = {"sid": sid, "ms": ms, "model": MODEL,
           # 存下"基于多少轮生成的" —— 对话还在跑时, 前端要能说清这份建议有多旧
           "turns": tree.get("total") or len(turns),
           "full_turns": full, "chars": len(corpus),
           "at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "advice": d}
    with _write_lock:
        with io.open(CACHE, "a", encoding="utf-8") as w:
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
    rec["ok"] = True
    return rec


if __name__ == "__main__":
    import server                                   # 只在命令行用, 避免循环导入
    sid = sys.argv[1]
    print(json.dumps(advise(sid, server.conv_tree(sid)), ensure_ascii=False, indent=1))
