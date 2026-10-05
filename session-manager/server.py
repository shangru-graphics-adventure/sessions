# -*- coding: utf-8 -*-
"""Claude 对话管理器 — localhost:8720

扫描 ~/.claude/projects/*/*.jsonl(每个文件 = 一次对话), 按最后更新时间列出,
支持: 自定义标题 / 自由注释 / 全文搜索 / 一键在新 CMD 窗口 resume。

启动:  python server.py        然后开 http://localhost:8720/
注释与自定义标题存 notes.json(与本文件同目录), 与 Claude Code 本身完全解耦,
删掉也只是丢注释, 不影响对话本身。
"""
import os
import sys
import re
import io
import json
import time
import shutil
import threading
import subprocess
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import config                             # 本机配置(端口/路径), 见 config.py
import replay                             # 完整回放(公开版, 2026-09-09 加)
import advisor                            # 「建议」的复盘生成, 见 advisor.py
import actions                            # 窗口定位与按键注入
from preview import preview_html, reveal   # 产物预览/定位, 见 preview.py

import utf8_console
utf8_console.enable()

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = config.PROJECTS_DIR
NOTES_PATH = os.path.join(HERE, "notes.json")
AUTO_TITLES_PATH = os.path.join(HERE, "titler", "titles.jsonl")

# 生成 auto_title 的批处理自己也是几千次 `claude -p` 调用, 每次都会在 projects/ 下
# 落一个会话文件。它们不是真对话, 必须从列表与全文搜索里排除, 否则噪音比正文还多。
IGNORE_PROJ = {config.project_slug(os.path.join(HERE, "titler"))}
# 本工具自己喂给 `claude -p` 的提示开头 —— 用来认出并藏掉它自己产生的一次性会话
SELF_PROMPT_HEAD = "下面是一个 Claude Code 对话"
CACHE_PATH = os.path.join(HERE, "cache.json")
STATE_DIR = os.path.join(HERE, "state")     # hook_state.py 每会话写一个
try:                                        # 在跑/等你 与 VS Code 标签 ▶ 同源 + 事件推送(~/.claude/scripts/turn_push.py, 与本机其他看板共用; 用户 10-04)
    sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "scripts"))
    import turn_push
except Exception:                           # 公开版 / 别的机器没有它: 状态照旧, 前端退回 2 s 轮询
    turn_push = None
PORT = config.PORT

MAX_TOPICS = 14           # 每个对话最多提取多少个"话题"
TOPIC_CHARS = 46          # 每个话题截多长
MAX_ARTIFACTS = 40        # 每个对话最多列多少个产物(超出只报数)
WHY_CHARS = 78            # "这文件怎么来的"截多长
SCAN_VER = 6              # (10-04 → 5: 话题跳过 isMeta 注入) 解析格式版本, 改了就让磁盘缓存整体失效重扫

# 产物过滤 —— 目标是"这次对话到底交付了什么", 不是"碰过哪些字节"
ART_SKIP_DIR = (
    "\.claude\\",          # 记忆/配置/skills/对话记录本身, 不是交付物
    "\__pycache__\\", "\node_modules\\", "\.git\\",
    "\site-packages\\", "\scratchpad\\",
    "\appdata\local\temp\\", "\_docs\chat\\",
)
ART_SKIP_EXT = (".log", ".tmp", ".bak", ".lock", ".pyc", ".swp")
# 大块中间数据: 记名, 但前端弱化显示, 不让它们淹掉真正的报告
ART_DATA_EXT = (".npz", ".npy", ".parquet", ".pkl", ".pickle", ".h5",
                ".db", ".sqlite", ".feather", ".arrow")
ART_DOC_EXT = (".md", ".csv", ".html", ".htm", ".txt", ".json",
               ".yaml", ".yml", ".tsv")
ART_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "Artifact")
# 「真实活动时刻」用: 只有 user/assistant 行才是对话本身。文件 mtime 会被
# artifact-autoreact-ledger / atis-latch / last-prompt / frame-link 这类**记账行**
# 顶上来 —— 一个还开着但几天没说话的窗口, 每隔几分钟就被这些行刷新 mtime,
# 于是按 mtime 排序会把几天前的对话顶到最上面(2026-09-09 实发)。
# 实测(68k 行, 68 个文件): 这两个子串与 json 解出来的 type 一一对应, 零假阳零假阴。
TS_RE = re.compile(r'"timestamp":"([^"]+)"')

PUB_RE = re.compile(r"https://claude\.ai/(?:code/artifact|public/artifacts)/[0-9a-fA-F]{8}-[0-9a-fA-F-]{20,}")

_scan_cache = {}          # path -> [key, parsed dict]
_cache_dirty = False
_notes_lock = threading.Lock()

# 这些是壳/注入, 不是用户真的在说的事
JUNK_PREFIX = (
    "Base directory for this skill",
    "Caveat: The messages below",
    "This session is being continued",
    "Analysis:",
    "[Request interrupted",
    # skill 往对话里注入的指令壳 —— 长得像真人发言(不以 < 开头), 但一个字都不是
    # 用户说的。不过滤掉, 产物的"来历"会全变成这句设计腔。
    "Approach this as the design lead",
    "Draw as the engineer who has to live",
    "You are an interactive agent",
    "<command-name>", "<local-command",
    "The user opened the file",
    "Your task is to create",
)
# 纯确认词, 不构成一个"话题"
FILLER = {
    "继续", "继续吧", "好", "好的", "好啊", "行", "可以", "嗯", "是", "对", "没错",
    "确认", "谢谢", "算了", "ok", "okay", "yes", "y", "n", "no", "go", "同意",
    "继续做", "接着", "然后呢", "嗯嗯", "对的", "是的", "不用", "不要",
}


# ---------------------------------------------------------------- 兜底标题(用户 10-04)
# 用户:「claude对话管理器仍然有很多"请读取"这种标题，没法一眼看出这个对话的目的」。
# 没手填标题、也没 haiku 标题时, 以前直接把第一句当占位 —— 交接/拉起的对话第一句都是「请读取 X.md 并严格照其中的步骤做」。
# 现在依次取: VS Code 标签名(~/.claude/tab_labels.json) → 交接链话题(config.json 的 "chain_meta", 可不配)
#            → 「请读取 X.md」里 X 的标题(交接文件取 tabname.handoff_title, 其余取 H1) → 空(页面再退回第一句)。
_LB = {"files": {}, "md": {}}
_TAB_LABELS = os.path.join(os.path.expanduser("~"), ".claude", "tab_labels.json")
_QA_META = config._cfg.get("chain_meta") or ""      # 别的看板记的交接链 {chains:{…}}; 没配就跳过这一级
_GENERIC = {"claude", "claude code", "claude handoff", ""}
_READ_RE = re.compile(r"请读取\s*`?([A-Za-z]:[^\s`\"']+?\.md)")


def _json_cached(path):
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {}
    c = _LB["files"].get(path)
    if c and c[0] == mt:
        return c[1]
    try:
        with io.open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        d = {}
    _LB["files"][path] = (mt, d)
    return d


def _md_title(path):
    if path in _LB["md"]:
        return _LB["md"][path]
    t = ""
    if "HANDOFF_START_" in path:
        try:
            sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "skills", "handoff"))
            import tabname
            h = tabname.handoff_title("请读取 " + path)
            if len(h) < 4:                                   # H1 只写了时间(「交接 2026-10-02 12:45 ET」): 改用第 6 节第一条下一步
                h = tabname.handoff_next("请读取 " + path)[:28] or h
            t = "接手: " + h if h else ""
        except Exception:
            t = ""
    if not t:
        try:
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                h1 = next((l for l in fh if l.startswith("# ")), "")
            t = re.sub(r"[（(][^）)]*[）)]", "", h1[2:]).strip()
        except Exception:
            t = ""
    t = t or os.path.splitext(os.path.basename(path))[0]
    _LB["md"][path] = t
    return t


def smart_label(sid, first):
    v = _json_cached(_TAB_LABELS).get(sid)
    v = (v.get("label") or "") if isinstance(v, dict) else (v or "")
    v = re.sub(r"\s*\[[^\[\]]*\]\s*$", "", v.replace("\u25b6", "").strip()).strip()
    if v.lower() not in _GENERIC:
        return v
    for c in ((_json_cached(_QA_META) if _QA_META else {}).get("chains") or {}).values():
        for m in c.get("members") or []:
            if m.get("sid") == sid and c.get("topic"):
                return "%s v%s" % (c["topic"], m.get("ver"))
    m = _READ_RE.search(first or "")
    return _md_title(m.group(1)) if m else ""


def outline_title(sid):
    """话题脉络顺带生成的具体标题(10-04 用户「现有的标题还是太过模糊笼统…提及说具体干了什么，20字左右」)。"""
    return (_json_cached(os.path.join(STATE_DIR, "outline", sid + ".json")) or {}).get("title") or ""


def tab_tag(sid):
    """cmd/VS Code 标签名末尾的方括号「[1-最新, 会话名(id8)]」; 没有标签就给 id 前 8 位(用户「把1-最新，id什么的接在后面，如同cmd标题」)。"""
    v = _json_cached(_TAB_LABELS).get(sid)
    v = (v.get("label") or "") if isinstance(v, dict) else (v or "")
    m = re.search(r"\[([^\[\]]*)\]\s*$", v)
    return m.group(1) if m else sid[:8]


# ---------------------------------------------------------------- notes 持久化

def load_notes():
    try:
        with io.open(NOTES_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


_AT_CACHE = {"mtime": None, "map": {}}


def load_auto_titles():
    """haiku 批量生成的标题(titler/run_titles.py 产出), sid -> title。

    是"兜底显示 + 可被搜索"的一层, 永远不覆盖 notes.json 里手填的 title。
    追加写的 jsonl, 同一 sid 后写的赢(重跑某条时不必清空文件)。
    """
    try:
        mt = os.path.getmtime(AUTO_TITLES_PATH)
    except OSError:
        return {}
    if _AT_CACHE["mtime"] == mt:
        return _AT_CACHE["map"]
    m = {}
    try:
        with io.open(AUTO_TITLES_PATH, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                t = (d.get("title") or "").strip()
                if d.get("sid") and t:
                    m[d["sid"]] = t
    except Exception:
        return _AT_CACHE["map"]
    _AT_CACHE["mtime"], _AT_CACHE["map"] = mt, m
    return m


def save_notes(notes):
    tmp = NOTES_PATH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(notes, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, NOTES_PATH)


# ---------------------------------------------------------------- jsonl 解析

_CMD_RE = re.compile(r"<command-name>/?([^<]+)</command-name>.*?<command-args>(.*?)</command-args>", re.S)


def _slash(t):
    """带参数的斜杠命令(/goal 正文、/loop 5m …)是用户真说的话 —— 还原成「/goal 正文」(10-04 用户:「这里显示没有我的发言，其实有，只是设定了goal」)。
    不带参数的(/clear、/compact)仍是命令壳, 原样返回(以 < 开头, 会被当成非人类输入)。"""
    if t.startswith("<command-"):
        m = _CMD_RE.search(t)
        if m and m.group(2).strip():
            return "/%s %s" % (m.group(1).strip(), m.group(2).strip())
    return t


def _text_of(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return _slash(c.lstrip())
    if isinstance(c, list):
        return _slash("".join(b.get("text", "") for b in c
                              if isinstance(b, dict) and b.get("type") == "text").lstrip())
    return ""


def _is_real_user_text(t):
    """排除 system-reminder / 斜杠命令壳 / 中断标记这类非人类输入。"""
    t = t.strip()
    if not t:
        return False
    if t.startswith("<"):
        return False
    if t.startswith("[Request interrupted"):
        return False
    return True


def _clean(t, n=200):
    return " ".join(t.split())[:n]


def _is_filler(s):
    t = s.strip().strip("。.!?！?~ ").lower()
    return t in FILLER


def _art_keep(fp):
    """这个路径算不算"这次对话的产物"。"""
    if not fp or len(fp) < 4:
        return False
    lp = fp.replace("/", "\\").lower()
    if not lp.endswith(tuple()) and any(sk in lp for sk in ART_SKIP_DIR):
        return False
    if os.path.splitext(lp)[1] in ART_SKIP_EXT:
        return False
    return True


def _art_kind(fp):
    e = os.path.splitext(fp)[1].lower()
    if e in ART_DATA_EXT:
        return "data"
    if e in ART_DOC_EXT:
        return "doc"
    return "code"


def pick_artifacts(arts):
    """按对话内首次写入时间排序; 超过上限只保留最早的一批(开局产物信息量最大)。"""
    rows = sorted(arts.values(), key=lambda a: (a["t"] or 0))
    for a in rows:
        a["kind"] = _art_kind(a["p"])
    return rows[:MAX_ARTIFACTS], len(rows)


def pick_topics(msgs):
    """把一串真人发言压成"这个对话里都讲了哪几件事"。

    一次对话经常横跨好几个主题, 只看首/末两条会漏掉中间的。这里保留每一条
    有实质内容的发言开头, 丢掉"继续/好的"这类确认词和与上一条重复的追问。
    """
    out = []
    for t in msgs:
        s = t.strip()
        if len(s) < 3 or _is_filler(s):
            continue
        if s.startswith(JUNK_PREFIX):
            continue
        head = s[:TOPIC_CHARS]
        # 和已有话题开头重合的算同一件事(常见于"再改一下xxx"这类连续追问)
        if any(head[:14] == o[:14] for o in out):
            continue
        out.append(head)
    if len(out) <= MAX_TOPICS:
        return out
    # 长对话(几十上百条发言)不能只留开头 —— 那样后半段讲的事全看不见。
    # 首尾各留一半, 中间折叠成一条计数, 保证"这个对话最后在干嘛"始终可见。
    half = MAX_TOPICS // 2
    return out[:half] + ["…(中间还有 %d 条发言)…" % (len(out) - half * 2)] + out[-half:]


def scan_file(path):
    """返回 {first,last,cwd,turns,topics}。按 (mtime,size) 缓存, 文件没变就不重解析。

    全文件扫一遍才能拿到"中间讲了什么"; 为了不被工具结果拖垮, 对每行先做纯字符串
    预筛, 只有像"真人发言"的行才付出 json.loads 的代价。
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = [st.st_mtime, st.st_size, SCAN_VER]
    hit = _scan_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]

    cwd = ""
    msgs = []
    arts = {}          # 本地文件产物: path -> 记录
    pub = {}           # 已发布的 claude.ai artifact: url -> 记录
    last_user = ""     # 最近一条真人发言 —— 就是下一个产物的"来历"
    act_raw = ""       # 最后一条 user/assistant 行的时间戳 = 真实活动时刻
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                # 真实活动时刻: 只认对话行, 不认记账行。纯字符串 + 一次正则,
                # 不付 json.loads 的钱(它在这个循环里是最贵的一步)。
                if '"type":"assistant"' in line or '"type":"user"' in line:
                    m = TS_RE.search(line)
                    if m:
                        act_raw = m.group(1)
                # 三种行才值得付 json.loads 的钱: 真人发言 / 写文件的工具调用 /
                # 含已发布 artifact 链接的行。其余(工具结果、思考块)直接跳过。
                is_user = ('"type":"user"' in line and '"tool_use_id"' not in line)
                has_file = ('"file_path"' in line and '"tool_use"' in line)
                has_pub = "claude.ai/code/artifact/" in line or                           "claude.ai/public/artifacts/" in line
                if not (is_user or has_file or has_pub):
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue

                if is_user and d.get("type") == "user":
                    if not cwd:
                        cwd = d.get("cwd") or ""
                    t = _text_of(d.get("message", {}))
                    # 10-04: isMeta = 系统注入(skill 正文、别的对话带话、Stop hook、额度重置续跑、跨对话通知), 不是我说的话
                    if _is_real_user_text(t) and not d.get("isMeta"):
                        t = " ".join(t.split())
                        msgs.append(t)
                        # 壳文本不能当"来历" —— 否则每个产物的解释都变成同一句
                        # skill 注入语(实测: 8 个已发布 artifact 有 7 个中招)
                        if not t.startswith(JUNK_PREFIX) and not _is_filler(t):
                            last_user = t
                    continue

                ts = _iso_epoch(d.get("timestamp")) or 0

                if has_pub:
                    for u in PUB_RE.findall(line):
                        u = u.split("?")[0]
                        r = pub.get(u)
                        if r is None:
                            pub[u] = {"u": u, "t": ts, "why": last_user[:WHY_CHARS]}

                if has_file and d.get("type") == "assistant":
                    for blk in (d.get("message") or {}).get("content") or []:
                        if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                            continue
                        if blk.get("name") not in ART_TOOLS:
                            continue
                        fp = (blk.get("input") or {}).get("file_path")
                        if not isinstance(fp, str) or not _art_keep(fp):
                            continue
                        r = arts.get(fp)
                        if r is None:
                            # 第一次写入才记来历 —— 后续 Edit 是修补, 不是"怎么来的"
                            arts[fp] = {"p": fp, "t": ts, "n": 1,
                                        "why": last_user[:WHY_CHARS]}
                        else:
                            r["n"] += 1
    except Exception:
        pass

    topics = pick_topics(msgs)
    art_rows, art_total = pick_artifacts(arts)
    out = {
        "first": _clean(msgs[0]) if msgs else "",
        "last": _clean(msgs[-1]) if msgs else "",
        "cwd": cwd,
        "turns": len(msgs),
        "topics": topics,
        "artifacts": art_rows,
        "art_total": art_total,
        "published": sorted(pub.values(), key=lambda a: a["t"]),
        "act": _iso_epoch(act_raw) or 0,
    }
    global _cache_dirty
    _scan_cache[path] = [key, out]
    _cache_dirty = True
    return out


def load_cache():
    """磁盘缓存: 3292 个对话第一次全扫要几秒, 之后重启 server 也不用重算。"""
    global _scan_cache
    try:
        with io.open(CACHE_PATH, encoding="utf-8") as fh:
            _scan_cache = json.load(fh)
    except Exception:
        _scan_cache = {}


def save_cache():
    global _cache_dirty
    if not _cache_dirty:
        return
    try:
        tmp = CACHE_PATH + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_scan_cache, fh, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
        _cache_dirty = False
    except Exception:
        pass


# ---------------------------------------------------------------- 实时状态

_alive_cache = {}               # pid -> [取样时刻, create_time 或 None(=不在了)]
ALIVE_TTL = 2.0


def alive_pids(pids):
    """这些 pid 里, 哪些还是活着的 claude 进程? 返回 {pid: 创建时间}。

    **只查传进来的这几十个 pid, 绝不遍历全表**。实测(bench_pids.py, 47 个 claude 进程):
        psutil.process_iter 全表   9022 ms   <- 页面每 2 秒轮询一次, 这个数字是灾难
        只查已知的 47 个 pid          1.0 ms   <- 结果与全表完全一致
        Toolhelp32 快照              52 ms

    缓存是**按 pid 逐个**存的, 不是"整批结果存一份"。曾经是后者, 结果只查单个会话
    的接口(切窗口/关窗口)会把全局缓存覆盖成"只有这一个会话的 pid", 接下来 2 秒里
    页面上**其它所有对话的徽章会集体消失**(它们的 pid 不在缓存里 = 判定已关闭)。
    逐 pid 缓存没有这个耦合: 谁问谁的, 互不干扰。
    """
    now = time.time()
    out = {}
    todo = []
    for pid in set(p for p in pids if p):
        hit = _alive_cache.get(pid)
        if hit and now - hit[0] < ALIVE_TTL:
            if hit[1] is not None:
                out[pid] = hit[1]
        else:
            todo.append(pid)
    if todo:
        try:
            import psutil
            for pid in todo:
                ct = None
                try:
                    p = psutil.Process(pid)
                    if p.name().lower() in config.CLAUDE_PROCS:
                        ct = p.create_time()
                except Exception:
                    ct = None
                _alive_cache[pid] = [now, ct]
                if ct is not None:
                    out[pid] = ct
        except Exception:
            pass                      # 没装 psutil: 一律当作查不到, 不假装知道
    if len(_alive_cache) > 512:       # 死 pid 会慢慢堆积, 定期扫掉过期的
        for k in [k for k, v in _alive_cache.items() if now - v[0] > 60]:
            _alive_cache.pop(k, None)
    return out


def _iso_epoch(s):
    """jsonl 的 timestamp 解析。带不带毫秒都要能吃 —— 解析失败会让新鲜度变成空白,
    而"不知道多新"比"显示旧"更糟。"""
    if not s:
        return None
    import datetime
    s = s.strip().replace("Z", "+0000")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.datetime.strptime(s, fmt).timestamp()
        except Exception:
            pass
    try:                                    # 3.11 的 fromisoformat 更宽容, 兜底
        return datetime.datetime.fromisoformat(s.replace("+0000", "+00:00")).timestamp()
    except Exception:
        return None


def _tool_brief(inp, name):
    """把工具参数压成一句能看懂的话。"""
    if not isinstance(inp, dict):
        return ""
    for k in ("description", "command", "file_path", "pattern", "url",
              "prompt", "query", "path"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            v = " ".join(v.split())
            if k == "file_path":
                v = os.path.basename(v)
            return v[:70]
    return ""


_GOAL_CACHE = {}          # path -> {"off": 已读到的字节, "g": 最后一条 goal_status 摘要}
_GOAL_LOCK = threading.Lock()


def goal_state(path):
    with _GOAL_LOCK:
        return _goal_state(path)


def _goal_state(path):
    """对话开着 /goal 吗(10-04 用户「如果goal active的话，在对话管理器也显示」)。
    来源: transcript 里 type=attachment / attachment.type=goal_status 的记录 —— 设定时写 met:false+sentinel,
    停止钩子每判一次写 met:false+reason, 结束(终端里 /goal clear 或判定达成)写 met:true+sentinel。
    所以「最后一条 met=false」= 还开着。增量读: 每个文件只读新增的字节。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    c = _GOAL_CACHE.get(path)
    if c is None or size < c["off"]:
        c = _GOAL_CACHE[path] = {"off": 0, "g": None}
    if size > c["off"]:
        try:
            with open(path, "rb") as fh:
                fh.seek(c["off"])
                buf = fh.read(size - c["off"])
        except OSError:
            return c["g"]
        cut = buf.rfind(b"\n")
        if cut < 0:
            return c["g"]
        c["off"] += cut + 1
        for ln in buf[:cut].split(b"\n"):
            if b'"goal_status"' not in ln:
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            a = d.get("attachment") or {}
            if a.get("type") != "goal_status":
                continue
            g = c["g"] if (c["g"] and not a.get("sentinel")) else {"n": 0}
            g = dict(g, on=not a.get("met"), cond=(a.get("condition") or "")[:400],
                     ts=d.get("timestamp") or "")
            if a.get("sentinel"):
                g["reason"] = ""
                if a.get("met"):
                    g["ended"] = True
            else:
                g["n"] = g.get("n", 0) + 1
                g["reason"] = (a.get("reason") or "")[:400]
            c["g"] = g
    return c["g"]


_BG_CACHE = {}            # path -> {"off": 已读字节, "started": {id: (epoch, kind)}, "done": set(ids)}
_BG_LOCK = threading.Lock()
_BG_START = (("Async agent launched successfully", "agent", re.compile(r"agentId: ([0-9a-z]+)")),
             ("Command running in background with ID:", "shell", re.compile(r"with ID: ([0-9a-z]+)")),
             ("Command did not complete within", "shell", re.compile(r"background \(ID: ([0-9a-z]+)\)")))
_BG_DONE = re.compile(r"^\s*<task-notification>\s*<task-id>([0-9a-z]+)</task-id>")


def _iso_epoch(s):
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def bg_tasks(path, since=0.0, max_age=86400):
    """这个对话还在跑的后台任务(10-04 用户「模型空闲可回话、但该对话仍有后台子 agent 或后台 shell 在跑」新状态)。
    启动 = tool_result 正文以「Async agent launched successfully … agentId: X」/「Command running in background with ID: X」/
    「Command did not complete within … (ID: X)」开头; 结束 = 以 <task-notification><task-id>X 开头的 queued_command 附件或用户消息。
    只按结构认(看正文开头), 不全文搜 —— 对话里引用这些字样不会被当成任务。只算本进程启动(since)之后开的、24 h 以内的。
    → [{"id", "kind": agent|shell, "age"}]"""
    with _BG_LOCK:
        try:
            size = os.path.getsize(path)
        except OSError:
            return []
        c = _BG_CACHE.get(path)
        if c is None or size < c["off"]:
            c = _BG_CACHE[path] = {"off": 0, "started": {}, "done": set()}
        if size > c["off"]:
            try:
                with open(path, "rb") as fh:
                    fh.seek(c["off"])
                    buf = fh.read(size - c["off"])
            except OSError:
                buf = b""
            cut = buf.rfind(b"\n")
            if cut >= 0:
                c["off"] += cut + 1
                for ln in buf[:cut].split(b"\n"):
                    if not (b"background" in ln or b"Async agent" in ln or b"task-notification" in ln):
                        continue
                    try:
                        d = json.loads(ln)
                    except ValueError:
                        continue
                    ts = _iso_epoch(d.get("timestamp"))
                    a = d.get("attachment") or {}
                    if a.get("type") == "queued_command":
                        m = _BG_DONE.match(str(a.get("prompt") or ""))
                        if m:
                            c["done"].add(m.group(1))
                        continue
                    if d.get("type") != "user":
                        continue
                    cont = (d.get("message") or {}).get("content")
                    if isinstance(cont, str):
                        m = _BG_DONE.match(cont)
                        if m:
                            c["done"].add(m.group(1))
                        continue
                    for b in cont if isinstance(cont, list) else []:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "text":
                            m = _BG_DONE.match(str(b.get("text") or ""))
                            if m:
                                c["done"].add(m.group(1))
                            continue
                        if b.get("type") != "tool_result":
                            continue
                        rc = b.get("content")
                        txt = rc if isinstance(rc, str) else " ".join(
                            x.get("text", "") for x in (rc or []) if isinstance(x, dict))
                        head = txt.lstrip()[:400]
                        for pre, kind, rx in _BG_START:
                            if head.startswith(pre):
                                m = rx.search(head)
                                if m:
                                    c["started"][m.group(1)] = (ts, kind)
                                break
        now = time.time()
        return [{"id": k, "kind": kind, "age": round(now - ts)}
                for k, (ts, kind) in c["started"].items()
                if k not in c["done"] and ts >= since - 5 and now - ts < max_age]


def tail_activity(path, tail_bytes=160_000):
    """从 jsonl 尾部读"此刻在干什么"和"这条消息多新"。

    不需要 PostToolUse hook: jsonl 是实时落盘的, 每次工具调用都会写一条 assistant
    记录。直接读尾部就能拿到最后一次动作, 粒度到每个工具调用, 而且零 hook 开销。
    """
    out = {"ts": None, "kind": "", "tool": "", "brief": ""}
    try:
        size = os.path.getsize(path)
        with io.open(path, "rb") as fh:
            if size > tail_bytes:
                fh.seek(size - tail_bytes)
                fh.readline()
            chunk = fh.read().decode("utf-8", "replace")
        lines = chunk.splitlines()
        # 工具返回的结果在 jsonl 里也是 type:"user"(带 tool_use_id)。倒序扫时如果
        # 不认这一点, 每跑完一个工具就会被读成"用户刚发了消息, 还没动手" —— 而那其实是
        # "工具刚返回, 正在想下一步"。碰到 tool_result 就继续往前找发起它的那次 tool_use。
        after_result = False
        for line in reversed(lines):
            if '"timestamp"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = _iso_epoch(d.get("timestamp") or "")
            if ts and out["ts"] is None:
                out["ts"] = ts
            ty = d.get("type")
            if ty == "assistant":
                c = d.get("message", {}).get("content")
                if isinstance(c, list):
                    for b in reversed(c):
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            out["kind"] = "tool_done" if after_result else "tool"
                            out["tool"] = b.get("name", "")
                            out["brief"] = _tool_brief(b.get("input"), out["tool"])
                            return out
                    txt = "".join(b.get("text", "") for b in c
                                  if isinstance(b, dict) and b.get("type") == "text")
                    if txt.strip():
                        out["kind"] = "text"
                        out["brief"] = _clean(txt, 70)
                        return out
            elif ty == "user":
                if '"tool_use_id"' in line:
                    after_result = True         # 工具结果, 不是人说的话
                    continue
                t = _text_of(d.get("message", {}))
                if not _is_real_user_text(t):   # system-reminder 之类的注入
                    continue
                out["kind"] = "user"
                out["brief"] = _clean(t, 70)
                return out
    except Exception:
        pass
    return out


def find_transcript(sid):
    for d in os.listdir(PROJ):
        fp = os.path.join(PROJ, d, sid + ".jsonl")
        if os.path.exists(fp):
            return fp
    return None


def load_states():
    out = {}
    if not os.path.isdir(STATE_DIR):
        return out
    try:
        names = os.listdir(STATE_DIR)
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with io.open(os.path.join(STATE_DIR, fn), encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            continue
        sid = d.get("sid") or fn[:-5]
        out[sid] = d
    return out


PROC_KEYS = ("pid", "pid_ctime", "term_pid", "term_name", "hwnd", "win_title",
             "win_owner", "shell_pid", "shell_name")


def rec_procs(rec):
    """这个对话记过账的所有进程。兼容只有顶层 pid 的旧 state 文件。"""
    ps = rec.get("procs")
    if isinstance(ps, list) and ps:
        return ps
    if rec.get("pid"):
        return [{k: rec.get(k) for k in PROC_KEYS}]
    return []


_shell_cache = {}               # claude pid -> (shell_pid, shell_name); 父进程不会变


def shell_of(pid):
    """这个 claude 进程的父 shell。**hook 没记的时候现场查一次。**

    为什么要兜底: `shell_pid` 是后来才加进 hook 的, 老的 state 文件里没有; 而它正是
    VS Code 桥认终端用的那个 pid(`Terminal.processId`)。不兜底的话, 升级之后每个
    还开着的对话都得先说一句话让 hook 补记, 才能精确切到标签页 —— 太别扭了。
    查一次就缓存: 一个进程的父不会中途换人。
    """
    if pid in _shell_cache:
        return _shell_cache[pid]
    out = (None, "")
    try:
        import psutil
        par = psutil.Process(pid).parent()
        if par:
            out = (par.pid, par.name())
    except Exception:
        pass
    _shell_cache[pid] = out
    return out


def live_windows(rec, alive):
    """此刻**真的还开着**的那几个窗口。

    一个对话可以被 resume 到多个窗口里(它们共用 session_id), 所以这里返回的是
    一个列表, 通常 0 或 1 个; 出现 2 个以上就是"你重复打开了同一个对话",
    页面会告警 —— 两个窗口写同一份 jsonl, 是会互相覆盖的。
    """
    out = []
    for e in rec_procs(rec):
        pid, ct = e.get("pid"), e.get("pid_ctime")
        if not pid or pid not in alive:
            continue
        if ct is not None and abs(alive[pid] - ct) >= 2.0:
            continue                              # pid 被回收给了别的进程
        sp, sn = e.get("shell_pid"), e.get("shell_name")
        if not sp:
            sp, sn = shell_of(pid)
        out.append({
            "pid": pid,
            "hwnd": e.get("hwnd"),
            "term_pid": e.get("term_pid"),
            "term": e.get("term_name") or "",
            "title": e.get("win_title") or "",
            # 这个 HWND 实际属于谁。VS Code 里终端宿主是没有窗口的渲染进程,
            # 句柄来自它的祖先(IDE 主窗口), 切过去只能切到窗口、到不了标签页。
            "owner": e.get("win_owner") or "",
            # 承载这个对话的 shell。VS Code 里它 == 扩展 API 的 Terminal.processId,
            # 有它才能精确点到具体哪个终端标签页。
            "shell_pid": sp,
            "shell": sn or "",
            "ts": e.get("ts"),
        })
    return out


def all_pids(states):
    out = []
    for rec in states.values():
        out += [e.get("pid") for e in rec_procs(rec)]
    return out


def resolve_state(rec, alive):
    """把 hook 记的账 + 进程是否还活着, 合成最终状态。

    进程存活是唯一可靠的"窗口还开着吗"信号 —— SessionEnd hook 在窗口被直接关掉时
    基本不触发(实测 2299 个会话里只有 148 个留下过 SessionEnd 记录), 所以不管
    hook 最后记的是 running 还是 done, 进程没了就是 closed。
    """
    if not live_windows(rec, alive):                 # 一个活着的窗口都没有
        return "closed"
    st = rec.get("state") or "done"
    if st == "closed":                                # 进程还在, 说明只是 /clear
        return "done"
    return st


def status_map(with_activity=True):
    states = load_states()
    alive = alive_pids(all_pids(states))
    now = time.time()
    out = {}
    turns = turn_push.states() if turn_push else {}
    for sid, rec in states.items():
        st = resolve_state(rec, alive)
        if turn_push:
            st = turn_push.manager_state(st, turns.get(sid), rec.get("ts"))
        # 10-04 用户「开着等你和等你确认有什么区别；不如这样，几个类型：正在跑，等你选择，等你回复意见，可以关了」
        # Notification 里「Claude is waiting for your input」只是空闲 60 s 的提醒, 不是要你选什么 —— 归回「这一轮结束」
        if st == "waiting" and "waiting for your input" in (rec.get("note") or ""):
            st = "done"
        sub = ""
        if st == "done":                          # 结束的这一轮: 等你回复意见 / 可以关了 —— 由话题脉络(Stop 后台 haiku)顺带判定
            sub = "reply"
            try:
                op = os.path.join(STATE_DIR, "outline", sid + ".json")
                if os.path.getmtime(op) >= (rec.get("ts") or 0) - 1:
                    if (_json_cached(op) or {}).get("next") == "可以关了":
                        sub = "closable"
            except OSError:
                pass
        wins = live_windows(rec, alive)
        for w in wins:                            # VS Code 里显示标签自己的名字, 不显示 IDE 主窗口标题
            if (w.get("owner") or w.get("term") or "").lower() == "code.exe":
                tn = (actions.tab_names() or {}).get(w.get("shell_pid") or 0)
                w["title"] = tn or "VS Code 标签"
        row = {
            "state": st, "sub": sub,
            "goal": rec.get("goal", ""),
            "result": rec.get("result", ""),
            "note": rec.get("note", ""),
            "took": rec.get("took"),
            "ts": rec.get("ts"),                    # hook 最后记账的时刻
            "age": round(now - (rec.get("ts") or now), 1),
            "term": rec.get("term_name", ""),
            "pid": rec.get("pid"),
            # 开着这个对话的窗口(可能不止一个 —— 那就是要提醒你关掉的情况)
            "wins": wins,
        }
        # 只对还活着的会话去读 jsonl 尾部 —— 这才是"此刻在干什么"的实时来源
        if with_activity and st != "closed":
            fp = find_transcript(sid)
            if fp:
                act = tail_activity(fp)
                row["act_kind"] = act["kind"]
                row["act_tool"] = act["tool"]
                row["act_brief"] = act["brief"]
                row["act_ts"] = act["ts"]
                row["act_age"] = round(now - act["ts"], 1) if act["ts"] else None
                # 空闲但后台还有子 agent / shell 在跑 → sub = "bg"(只算当前 claude 进程启动之后开的)
                if st == "done" and wins:
                    pids = {w["pid"] for w in wins}
                    since = max([e.get("pid_ctime") or 0 for e in rec_procs(rec) if e.get("pid") in pids] or [0])
                    bg = bg_tasks(fp, since)
                    if bg:
                        row["sub"] = "bg"
                        row["bg"] = bg
                # 10-04 用户「各个对话有较长计算的能否都给个时间预期，显示到对话管理器上」: 对话自己用 eta.py 上钟
                e = _json_cached(os.path.join(STATE_DIR, "eta", sid + ".json"))
                if e and e.get("end") and now - e["end"] < 6 * 3600:
                    row["eta"] = {"what": e.get("what", ""), "basis": e.get("basis", ""), "start": e.get("start"),
                                  "end": e["end"], "end0": e.get("end0", e["end"]), "extends": e.get("extends", [])}
                # 没报预期却在长跑: 工具连续跑 > 60 s, 或空闲但后台任务在跑
                if "eta" not in row:
                    if st == "running" and act["kind"] == "tool" and act["ts"] and now - act["ts"] > 60:
                        row["eta_missing"] = {"kind": "tool", "since": act["ts"], "tool": act["tool"]}
                    elif row.get("bg"):
                        row["eta_missing"] = {"kind": "bg", "since": now - max(x["age"] for x in row["bg"])}
                g = goal_state(fp)
                if g and g.get("on"):
                    row["goal_on"] = True
                    row["goal_cond"] = g.get("cond", "")
                    row["goal_reason"] = g.get("reason", "")
                    row["goal_n"] = g.get("n", 0)
        out[sid] = row
    return out


def prune_states(days=30):
    """状态文件按会话累积, 定期清掉早就关掉的老会话。"""
    cutoff = time.time() - days * 86400
    if not os.path.isdir(STATE_DIR):
        return
    for fn in os.listdir(STATE_DIR):
        p = os.path.join(STATE_DIR, fn)
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass


def _art_view(rows):
    """给前端补 basename / 所在目录 / 文件还在不在。

    exists 必须现算 —— 产物被后续会话删掉/移走是常事, 列一个点开就 404 的链接
    比不列还糟。150 行 x 平均十几个文件 ≈ 一两千次 stat, 实测 10ms 量级。
    """
    out = []
    for a in rows:
        fp = a["p"]
        try:
            ok = os.path.isfile(fp)
            sz = os.path.getsize(fp) if ok else 0
        except OSError:
            ok, sz = False, 0
        out.append({
            "p": fp,
            "n": os.path.basename(fp),
            "d": os.path.dirname(fp),
            "t": a.get("t") or 0,
            "why": a.get("why") or "",
            "kind": a.get("kind") or "code",
            "edits": a.get("n") or 1,
            "exists": ok,
            "kb": round(sz / 1024.0, 1),
        })
    return out


def list_sessions(limit):
    rows = []
    if not os.path.isdir(PROJ):
        return rows
    files = []
    for d in os.listdir(PROJ):
        p = os.path.join(PROJ, d)
        if not os.path.isdir(p) or d in IGNORE_PROJ:
            continue
        for fn in os.listdir(p):
            if not fn.endswith(".jsonl"):
                continue
            fp = os.path.join(p, fn)
            try:
                files.append((os.path.getmtime(fp), fp, fn[:-6], d))
            except OSError:
                pass
    files.sort(reverse=True)
    total = len(files)
    notes = load_notes()
    autot = load_auto_titles()
    stat = status_map(with_activity=False)
    for mt, fp, sid, proj in files[:limit]:
        info = scan_file(fp) or {}
        # 本工具自己起的 `claude -p`(起标题 / 做复盘)会留下一次性会话。新的都钉在
        # titler/ 的 slug 下、已被 IGNORE_PROJ 排掉; 这一条是给**历史遗留**的那些兜底,
        # 它们落在 server 自己的 cwd 下, 混在用户的真对话里(2026-08-26 实测 17 条)。
        if (info.get("first") or "").startswith(SELF_PROMPT_HEAD):
            total -= 1
            continue
        n = notes.get(sid, {})
        try:
            size = os.path.getsize(fp)
        except OSError:
            size = 0
        rows.append({
            "id": sid,
            # mtime 是**给人看和给排序用的"最后说话时刻"**, 不是文件 mtime。
            # 文件 mtime 留在 ftime 里备查(两者差得远 = 这个窗口开着但没在用)。
            "mtime": info.get("act") or mt,
            "ftime": mt,
            "project": proj,
            "cwd": info.get("cwd") or "",
            "first": info.get("first") or "",
            "last": info.get("last") or "",
            "topics": info.get("topics") or [],
            "artifacts": _art_view(info.get("artifacts") or []),
            "art_total": info.get("art_total") or 0,
            "published": info.get("published") or [],
            "turns": info.get("turns") or 0,
            "kb": round(size / 1024.0, 1),
            "title": n.get("title", ""),
            "auto_title": autot.get(sid, ""), "label": smart_label(sid, info.get("first") or ""),
            "note": n.get("note", ""), "otitle": outline_title(sid), "tag": tab_tag(sid),
            "star": bool(n.get("star")),
            "status": stat.get(sid),
        })
    # 按真实活动时刻重排。这一步是正确的而不是近似的: 文件只会被追加,
    # 所以 act <= 文件 mtime 恒成立 ⇒ 「act 前 limit 名」必是「mtime 前 limit 名」
    # 的子集, 在这个窗口内重排不会漏掉任何一条本该上榜的对话。
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows, total


def _grep_real_text(path, kw_l):
    """只在真人发言与 Claude 的回答正文里找 kw。

    绝不能直接 grep 整个 jsonl —— 全局 CLAUDE.md 与 system-reminder 会被注入进
    每一个会话, 那样搜"面汤"会命中 400 个会话里的 99 个, 全是同一段系统提示。
    """
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if kw_l not in line.lower():
                    continue
                if '"type":"user"' not in line and '"type":"assistant"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                ty = d.get("type")
                if ty not in ("user", "assistant"):
                    continue
                t = _text_of(d.get("message", {}))
                if ty == "user" and not _is_real_user_text(t):
                    continue
                low = t.lower()
                i = low.find(kw_l)
                if i < 0:
                    continue
                who = "我: " if ty == "user" else "claude: "
                return who + _clean(t[max(0, i - 70):i + 130], 200)
    except Exception:
        pass
    return None


def grep_sessions(kw, scan_n):
    """在最近 scan_n 个会话的正文里全文搜索。"""
    kw_l = kw.lower()
    files = []
    for d in os.listdir(PROJ):
        p = os.path.join(PROJ, d)
        if not os.path.isdir(p) or d in IGNORE_PROJ:
            continue
        for fn in os.listdir(p):
            if fn.endswith(".jsonl"):
                fp = os.path.join(p, fn)
                try:
                    files.append((os.path.getmtime(fp), fp, fn[:-6], d))
                except OSError:
                    pass
    files.sort(reverse=True)
    n_all = len(files)
    files = files[:scan_n]
    notes = load_notes()
    autot = load_auto_titles()
    hits = []
    for mt, fp, sid, proj in files:
        # 先用整文件做一次廉价预筛(读一遍字符串), 没有关键词就完全跳过逐行解析
        try:
            with io.open(fp, encoding="utf-8", errors="replace") as fh:
                if kw_l not in fh.read().lower():
                    continue
        except Exception:
            continue
        snippet = _grep_real_text(fp, kw_l)
        if snippet is None:
            continue          # 只出现在 system 提示里, 不算命中
        info = scan_file(fp) or {}
        n = notes.get(sid, {})
        hits.append({
            "id": sid, "mtime": info.get("act") or mt, "ftime": mt, "project": proj,
            "cwd": info.get("cwd") or "", "first": info.get("first") or "",
            "last": info.get("last") or "", "turns": info.get("turns") or 0,
            "topics": info.get("topics") or [],
            "kb": 0, "title": n.get("title", ""),
            "auto_title": autot.get(sid, ""), "label": smart_label(sid, info.get("first") or ""), "note": n.get("note", ""), "otitle": outline_title(sid), "tag": tab_tag(sid),
            "star": bool(n.get("star")), "snippet": snippet,
        })
    hits.sort(key=lambda r: r["mtime"], reverse=True)
    return hits, len(files), n_all


# ---------------------------------------------------------------- 对话全文树

TREE_TURN_CHARS = 60_000      # 单轮回复最多回传多少字(防止一条超长回复撑爆前端)
TREE_MAX_TURNS = 400          # 最多回传多少轮


def _tool_line(blk):
    """把一次工具调用压成一行, 让"只有工具没有文字"的回复不至于显示成空白。"""
    name = blk.get("name") or "tool"
    inp = blk.get("input") or {}
    return "· %s %s" % (name, _tool_brief(inp, name))


def conv_tree(sid):
    """把一个对话拆成 [{q: 我说的, a: claude 的回复, ...}] 的轮次列表。

    前端的展开是两层的(先展开提问, 再逐条展开回复), 所以这里必须给全文而不是摘要。
    子 agent(isSidechain)的往返不算轮次 —— 那是 claude 自己派出去的活, 混进来会把
    "我问了什么"这条主线冲散; 但它们的条数记在所属轮次上, 免得看起来无中生有。
    """
    path = find_transcript(sid)
    if not path:
        return None
    turns = []
    cur = None

    def flush():
        if cur is None:
            return
        txt = "\n".join(p for p in cur["_a"] if p).strip()
        cur["a"] = txt[:TREE_TURN_CHARS]
        cur["cut"] = len(txt) > TREE_TURN_CHARS
        del cur["_a"]
        turns.append(cur)

    bad = [0]
    def one(d):
        nonlocal cur
        ty = d.get("type")
        if ty == "attachment":   # 10-04: 它运算时我插的问题(queued_command), 记在当前这一轮下面
            at = d.get("attachment") or {}
            pt = at.get("prompt") if at.get("type") == "queued_command" else ""
            if isinstance(pt, list):         # 10-04: 带截图的插问, prompt 是 [{type:text},{type:image}] —— 以前 .strip() 抛错, 把这一行之后的整份解析都静默丢了
                pt = " ".join(b.get("text", "") if b.get("type") == "text" else "[图]" for b in pt if isinstance(b, dict))
            pt = _slash(str(pt or "").strip())
            if pt and cur is not None and _is_real_user_text(pt):
                cur.setdefault("mid", []).append({"q": pt[:4000], "ts": at.get("timestamp") or d.get("timestamp", "")})
            # 10-04: 对话在跑时网页发来、由钩子塞进上下文的消息(additionalContext「【…发来 N 条消息…】\n- [时刻] 来源：正文」), 也记成插问
            if at.get("type") == "hook_additional_context" and cur is not None:
                for blob in at.get("content") or []:
                    if isinstance(blob, str) and blob.startswith("【") and "发来" in blob.split("\n", 1)[0]:
                        for m in re.finditer(r"^- \[([^\]]+)\] ([^：\n]{1,20})：(.+)$", blob, re.M):
                            cur.setdefault("mid", []).append({"q": m.group(3)[:4000], "ts": m.group(1), "via": m.group(2)})
            return
        if ty not in ("user", "assistant"):
            return
        if d.get("isSidechain"):
            if cur is not None:
                cur["sub"] += 1
            return
        msg = d.get("message") or {}
        if ty == "user":
            t = _text_of(msg).strip()
            # 10-04: skill 正文 / 系统注入(isMeta 或 JUNK_PREFIX)不是我说的话 —— 以前被当成提问, 一整页 skill 挤成一段。
            # skill 只在这一轮的工具流水里记一行
            if d.get("isMeta") or t.startswith(JUNK_PREFIX):
                m = re.search(r"Base directory for this skill: \S*?[\\/]skills[\\/]([^\\/\s]+)", t)
                if m and cur is not None:
                    cur["_a"].append("· 载入 skill " + m.group(1))
                return
            # 工具结果也是 type=user, 不是我说的话
            if not t or not _is_real_user_text(t):
                return
            flush()
            cur = {"q": t[:4000], "ts": d.get("timestamp", ""),     # 10-04: 保留换行, 前端按 Markdown 渲染
                   "tools": 0, "sub": 0, "_a": []}
            return
        if cur is None:      # 极少数对话以 assistant 开头(--continue 拼接)
            cur = {"q": "(这一轮前面没有我的发言)", "ts": d.get("timestamp", ""),
                   "tools": 0, "sub": 0, "_a": []}
        cur["end"] = d.get("timestamp", "") or cur.get("end", "")   # 10-04 A: 完成时刻 = 本轮最后一条 assistant 行; 想了多久 = end − ts(用户提问时刻)
        for blk in msg.get("content") or []:
            if not isinstance(blk, dict):
                return
            if blk.get("type") == "text":
                cur["_a"].append(blk.get("text", ""))
            elif blk.get("type") == "tool_use":
                cur["tools"] += 1
                cur["_a"].append(_tool_line(blk))

    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"type":"user"' not in line and '"type":"assistant"' not in line and '"queued_command"' not in line and '"hook_additional_context"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                try:
                    one(d)
                except Exception:            # 10-04: 一行坏了只跳过这一行(以前整份解析静默中止, 截图插问之后全丢)
                    bad[0] += 1
    except Exception:
        pass
    flush()
    total = len(turns)
    return {"turns": turns[-TREE_MAX_TURNS:], "total": total,
            "dropped": max(0, total - TREE_MAX_TURNS)}


def tree_sse(h, sid, period=0.25, ping_s=15):
    """SSE: 盯一个会话的 jsonl(大小+修改时刻)与在跑/等你状态(turn_push), 任一变化就发 event=tree。
    每个连接每 0.25 s 两次 stat, 微秒级; 不读文件内容 —— 内容由浏览器收到事件后拉 /api/tree(大会话全量解析 17 ms, 10-04 实测)。"""
    if not re.fullmatch(r"[0-9a-f-]{36}", sid or ""):
        return h._send(404, {"error": "not found"})
    path = find_transcript(sid)                   # 新开的对话第一句话之前没有 jsonl: 照样连上, 下面每 0.25 s 再找
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream; charset=utf-8")
    h.send_header("Cache-Control", "no-store")
    h.end_headers()

    def ver():
        nonlocal path
        if not path:
            path = find_transcript(sid)
        try:
            st = os.stat(path); v = "%d:%d" % (st.st_size, st.st_mtime_ns)
        except (OSError, TypeError):          # 新对话还没有 jsonl: path=None
            v = "gone"
        t = (turn_push.states().get(sid) or {}) if turn_push else {}
        try:                                           # 话题脉络更新了也推
            v += ":%d" % os.stat(os.path.join(STATE_DIR, "outline", sid + ".json")).st_mtime_ns
        except OSError:
            pass
        return v + "|" + str(t.get("state")) + "|" + str(t.get("ts"))
    last, idle = None, 0.0
    try:
        while True:
            v = ver()
            if v != last:
                h.wfile.write(("event: tree\ndata: %s\n\n" % json.dumps({"v": v})).encode("utf-8")); h.wfile.flush()
                last, idle = v, 0.0
            elif idle >= ping_s:
                h.wfile.write(b": ping\n\n"); h.wfile.flush(); idle = 0.0
            time.sleep(period); idle += period
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        return


def xref_rules():
    """config.json 的 "xref": [{name, re, url?, hover?} | {gloss: <json 文件路径>, skip: [键…]}] —— 本机私有, 不进公开版。"""
    rules, gloss = [], {}
    for r in config._cfg.get("xref", []):
        if r.get("gloss"):
            try:
                with io.open(r["gloss"], encoding="utf-8") as fh:
                    g = json.load(fh)
                gloss.update({k: v for k, v in g.items() if isinstance(v, str) and not k.startswith("_") and k not in (r.get("skip") or [])})
            except Exception:
                pass
            rules.append({"name": r.get("name", ""), "re": "(?!)"})      # 占位: 保持下标与 config 一致
        else:
            rules.append({k: r.get(k) for k in ("name", "re", "url") if r.get(k)} | {"hover": bool(r.get("hover"))})
    return {"rules": rules, "gloss": gloss, "preview": True}


_xref_cache = {}


def xref_fetch(url, ttl=60):
    """悬停数据: 只取本机(127.0.0.1/localhost)的 JSON, 缓存 60 s。"""
    if not re.match(r"https?://(127\.0\.0\.1|localhost)(:\d+)?/", url):
        return None
    hit = _xref_cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:
        d = None
    _xref_cache[url] = (time.time(), d)
    return d


def path_info(p, cwd=""):
    """路径链接: 解析成绝对路径(相对路径按对话的 cwd), 报是否存在/文件夹, 并给出逐级上级目录。"""
    p = (p or "").strip().strip("`'\"")
    if not p:
        return {}
    p = os.path.expanduser(p)
    cands = [p] if os.path.isabs(p) else [os.path.join(c, p) for c in (cwd, os.path.expanduser("~")) if c]
    ab = None
    for c in cands:
        if os.path.exists(c):
            ab = c; break
    ab = os.path.normpath(ab or cands[0])
    par, d = [], os.path.dirname(ab)
    while d and len(par) < 12:
        par.append(d)
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    ex = os.path.exists(ab)
    return {"abs": ab, "exists": ex, "isdir": os.path.isdir(ab),
            "size": os.path.getsize(ab) if ex and os.path.isfile(ab) else None, "parents": par[::-1]}


# 10-04 用户「每个页面做一个到顶部、到底部的按钮」: 预览页(/file)也挂上(index.html 自带一份)
JUMP_HTML = ('<div style="position:fixed;right:14px;bottom:14px;display:flex;flex-direction:column;gap:6px;z-index:99">'
             + "".join('<button title="%s" onclick="scrollTo({top:%s,behavior:&quot;instant&quot;})" style="width:34px;height:34px;'
                       'border-radius:50%%;border:1px solid #888;background:#2228;color:#fff;font-size:15px;cursor:pointer">%s</button>'
                       % (t, y, c) for t, y, c in (("到顶部", "0", "↑"), ("到底部", "document.body.scrollHeight", "↓")))
             + "</div>")


def _shell_sealed(shell_pid):
    """claude 的父进程是不是「密封」的: powershell/pwsh -Command claude… 或 cmd /c claude…(不是交互 shell)。
    读的是 OS 进程命令行, 与桥的名单互为核对(pid 被复用给一个交互 shell 时这里会是 False)。"""
    try:
        import psutil
        a = [x.lower() for x in psutil.Process(int(shell_pid)).cmdline()]
    except Exception:
        return False
    if not a:
        return False
    exe = os.path.basename(a[0])
    if exe in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        k = next((i for i, x in enumerate(a) if x in ("-command", "-c")), -1)
        if k < 0 or "-noexit" in a:
            return False
    elif exe in ("cmd.exe", "cmd"):
        k = next((i for i, x in enumerate(a) if x == "/c"), -1)
        if k < 0 or "/k" in a:
            return False
    else:
        return False
    rest = " ".join(a[k + 1:]).strip().strip('"')
    return rest == "claude" or rest.startswith("claude ") or rest.startswith("codex resume ")


def _bridge_type(shell_pid, text):
    """经 VS Code 桥(vscode-bridge 扩展) /type 往 pid=shell_pid 的终端打一行字并回车。8 个端口并行探。→ (ok, 说明)"""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor

    def ping(port):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/ping" % port, timeout=0.4) as r:
                d = json.loads(r.read().decode("utf-8"))
            return port if d.get("what") == "claude-sessions-bridge" and "type" in (d.get("routes") or []) else None
        except Exception:
            return None
    with ThreadPoolExecutor(8) as ex:
        ports = [x for x in ex.map(ping, range(config.VSCODE_BRIDGE_PORT, config.VSCODE_BRIDGE_PORT + config.VSCODE_BRIDGE_SPAN)) if x]
    if not ports:
        return False, "没找到带 /type 的 VS Code 桥(装 vscode-bridge 扩展; 改过扩展要 Reload Window)"
    for port in ports:
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/type" % port, method="POST", headers={"Content-Type": "application/json"},
                                         data=json.dumps({"pid": int(shell_pid), "text": text}).encode("utf-8"))
            with urllib.request.urlopen(req, timeout=6) as r:
                if json.loads(r.read().decode("utf-8")).get("ok"):
                    return True, "port %d 已打字" % port
        except Exception:
            continue
    return False, "各个 VS Code 窗口里都没找到这个终端(pid %s)" % shell_pid


def _prompt_seen(sid, since):
    """hook_state.py 写的 state/<sid>.json: 打字之后出现过任何钩子事件 = 对话真的收到了这句话。
    (发之前对话是空闲的, 不会有别的事件; 只认 UserPromptSubmit 会漏 —— 它很快被随后的 PreToolUse 覆盖)"""
    try:
        with io.open(os.path.join(STATE_DIR, sid + ".json"), encoding="utf-8") as fh:
            d = json.load(fh)
        return float(d.get("ts") or 0) >= since and d.get("last_event") not in (None, "SessionStart")
    except Exception:
        return False


def _bridge_enter(shell_pid):
    """经桥 /enter 只补一次回车(桥要 ≥ type3 版; 旧版没有这个路由 → 返回 False)。"""
    import urllib.request
    for port in range(config.VSCODE_BRIDGE_PORT, config.VSCODE_BRIDGE_PORT + config.VSCODE_BRIDGE_SPAN):
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/enter" % port, method="POST", headers={"Content-Type": "application/json"},
                                         data=json.dumps({"pid": int(shell_pid)}).encode("utf-8"))
            with urllib.request.urlopen(req, timeout=2) as r:
                if json.loads(r.read().decode("utf-8")).get("ok"):
                    return True
        except Exception:
            continue
    return False


def _confirm_prompt(sid, shell_pid, since, n_chars, tries=1):
    """10-04(11:35 实发): 长中文句打进输入框后回车被当成粘贴里的换行, 字停在输入框没发出去。
    打字后等对话真收到(UserPromptSubmit); 等不到就补一次回车再等。→ 给网页看的一句说明。"""
    first = min(6.0, 1.5 + n_chars * 0.008)          # 桥那边回车前要等 300 ms + 6 ms/字, 这里多留余量
    end = time.time() + first
    while time.time() < end:
        if _prompt_seen(sid, since):
            return "对话已收到"
        time.sleep(0.25)
    # 10-04 20:16 实发: 新开的对话刚起来(SessionStart 钩子还在跑), 字进了输入框, 回车和补的那一次回车都被吞了 → 新对话多补几次
    for k in range(tries):
        if not _bridge_enter(shell_pid):
            return "未确认收到, 补回车失败(桥可能是旧版, 需 Reload Window) —— 请到终端手动按回车"
        end = time.time() + 3.0
        while time.time() < end:
            if _prompt_seen(sid, since):
                return "第一次回车没生效, 已补按 %d 次回车, 对话已收到" % (k + 1)
            time.sleep(0.25)
    return "已补按 %d 次回车但仍未确认收到 —— 请到终端看一眼" % tries


def _relay():
    """可选: config.json 的 "relay_dir" 指向一个带 relay.py(queue/log_sent) 的目录 —— 对话在跑时把消息排队, 由钩子送达。没配就不排队。"""
    d = config._cfg.get("relay_dir")
    if not d:
        return None
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        import relay
        return relay
    except Exception:
        return None


def new_session(cwd, text="", where="vscode"):
    """开新对话(10-04 用户「加一个开新对话功能。新对话可以只在对话管理器开，也可以同时也在vscode开，默认也在vscode开」)。
    where="vscode": 经 VS Code 桥 /new 开终端标签跑 claude(不给 name, 免得成静态标签), 等 ~/.claude/sessions/<pid>.json 出现拿到 sid;
                    有第一句话就经 send_to 打进去。网页这边就是镜像 + 输入框(A 路线)。
    where="manager": 网页托管(B 路线, headless stream-json)还没做 —— 明确报不支持。
    → {ok, sid, pid, shell_pid, sent?, ms} / {ok: False, why}"""
    t0 = time.time()
    if where != "vscode":
        return {"ok": False, "why": "「只在管理器」要等网页托管会话(B 路线)做好; 现在只能开在 VS Code 里、网页镜像"}
    cwd = os.path.abspath(os.path.expanduser(cwd or os.path.expanduser("~")))
    if not os.path.isdir(cwd):
        return {"ok": False, "why": "目录不存在: %s" % cwd}
    trusted = actions.trust_folder(cwd) if config.AUTO_TRUST else None
    via = actions.bridge("/new", {"cwd": cwd, "cmd": "claude"}, timeout=10, side_effect=True)
    if not via:
        return {"ok": False, "why": "没找到 VS Code 桥(扩展没装? VS Code 没开?) —— 没有开任何东西"}
    if not via.get("ok") or not via.get("pid"):
        return {"ok": False, "why": "桥没开成终端: %s" % (via.get("why") or via)}
    shell = int(via["pid"])
    sdir = os.path.join(config.CLAUDE_HOME, "sessions")
    import psutil
    sid = pid = None
    while time.time() - t0 < 25 and not sid:          # claude 起来要几秒; 按「父进程 = 这个终端的 shell」认领
        for f in os.listdir(sdir):
            try:
                with io.open(os.path.join(sdir, f), encoding="utf-8") as fh:
                    d = json.load(fh)
                if psutil.Process(int(d["pid"])).ppid() == shell:
                    sid, pid = d.get("sessionId"), d.get("pid"); break
            except Exception:
                continue
        if not sid:
            time.sleep(0.3)
    if not sid:
        return {"ok": False, "why": "终端开了(shell pid %d), 但 25 s 内没等到 claude 起来 —— 去 VS Code 看一眼那个标签" % shell, "shell_pid": shell}
    r = {"ok": True, "sid": sid, "pid": pid, "shell_pid": shell, "cwd": cwd, "trusted": trusted}
    text = (text or "").strip()
    if text:
        t_ready = time.time() + 20                       # 10-04: 等 SessionStart 钩子记过账(=启动钩子跑完、输入框就绪)再多等 1 s; 太早打字回车会被吞
        while time.time() < t_ready:
            try:
                with io.open(os.path.join(STATE_DIR, sid + ".json"), encoding="utf-8") as fh:
                    if json.load(fh).get("last_event"):
                        break
            except Exception:
                pass
            time.sleep(0.3)
        time.sleep(1.0)
        for i in range(30):                              # 刚起来的会话要等它到「空闲」才能收字
            try:
                r["sent"] = send_to(sid, text, tries=5)
            except ValueError as e:
                r["sent"] = {"ok": False, "why": str(e)}
            if r["sent"].get("ok") or "没开着" not in (r["sent"].get("why") or ""):
                break
            time.sleep(0.5)
    r["ms"] = int((time.time() - t0) * 1000)
    return r


def send_to(sid, text, tries=1):
    """网页输入框发一句话给某会话(10-04 用户「以后和claude聊统一通过对话管理器」A 路线)。
    空闲 → 经 VS Code 桥 /type 打进它的终端并回车(桥只收单行, 换行压成空格; 密封终端打原文, 别的终端加「【对话管理器】」头);
    在跑 → 配了 relay 就排队(钩子在下一次工具调用后 / 本轮结束时塞进上下文), 没配就报「等这一轮结束再发」。
    没开着的会话直接报失败; 空闲但打字失败也报失败, 不悄悄排队 —— 网页要的是「发出去了没有」。
    空闲/在跑按 turn_push.status_of 判(与标签 ▶ 同源; 会话文件的 status 有 idle / waiting / busy 三种值)。"""
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("消息不能为空")
    if len(text) > 3900:
        raise ValueError("消息太长(>3900 字)")
    sdir = os.path.join(config.CLAUDE_HOME, "sessions")
    sess = None
    for f in (os.listdir(sdir) if os.path.isdir(sdir) else []):
        try:
            with io.open(os.path.join(sdir, f), encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            continue
        if d.get("sessionId") == sid and alive_pids([int(d.get("pid") or 0)]):
            sess = d
            break
    if not sess:
        return {"ok": False, "why": "这个对话没开着(没有活的 claude 进程) —— 先 Resume"}
    T = turn_push.states().get(sid) if turn_push else None
    st = turn_push.status_of(sess, T) if turn_push else sess.get("status")
    # 10-04 用户「f1 显示已排队, 实际是可交互等待程序运行」: 这一轮已答完、只剩后台 shell 在跑时, Claude Code 会话文件写 status=shell(提示符可打字),
    # 而 turn_notify 的 Stop 为保住标签 ▶ 补记了 running → 旧判据走排队, 可没有进行中的轮次, 钩子不触发, 消息卡到后台任务结束。
    # 会话文件的 shell 不比钩子记录旧(2 s 容差) → 按空闲打进终端。(实测: 前台 Bash 跑时会话文件是 busy, 不是 shell)
    if sess.get("status") == "shell" and (sess.get("statusUpdatedAt") or sess.get("updatedAt") or 0) / 1000 >= (T or {}).get("ts", 0) - 2:
        st = "idle"
    if st in ("idle", "waiting"):
        try:
            import psutil
            shell = psutil.Process(int(sess["pid"])).ppid()
        except Exception:
            shell = None
        if not shell:
            return {"ok": False, "why": "找不到它所在终端的 shell 进程"}
        t_typed = time.time()
        # 10-04 密封终端: shell 本身是「-Command claude…」, claude 退出它就退出, 从不读键盘 → 打原文, 不加头(桥那边也核一遍名单)。
        # 不是密封终端(手动开的老终端) / 旧桥拒收 → 退回带「【对话管理器】」头。
        ok, why = _bridge_type(shell, text) if _shell_sealed(shell) else (False, "【开头")
        if not ok and "【开头" in why:
            ok, why = _bridge_type(shell, "【对话管理器】" + text)
        if not ok:
            return {"ok": False, "why": "打字失败: " + why}
        why += "; " + _confirm_prompt(sid, shell, t_typed, len(text), tries)
        R = _relay()
        if R:
            R.log_sent(sid, dict(ts=time.strftime("%Y-%m-%dT%H:%M:%S"), frm="对话管理器", text=text, mode="typed"))
        return {"ok": True, "mode": "typed", "why": why, "ts": time.time()}
    R = _relay()
    if not R:
        return {"ok": False, "why": "对话在跑(%s), 终端只在空闲时收字 —— 等这一轮结束再发" % st}
    m = R.queue(sid, text, "对话管理器")
    return {"ok": True, "mode": "queued", "why": "对话在跑(%s), 下一次工具调用后或本轮结束时由钩子送达" % st, "id": m["id"], "ts": time.time()}


# ---------------------------------------------------------------- resume

WT_EXE = shutil.which("wt.exe") or shutil.which("wt")
WT_SETTINGS = os.path.expandvars(
    r"%LOCALAPPDATA%\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe"
    r"\LocalState\settings.json")


def _wt_profiles():
    """读 wt 的 settings.json(允许 // 注释, 得先剥掉)。"""
    try:
        t = io.open(WT_SETTINGS, encoding="utf-8-sig").read()
        return json.loads(re.sub(r"^\s*//.*$", "", t, flags=re.M))
    except Exception:
        return {}


def wt_cmd_profile():
    """找那个"命令提示符" profile 的 guid —— 就是你平时手动开 cmd 用的那个。

    按名字找不行(本机是中文"命令提示符"), 所以按 commandline 里是不是裸 cmd.exe 认,
    并排掉 Anaconda / VS 那几个带一长串激活参数的变体。
    """
    for x in (_wt_profiles().get("profiles") or {}).get("list") or []:
        cl = (x.get("commandline") or "").lower()
        if cl.endswith("cmd.exe") and "activate" not in cl:
            return x.get("guid") or x.get("name")
    return ""


def wt_default_profile():
    return _wt_profiles().get("defaultProfile") or ""


WT_CMD_PROFILE = wt_cmd_profile()
WT_PROFILE = WT_CMD_PROFILE or wt_default_profile()


def _find_window(title, timeout=8.0):
    """等到有个可见窗口的标题里含 title, 返回它的 hwnd。

    wt 是**单窗口多标签**, 窗口标题跟着当前活动标签走 —— 所以"标题匹配上了"
    同时证明了两件事: 新标签起来了, 而且它就在最前面。这正是往里敲字的前提。
    """
    import ctypes
    import ctypes.wintypes as wtypes
    u32 = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = []
        P = ctypes.WINFUNCTYPE(ctypes.c_bool, wtypes.HWND, wtypes.LPARAM)

        def cb(h, _):
            if u32.IsWindowVisible(h) and title in actions.window_title(h):
                found.append(h)
            return True

        u32.EnumWindows(P(cb), 0)
        if found:
            return found[0]
        time.sleep(0.2)
    return 0


def focus_win(w):
    """把一个窗口切到前台。IDE 里再往前走一步: 精确点到那个终端标签页。

    VS Code 的终端标签没有 HWND(一个 IDE 窗口里所有标签共用一个句柄), 所以 Windows
    这一层最多只能把 IDE 窗口切到前台。装了 vscode-bridge 那个扩展就不一样了 ——
    它用扩展 API 的 `Terminal.show()` 直接显示那个终端, 而它认的 `Terminal.processId`
    实测就等于我们记的 shell pid。桥不在(没装/没开/别的窗口占了端口)就安静退回原来
    的行为。
    """
    host = (w.get("owner") or w.get("term") or "").lower()
    # 句柄统一在这里解析一次, 而且**记过的也要验真**: 旧版 hook 会把 ConPTY 的
    # PseudoConsoleWindow(0x0 伪窗口)当窗口记下来, 对它 SetForegroundWindow 的效果
    # 落在宿主 WT 上 —— 也就是随便哪个当前活动的标签(实测: 点 A 的切过去, 前台变成
    # 无关的 B)。验不过就从活着的 claude 进程沿父链重找真窗口。
    hwnd = w.get("hwnd")
    if not actions.is_real_window(hwnd):
        hwnd = actions.window_for_pid(w.get("pid"))
    # 分流按**真窗口的实际主人**来, 不信记账里的 term —— 旧账把 WT 标签里的对话记成
    # "cmd.exe"(shell 挡在了宿主前面), 按那个字段走就会漏掉标签轮转。
    if hwnd:
        _, owner_name = actions.window_owner(hwnd)
        owner_name = (owner_name or "").lower()
        if owner_name == "windowsterminal.exe":
            host = "windowsterminal.exe"
        elif owner_name == "code.exe":
            host = "code.exe"

    if host == "code.exe" and w.get("shell_pid"):
        via = actions.bridge("/show", {"pid": w["shell_pid"]})
        if via and via.get("ok"):
            fg = actions.focus_window(hwnd)
            r = {"ok": True, "how": "vscode-bridge", "win": w,
                 "title": via.get("shown") or "", "tab": True,
                 "raised": bool(fg.get("ok"))}
            if not fg.get("ok"):
                r["why"] = ("标签页切过去了, 但 IDE 窗口没能提到前台(%s) — 点一下任务栏"
                            % (fg.get("why") or "没有窗口句柄"))
            return r
        if via:
            return {"ok": False, "win": w, "how": "vscode-bridge",
                    "why": via.get("why") or "桥说它找不到这个终端"}
    if not hwnd:
        return {"ok": False, "win": w,
                "why": "没记到窗口句柄(pid %s) — 请自己切过去" % w["pid"]}
    if host == "windowsterminal.exe":
        # WT 单窗口多标签共用一个 HWND: 光提前台, 活动的还是原来那个标签(你若有
        # 六个对话开在同一个窗口里, 六个"切过去"会全落在同一个标签上)。所以提前台
        # 之后按标签标题轮 Ctrl+Tab 找到它。
        # 标题**现场直接问那个 claude 进程的控制台**(AttachConsole), 不用记账里的 ——
        # 记账标题被"Stop 时刻抓到别人标签"污染过一整轮(点谁都切到同一个对话),
        # 而 GetConsoleTitleW 是权威来源, 谁的控制台谁答话, 不存在张冠李戴。
        want = actions.console_title_of(w.get("pid")) or w.get("title")
        r = actions.focus_wt_tab(hwnd, want)
        r["win"] = w
        if r.get("tab"):
            # 标题是唯一的定位手段, 所以**重名标签分不开** —— 同一窗口里有别的标签
            # 顶着同一个标题时(把同一对话开两份就会这样), 会选中先遇到的那个。
            # 重名从 UIA 顺手带回的全量标签名单里数, 零额外成本(曾经每个候选起一个
            # 子进程去问标题, 一次 focus 拖到 3.5 秒)。
            core = actions._title_core(want)
            dups = sum(1 for t in (r.get("all_tabs") or [])
                       if core and core in actions._title_core(t)) - 1
            if dups > 0:
                r["why"] = ("这个窗口里还有 %d 个标签顶着同样的标题, 可能停在了别的"
                            "同名对话上 — 标题是唯一的定位手段, 重名分不开" % dups)
            r.pop("all_tabs", None)
        return r
    r = actions.focus_window(hwnd)
    r["win"] = w
    if r.get("ok") and host == "code.exe":
        r["why"] = ("切到了 VS Code 窗口, 但到不了具体哪个标签页 —— "
                    "装上 vscode-bridge 扩展就能精确点到")
    return r


def windows_of(sid):
    """某一个对话此刻开着的窗口。给 resume / 切过去 / 关闭 三个动作共用。"""
    rec = load_states().get(sid) or {}
    return rec, live_windows(rec, alive_pids(all_pids({sid: rec})))


def do_resume(sid, cwd, terminal="type", prefer_existing=True, dry_run=False):
    """在终端里 resume。默认**照着你手动开 cmd 的样子来**。

    模式:
      type   (默认) 开一个**纯 cmd 标签页**(用 wt 的"命令提示符" profile, 不带任何
             commandline —— 和你按 Ctrl+Shift+T 开出来的一模一样), 等它就绪后把
             `claude --resume <id>` 一个字一个字敲进去再回车。
             这么绕是因为: 直接 `wt ... cmd /k claude --resume <id>` 把命令挂在
             profile 上, 出来的 claude 界面是单色的; 而从一个普通 cmd 提示符里
             敲进去, 和你自己敲完全等价, 颜色就正常。
      dock   并进当前 wt 窗口开新标签页, 但命令直接挂在 profile 上(不敲键盘)。
      new    同 dock, 但开一个独立新窗口。
      conhost 兜底: 本机"默认终端"仍是旧版 conhost, 所以这条走 `start cmd`。

    type 模式会**抢一下焦点**(要敲键盘)。actions.type_into_window 里有硬约束:
    切不到目标窗口就直接放弃, 绝不对着别的窗口乱敲。

    prefer_existing(默认开): 先查这个对话是不是已经开着 —— 开着一个就直接切过去
    不再新开; 开着两个以上直接拒绝并把它们列出来, 让你先关到只剩一个。
    """
    if not cwd or not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")

    # 已经开着的窗口优先 —— 同一个对话被 resume 进两个窗口时, 两边写同一份 jsonl,
    # 后写的会覆盖先写的。所以这里不是"体贴", 是防数据互相踩。
    if prefer_existing:
        _, wins = windows_of(sid)
        if len(wins) > 1:
            return {"ok": False, "conflict": True, "wins": wins,
                    "why": "这个对话已经开在 %d 个窗口里了 —— 先关到只剩一个"
                           % len(wins)}
        if len(wins) == 1:
            r = focus_win(wins[0])
            r["switched"] = True
            return r

    title = "claude %s" % sid[:8]
    line = "claude --resume %s" % sid
    if dry_run:                            # 测试用: 只回报要做什么, 不真的开窗口
        # dry-run 必须**没有任何副作用** —— 包括不去改 ~/.claude.json 的信任位
        return {"ok": True, "dry": True, "cwd": cwd, "terminal": terminal,
                "would_trust": bool(config.AUTO_TRUST),
                "cmd": "cd /d %s && %s" % (cwd, line)}

    # 新窗口起来之前先把目录标成已信任, 否则第一屏是 trust 对话框, 敲进去的
    # `claude --resume` 会卡在那儿等你按 y。(切到已有窗口那条路不需要, 它早就信任过了。)
    trusted = actions.trust_folder(cwd) if config.AUTO_TRUST else None

    if terminal.startswith("vscode"):
        # 在 VS Code 里开一个新终端标签并把命令敲进去 —— 走扩展的 createTerminal +
        # sendText, 不抢焦点也不会敲错窗口。桥不在(没装扩展 / VS Code 没开)就自动
        # 退回终端那条路, 并在返回里说清楚为什么。
        via = actions.bridge("/new", {"cwd": cwd, "cmd": line, "name": title}, timeout=10, side_effect=True)
        if via and via.get("ok"):
            # createTerminal + show() 只是在 VS Code **内部**把新终端设为活动标签,
            # IDE 窗口本身还留在你身后 —— 和 Terminal.show() 在 focus 里的坑一模一样,
            # 提前台永远是我们自己的事。窗口从新终端的 shell pid 沿父链找。
            fg = {}
            if via.get("pid"):
                hwnd = actions.window_for_pid(via["pid"])
                if hwnd:
                    fg = actions.focus_window(hwnd)
            r = {"ok": True, "cwd": cwd, "terminal": "vscode", "trusted": trusted,
                 "shell_pid": via.get("pid"), "tab": via.get("name"),
                 "raised": bool(fg.get("ok")),
                 "cmd": "cd /d %s && %s" % (cwd, line)}
            if not fg.get("ok"):
                r["why"] = "已在 VS Code 开好终端, 但 IDE 窗口没能提到前台 — 点一下任务栏"
            return r
        if via and via.get("timeout"):        # 桥慢 ≠ 桥不在: 终端可能已经开了, 再退回 Windows Terminal 就成了两个窗口跑同一个对话
            return {"ok": False, "cwd": cwd, "why": via["why"]}
        fell_back = (via or {}).get("why") or "没找到 VS Code 桥(扩展没装? VS Code 没开?)"
        terminal = "type"                     # 退回原来的做法
    else:
        fell_back = None

    if terminal in ("type", "type-new") and WT_EXE:
        args = [WT_EXE, "-w", "0" if terminal == "type" else "new",
                "new-tab", "--title", title]
        if WT_CMD_PROFILE:
            args += ["-p", WT_CMD_PROFILE]
        args += ["-d", cwd]                    # 注意: 不给 commandline
        subprocess.Popen(args)
        hwnd = _find_window(title)
        if not hwnd:
            return {"ok": False, "why": "新标签页没起来(等了 8 秒没等到标题)",
                    "cmd": "cd /d %s && %s" % (cwd, line)}
        time.sleep(0.45)                       # cmd 画完提示符再敲, 否则会掉字
        r = actions.type_into_window(hwnd, line, press_enter=True)
        if not r.get("ok"):
            return {"ok": False, "why": r.get("why"), "terminal": "wt/type",
                    "cmd": "cd /d %s && %s" % (cwd, line)}
        return {"ok": True, "cwd": cwd, "terminal": "wt/type", "trusted": trusted,
                "fell_back": fell_back, "cmd": "cd /d %s && %s" % (cwd, line)}

    if terminal != "conhost" and WT_EXE:
        args = [WT_EXE, "-w", "0" if terminal == "dock" else "new", "new-tab",
                "--title", title]
        if WT_PROFILE:
            args += ["-p", WT_PROFILE]
        args += ["-d", cwd, "cmd", "/k", line]
        subprocess.Popen(args)
        used = "wt/" + terminal
    else:
        subprocess.Popen('start "%s" cmd /k %s' % (title, line),
                         cwd=cwd, shell=True,
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        used = "conhost"

    return {"ok": True, "cwd": cwd, "terminal": used, "trusted": trusted,
            "fell_back": fell_back, "cmd": "cd /d %s && %s" % (cwd, line)}


# ---------------------------------------------------------------- HTTP

TS_OWNER = (config._cfg.get("ts_owner") or "<未配置 ts_owner, 拒绝转发>").lower()   # tailscale serve 转发时只认本人
TS_HOST = config._cfg.get("ts_host") or "ts-host.invalid"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _denied(self, post=False):
        """2026-10-04 手机版: `tailscale serve` 从 127.0.0.1 转发 tailnet 请求 —— 只认本人 Tailscale 账号;
        浏览器跨站 POST 一律拒(防别的网页借身份往对话里打字 / 开对话 / 关进程)。"""
        login = self.headers.get("Tailscale-User-Login")
        if (login is not None or self.headers.get("X-Forwarded-For")) and (login or "").lower() != TS_OWNER:
            return "只认本人 Tailscale 账号"
        if post:
            o = self.headers.get("Origin")
            if o and not re.fullmatch(r"https?://(127\.0\.0\.1|localhost|\[::1\]|%s)(:\d+)?" % re.escape(TS_HOST), o):
                return "拒绝跨站请求: " + o
        return None

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        why = self._denied()
        if why:
            return self._send(403, {"error": why})

        if u.path in ("/icon.svg", "/icon-180.png", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/favicon.ico"):   # 10-04 图标
            fn = "icon.svg" if u.path in ("/icon.svg", "/favicon.ico") else "icon-180.png"
            with open(os.path.join(HERE, fn), "rb") as fh:
                return self._send(200, fh.read(), "image/svg+xml" if fn.endswith(".svg") else "image/png")
        if u.path == "/manifest.webmanifest":
            return self._send(200, {"name": "Claude 对话管理器", "short_name": "对话", "start_url": "/m", "display": "standalone",
                                    "background_color": "#f6f8fb", "theme_color": "#4a8bd6",
                                    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"},
                                              {"src": "/icon-180.png", "sizes": "180x180", "type": "image/png"}]},
                              "application/manifest+json; charset=utf-8")

        mobile = u.path in ("/m", "/m.html") or (u.path in ("/", "/index.html") and "desktop" not in q
                 and re.search(r"iPhone|Android.*Mobile|Mobile Safari", self.headers.get("User-Agent") or ""))
        if mobile:                                    # 10-04 用户「对话管理器也应该有手机版」: 手机打开首页直接给手机版(?desktop=1 看桌面版)
            with io.open(os.path.join(HERE, "m.html"), encoding="utf-8") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")

        if u.path in ("/", "/index.html"):
            try:
                with io.open(os.path.join(HERE, "index.html"), encoding="utf-8") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            except Exception as e:
                return self._send(500, "index.html 读不到: %s" % e, "text/plain; charset=utf-8")

        if u.path == "/md.js":                               # 与本机其他看板共用的 Markdown 渲染(只维护一份, 用户 10-04「像 claude 控制台显示相应的字体」)
            try:
                with io.open(config._cfg.get("md_js") or os.path.join(HERE, "md.js"), encoding="utf-8") as fh:
                    return self._send(200, fh.read(), "text/javascript; charset=utf-8")
            except Exception as e:
                return self._send(404, "md.js 读不到: %s" % e, "text/plain; charset=utf-8")

        if u.path == "/icons.js":                            # 10-04: 状态小图标(管理器与手机版共用)
            with io.open(os.path.join(HERE, "icons.js"), encoding="utf-8") as fh:
                return self._send(200, fh.read(), "text/javascript; charset=utf-8")

        if u.path == "/xref.js":                             # 10-04: 对话文字自动链接(编号/网址/路径/缩写)
            with io.open(os.path.join(HERE, "xref.js"), encoding="utf-8") as fh:
                return self._send(200, fh.read(), "text/javascript; charset=utf-8")

        if u.path == "/api/xref/rules":
            return self._send(200, xref_rules())

        if u.path == "/api/xref/hover":
            try:
                r = config._cfg.get("xref", [])[int(q.get("r", ["-1"])[0])]
            except (IndexError, ValueError):
                return self._send(404, {"error": "no rule"})
            k = q.get("k", [""])[0]
            if not r.get("hover") or not re.fullmatch(r.get("re", ""), k):
                return self._send(404, {"error": "no match"})
            return self._send(200, xref_fetch(r["hover"].replace("{0}", k)))

        if u.path == "/api/xref/path":
            return self._send(200, path_info(q.get("p", [""])[0], q.get("cwd", [""])[0]))

        if u.path == "/api/sessions":
            t0 = time.time()
            limit = int(q.get("limit", ["150"])[0])
            rows, total = list_sessions(limit)
            threading.Thread(target=save_cache, daemon=True).start()
            return self._send(200, {"rows": rows, "total": total,
                                    "ms": int((time.time() - t0) * 1000)})

        if u.path == "/api/grep":
            t0 = time.time()
            kw = q.get("q", [""])[0]
            n = int(q.get("n", ["400"])[0])
            if not kw.strip():
                return self._send(200, {"rows": [], "scanned": 0, "total": 0, "ms": 0})
            rows, scanned, n_all = grep_sessions(kw, n)
            return self._send(200, {"rows": rows, "scanned": scanned, "total": n_all,
                                    "ms": int((time.time() - t0) * 1000)})

        if u.path == "/api/status/stream":            # SSE: 状态一变就推(浏览器不再 2 s 轮询)
            if not turn_push:
                return self._send(404, {"error": "没有 turn_push"})
            return turn_push.sse(self, turn_push.watcher(extra=(STATE_DIR, os.path.join(STATE_DIR, "outline"))), event="status")

        if u.path == "/api/status":
            t0 = time.time()
            m = status_map()
            live = {k: v for k, v in m.items() if v["state"] != "closed"}
            return self._send(200, {"status": m, "live": len(live),
                                    "now": time.time(),
                                    "ms": int((time.time() - t0) * 1000)})

        if u.path == "/file":
            fp = q.get("path", [""])[0]
            if not fp:
                return self._send(400, "缺 path", "text/plain; charset=utf-8")
            return self._send(200, preview_html(fp) + JUMP_HTML, "text/html; charset=utf-8")

        if u.path == "/reveal":
            fp = q.get("path", [""])[0]
            return self._send(200, reveal(fp))

        if u.path == "/api/advice":
            sid = q.get("id", [""])[0]
            return self._send(200, {"rec": advisor.cached(sid),
                                    "model": advisor.MODEL,
                                    "hist": advisor.hist()})

        if u.path == "/api/tree/stream":              # 10-04 A: 单对话视图实时镜像 —— 这个会话的 jsonl 一写就推(浏览器收到再拉 /api/tree)
            return tree_sse(self, q.get("id", [""])[0])

        if u.path == "/api/tree":
            sid = q.get("id", [""])[0]
            d = conv_tree(sid)
            if d is None:
                return self._send(404, {"error": "not found"})
            import outline                            # 10-04: 话题脉络(小折叠树), 由 Stop 钩子后台更新
            d["outline"] = outline.load(sid)
            d["story"] = outline.handoff_story(d.get("turns") or [])   # 接手对话: 交接文件里的来龙去脉原文
            return self._send(200, d)

        if u.path == "/replay":
            try:
                with io.open(os.path.join(HERE, "replay.html"), encoding="utf-8") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            except Exception as e:
                return self._send(500, "replay.html 读不到: %s" % e, "text/plain; charset=utf-8")

        if u.path == "/api/replay":
            sid = q.get("id", [""])[0]
            p = find_transcript(sid)
            if not p:
                return self._send(404, {"error": "not found"})
            return self._send(200, replay.events(
                p, int(q.get("from", ["0"])[0]), int(q.get("n", ["200"])[0]),
                q.get("kinds", [""])[0], q.get("q", [""])[0], q.get("kw", [""])[0],
                q.get("nosys", ["0"])[0] == "1"))

        if u.path == "/api/starred":
            return self._send(200, replay.starred(PROJ, q.get("kw", [""])[0]))

        if u.path == "/api/outline":
            sid = q.get("id", [""])[0]
            p = find_transcript(sid)
            if not p:
                return self._send(404, {"error": "not found"})
            d = replay.outline(p, q.get("kw", [""])[0], q.get("nosys", ["0"])[0] == "1")
            d["path"] = p
            return self._send(200, d)

        return self._send(404, {"error": "no route"})

    def do_POST(self):
        u = urlparse(self.path)
        why = self._denied(post=True)
        if why:
            return self._send(403, {"ok": False, "why": why})
        ln = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(ln).decode("utf-8")) if ln else {}
        except Exception:
            data = {}

        if u.path == "/api/new":                      # 10-04: ＋ 新对话
            r = new_session(data.get("cwd", ""), data.get("text", ""), data.get("where") or "vscode")
            return self._send(200 if r.get("ok") else 409, r)

        if u.path == "/api/paste_image":              # 10-04 用户「应该支持粘贴图片（像命令行）」: 存成文件, 消息里带路径, claude 用 Read 看图
            import base64
            m = re.match(r"data:image/(png|jpeg|jpg|gif|webp);base64,(.*)$", data.get("data") or "", re.S)
            if not m:
                return self._send(400, {"ok": False, "why": "只收 png/jpeg/gif/webp 的 dataURL"})
            raw = base64.b64decode(m.group(2))
            if len(raw) > 20 * 1024 * 1024:
                return self._send(413, {"ok": False, "why": "图片超过 20 MB"})
            d = os.path.join(HERE, "uploads", time.strftime("%Y-%m-%d"))
            os.makedirs(d, exist_ok=True)
            ext = "jpg" if m.group(1) == "jpeg" else m.group(1)
            p = os.path.join(d, "paste_%s_%03d.%s" % (time.strftime("%H%M%S"), int(time.time() * 1000) % 1000, ext))
            with open(p, "wb") as fh:
                fh.write(raw)
            return self._send(200, {"ok": True, "path": p.replace("\\", "/"), "bytes": len(raw)})

        if u.path == "/api/send":                     # 10-04 A: 网页输入框 → 该会话所在终端(空闲) / 排队(在跑, 需配 relay)
            try:
                r = send_to(data.get("id", ""), data.get("text", ""))
            except ValueError as e:
                r = {"ok": False, "why": str(e)}
            return self._send(200 if r.get("ok") else 409, r)

        if u.path == "/api/unsend":                   # 10-04 撤回排队中的消息(已打进终端的撤不回; 前端在打字前另有几秒反悔窗)
            R = _relay()
            if not R or not hasattr(R, "unqueue"):
                return self._send(409, {"ok": False, "why": "没配 relay, 没有排队这回事"})
            ok, why = R.unqueue(str(data.get("id", "")), str(data.get("mid", "")))
            return self._send(200 if ok else 409, {"ok": ok, "why": why})

        if u.path == "/api/xref/open":                # 10-04: 路径链接 → 打开文件夹 / 在资源管理器里选中文件
            if self.headers.get("X-Desk-Client") != "1":
                return self._send(403, {"ok": False, "why": "缺 X-Desk-Client 头"})
            fp = os.path.normpath(os.path.expanduser(str(data.get("path") or "")))
            if not os.path.exists(fp):
                return self._send(404, {"ok": False, "why": "不存在"})
            if os.path.isdir(fp) and not data.get("select"):
                subprocess.Popen(["explorer", fp])
                return self._send(200, {"ok": True})
            return self._send(200, reveal(fp))

        if u.path == "/api/topics":                   # 10-04: 「生成/重写脉络」按钮 —— 后台跑(~30 s), 完成后 tree 流会推
            sid = data.get("id", "")
            if not re.fullmatch(r"[0-9a-f-]{36}", sid or "") or not find_transcript(sid):
                return self._send(404, {"ok": False, "why": "没有这个对话"})
            import outline
            if os.path.exists(outline.path_of(sid) + ".lock"):
                return self._send(200, {"ok": True, "why": "已经在生成"})
            args = [sid] + (["--force"] if data.get("force") else [])
            w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            subprocess.Popen([w if os.path.exists(w) else sys.executable, os.path.join(HERE, "outline.py")] + args, cwd=HERE,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self._send(200, {"ok": True, "why": "已开始, 约 30 秒"})

        if u.path == "/api/note":
            sid = data.get("id", "")
            if not sid:
                return self._send(400, {"error": "no id"})
            with _notes_lock:
                notes = load_notes()
                rec = notes.get(sid, {})
                for k in ("title", "note", "star"):
                    if k in data:
                        rec[k] = data[k]
                rec["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
                notes[sid] = rec
                save_notes(notes)
            if "title" in data:                       # 10-04: 手填标题 = 三处统一的标题, 同步写进 VS Code 标签; 清空就退回脉络标题
                import outline
                threading.Thread(target=outline.push_label, args=(sid, (outline.load(sid) or {}).get("title", "")), daemon=True).start()
            return self._send(200, {"ok": True})

        if u.path == "/api/retitle":
            sid = data.get("id", "")
            if not sid:
                return self._send(400, {"error": "no id"})
            try:
                # 并发点好几个 ↻ 时这里会被同时进来好几次 —— 每次都 insert 会让
                # sys.path 越长越离谱。gen 本身是线程安全的(titles.jsonl 有写锁)。
                _tp = os.path.join(HERE, "titler")
                if _tp not in sys.path:
                    sys.path.insert(0, _tp)
                import gen
                r = gen.retitle(sid)
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)[:300]})
            if r.get("ok"):
                _AT_CACHE["mtime"] = None          # 逼下次 load_auto_titles 重读
            return self._send(200, r)

        if u.path == "/api/advise":
            sid = data.get("id", "")
            if not sid:
                return self._send(400, {"error": "no id"})
            if not data.get("force"):
                rec = advisor.cached(sid)
                tree = conv_tree(sid)
                # 缓存是按"生成时对话有多少轮"记的; 一轮没多就直接用旧的
                if rec and tree and rec.get("turns") == (tree.get("total") or 0):
                    rec["ok"] = True
                    rec["from_cache"] = True
                    return self._send(200, rec)
            tree = conv_tree(sid)
            if tree is None:
                return self._send(404, {"ok": False, "error": "找不到这个对话"})
            try:
                return self._send(200, advisor.advise(sid, tree))
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)[:300]})

        if u.path == "/api/resume":
            sid = data.get("id", "")
            cwd = data.get("cwd", "")
            if not sid:
                return self._send(400, {"error": "no id"})
            return self._send(200, do_resume(
                sid, cwd, data.get("terminal", "type"),
                prefer_existing=data.get("prefer_existing", True),
                dry_run=bool(data.get("dry_run"))))

        if u.path == "/api/focus":
            # 切到已经开着的那个窗口。多个窗口时必须指明 pid。
            sid = data.get("id", "")
            if not sid:
                return self._send(400, {"error": "no id"})
            _, wins = windows_of(sid)
            if not wins:
                return self._send(200, {"ok": False, "why": "这个对话没有开着的窗口"})
            pid = data.get("pid")
            w = next((x for x in wins if x["pid"] == pid), None) if pid else (
                wins[0] if len(wins) == 1 else None)
            if w is None:
                return self._send(200, {"ok": False, "conflict": True, "wins": wins,
                                        "why": "开着 %d 个窗口, 要指明切哪一个" % len(wins)})
            return self._send(200, focus_win(w))

        if u.path == "/api/close":
            # 结束这个对话的进程。close_terminal 只在该终端窗口里没有别的已知对话时才做。
            sid = data.get("id", "")
            pid = data.get("pid")
            if not sid or not pid:
                return self._send(400, {"error": "need id + pid"})
            T0 = time.time(); ms = {}                 # 10-04 用户「管理器连标签页关闭，怎么这么久」: 各段计时回传
            states = load_states()
            alive = alive_pids(all_pids(states))
            rec = states.get(sid) or {}
            w = next((x for x in live_windows(rec, alive) if x["pid"] == pid), None)
            if w is None:
                return self._send(200, {"ok": False, "why": "这个 pid 不在该对话活着的窗口里(可能已经关了)"})
            ct = next((e.get("pid_ctime") for e in rec_procs(rec) if e.get("pid") == pid), None)
            # 这个终端窗口里还有别的对话吗? 有就绝不关窗 —— Windows Terminal 是
            # 单窗口多标签, 关窗会连带关掉别人。
            others = [x["pid"] for sid2, r2 in states.items()
                      for x in live_windows(r2, alive)
                      if x.get("term_pid") and x["term_pid"] == w.get("term_pid")
                      and x["pid"] != pid]
            want_term = bool(data.get("close_terminal", True))
            want_tab = bool(data.get("close_tab"))
            # 关标签页优先走桥: VS Code 自己 dispose() 掉的标签干干净净, 不会留下
            # "terminal process terminated with exit code" 那条提示(我们杀 shell
            # 是非零退出码)。桥不在就退回杀 shell, 结果一样只是多一条提示。
            ms["locate"] = int((time.time() - T0) * 1000)
            via = None
            if want_tab and w.get("shell_pid") and                     (w.get("owner") or w.get("term") or "").lower() == "code.exe":
                via = actions.bridge("/close", {"pid": w["shell_pid"]})
            ms["bridge"] = int((time.time() - T0) * 1000) - ms["locate"]
            r = actions.close_claude(pid, ct, hwnd=w.get("hwnd"),
                                     close_terminal=want_term and not others,
                                     term_name=w.get("term"),
                                     kill_shell=want_tab and not (via and via.get("ok")),
                                     reset=not (via and via.get("ok")))
            ms["kill"] = int((time.time() - T0) * 1000) - ms["locate"] - ms["bridge"]
            r["ms"] = ms
            if via and via.get("ok"):
                r["tab_closed"] = "vscode-bridge"
            r["siblings"] = len(others)
            if others and want_term:
                r["note"] = ("这个终端窗口里还开着 %d 个别的对话, 所以只结束了这一个, "
                             "窗口留着" % len(others))
            elif r.get("shell_why"):
                r["note"] = r["shell_why"]
            elif r.get("tab_closed"):
                r["note"] = "标签页由 VS Code 自己关掉了(干净, 没有退出码提示)"
            elif r.get("shell_killed"):
                r["note"] = "连它所在的终端标签页一起关了"
            elif r.get("term_kept"):
                r["note"] = r["term_kept"]
            return self._send(200, r)

        return self._send(404, {"error": "no route"})


def main():
    load_cache()
    prune_states()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    # 环回**双栈**监听(2026-08-29 实测, 另一个本地服务 8080 上量到的同一个缺陷):
    # 本机 `localhost` 解析出 **::1 排在 127.0.0.1 前面**, 只绑 IPv4 时每个新连接
    # 都要先试 IPv6 失败再回落 —— 实测 python urlopen 2.04s / 浏览器导航
    # connect 303ms、ttfb 311ms; 补上 ::1 之后 ttfb 6.8ms(45x), 连接数 110→1。
    # 只加环回地址, **暴露面不变**。Windows 默认 IPV6_V6ONLY=1, 两个套接字互不抢端口。
    try:
        import socket as _sk6, threading as _th6
        class _Srv6(ThreadingHTTPServer):
            address_family = _sk6.AF_INET6
        _th6.Thread(target=_Srv6(("::1", PORT), Handler).serve_forever,
                    name="http-v6", daemon=True).start()
    except Exception as _e6:
        print("IPv6 环回未监听(localhost 会慢 ~300ms): %s" % _e6)

    print("Claude 对话管理器  ->  http://localhost:%d/" % PORT)
    print("扫描目录: %s" % PROJ)
    print("注释存于: %s" % NOTES_PATH)
    print("终端: %s" % (WT_EXE or "(未找到 wt.exe, 回退旧版 conhost)"))
    print("cmd profile: %s" % (WT_CMD_PROFILE or "(没找到, 回退 defaultProfile)"))
    print("缓存: %d 个对话已解析" % len(_scan_cache))
    print("状态: %d 个会话有 hook 记账" % len(load_states()))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("bye")


if __name__ == "__main__":
    main()
