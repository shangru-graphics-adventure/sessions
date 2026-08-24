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
CACHE_PATH = os.path.join(HERE, "cache.json")
STATE_DIR = os.path.join(HERE, "state")     # hook_state.py 每会话写一个
PORT = config.PORT

MAX_TOPICS = 14           # 每个对话最多提取多少个"话题"
TOPIC_CHARS = 46          # 每个话题截多长
MAX_ARTIFACTS = 40        # 每个对话最多列多少个产物(超出只报数)
WHY_CHARS = 78            # "这文件怎么来的"截多长
SCAN_VER = 3              # 解析格式版本, 改了就让磁盘缓存整体失效重扫

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

def _text_of(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c
                       if isinstance(b, dict) and b.get("type") == "text")
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
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
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
                    if _is_real_user_text(t):
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
    for sid, rec in states.items():
        st = resolve_state(rec, alive)
        wins = live_windows(rec, alive)
        row = {
            "state": st,
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
        n = notes.get(sid, {})
        try:
            size = os.path.getsize(fp)
        except OSError:
            size = 0
        rows.append({
            "id": sid,
            "mtime": mt,
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
            "auto_title": autot.get(sid, ""),
            "note": n.get("note", ""),
            "star": bool(n.get("star")),
            "status": stat.get(sid),
        })
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
            "id": sid, "mtime": mt, "project": proj,
            "cwd": info.get("cwd") or "", "first": info.get("first") or "",
            "last": info.get("last") or "", "turns": info.get("turns") or 0,
            "topics": info.get("topics") or [],
            "kb": 0, "title": n.get("title", ""),
            "auto_title": autot.get(sid, ""), "note": n.get("note", ""),
            "star": bool(n.get("star")), "snippet": snippet,
        })
    return hits, len(files), n_all


def transcript(sid, n=40):
    """取某会话最后 n 条消息(人+AI 文本), 用于在页面里确认"是不是这个"。"""
    path = None
    for d in os.listdir(PROJ):
        fp = os.path.join(PROJ, d, sid + ".jsonl")
        if os.path.exists(fp):
            path = fp
            break
    if not path:
        return None
    out = []
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
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
                t = t.strip()
                if not t:
                    continue
                out.append({"role": ty, "text": _clean(t, 1200), "ts": d.get("timestamp", "")})
    except Exception:
        pass
    return out[-n:]


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
        via = actions.bridge("/new", {"cwd": cwd, "cmd": line, "name": title})
        if via and via.get("ok"):
            return {"ok": True, "cwd": cwd, "terminal": "vscode", "trusted": trusted,
                    "shell_pid": via.get("pid"), "tab": via.get("name"),
                    "cmd": "cd /d %s && %s" % (cwd, line)}
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

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            try:
                with io.open(os.path.join(HERE, "index.html"), encoding="utf-8") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            except Exception as e:
                return self._send(500, "index.html 读不到: %s" % e, "text/plain; charset=utf-8")

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
            return self._send(200, preview_html(fp), "text/html; charset=utf-8")

        if u.path == "/reveal":
            fp = q.get("path", [""])[0]
            return self._send(200, reveal(fp))

        if u.path == "/api/transcript":
            sid = q.get("id", [""])[0]
            msgs = transcript(sid, int(q.get("n", ["40"])[0]))
            if msgs is None:
                return self._send(404, {"error": "not found"})
            return self._send(200, {"msgs": msgs})

        return self._send(404, {"error": "no route"})

    def do_POST(self):
        u = urlparse(self.path)
        ln = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(ln).decode("utf-8")) if ln else {}
        except Exception:
            data = {}

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
            via = None
            if want_tab and w.get("shell_pid") and                     (w.get("owner") or w.get("term") or "").lower() == "code.exe":
                via = actions.bridge("/close", {"pid": w["shell_pid"]})
            r = actions.close_claude(pid, ct, hwnd=w.get("hwnd"),
                                     close_terminal=want_term and not others,
                                     term_name=w.get("term"),
                                     kill_shell=want_tab and not (via and via.get("ok")))
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
