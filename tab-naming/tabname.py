# -*- coding: utf-8 -*-
"""交接链与 VS Code 终端标签命名(用户 2026-10-03):
  老窗口 → 「<话题> [k, <会话名>(<id 前 8 位>)]」, 新窗口 → 「<话题> [k+1-最新, <会话名>(<id 前 8 位>)]」;
  最新那个再交接时自己变成 [k+1, …], 新的是 [k+2-最新, …]。

数据: $CLAUDE_TAB_META(缺省 ~/.claude/tab_chains.json) = {"chains": {cid: {topic, members: [{sid, name, ver, ts}]}}, "pinned": {sid: {note, ts}}}
(其他工具可以读同一个文件, 比如做一个在线对话面板)。活会话来自 ~/.claude/sessions/<claude pid>.json; 终端 pid = claude 进程的父进程(shell),
与 VS Code 桥(8721-8728)的 Terminal.processId 相同。

  python tabname.py handoff --sid <旧会话 id> --topic <话题> --term-pid <新终端 shell pid>   (handoff.py 调用)
  python tabname.py claim --chain <cid> --ver <k> --term-pid <pid> --after <epoch>          (后台轮询: 等新会话出现再改名)
  python tabname.py refresh                                                                 (按登记把所有活着的成员标签重新改一遍)
  python tabname.py show
"""
import argparse, glob, json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path

META = Path(os.environ.get("CLAUDE_TAB_META") or (Path.home() / ".claude" / "tab_chains.json"))
SESS = Path.home() / ".claude" / "sessions"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load():
    try: return json.loads(META.read_text(encoding="utf-8"))
    except Exception: return {"chains": {}, "pinned": {}}


def save(d):
    tmp = META.with_suffix(".tmp"); tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8"); os.replace(tmp, META)


def alive(pid):
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        return True


def live_sessions():
    """{sid: {pid, name, status, cwd, startedAt, updatedAt, shell_pid}} —— 只要进程还在的。"""
    out = {}
    for f in SESS.glob("*.json"):
        try: d = json.loads(f.read_text(encoding="utf-8"))
        except Exception: continue
        sid, pid = d.get("sessionId"), d.get("pid")
        if not sid or not pid or not alive(pid): continue
        d["shell_pid"] = shell_pid(pid)
        out[sid] = d
    return out


def shell_pid(pid):
    try:
        import psutil
        return psutil.Process(int(pid)).ppid()
    except Exception:
        return None


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


def bridges():
    out = []
    for port in live_ports():
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/ping" % port, timeout=0.5) as r:
                if json.loads(r.read().decode("utf-8")).get("what") == "claude-sessions-bridge": out.append(port)
        except Exception: pass
    return out


def rename(term_pid, name):
    """10-04 起改控制台标题(同目录 tab_title.py): 不切标签、不闪, 等你时自动带「▶ 」。
    VS Code API 改过名的老标签是静态的, 控制台标题改不动它 —— 那种情况退回桥的 /rename(会切一下标签)。→ (ok, 说明)"""
    if not term_pid: return False, "没有终端 pid"
    sid = next((k for k, v in live_sessions().items() if v.get("shell_pid") == int(term_pid)), None)
    if sid:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import tab_title
        ok, info = tab_title.apply(sid, None, name)
        cur = term_name(int(term_pid)) or ""
        if ok and cur.replace(tab_title.MARK, "").strip() == name.strip(): return True, "控制台标题 → " + cur
        if not os.environ.get("TABNAME_API"): return False, "标签是 API 静态名(旧), 控制台标题改不动, 为免闪动不再用 API 改(当前 %s; 要强改设 TABNAME_API=1)" % cur
    if not os.environ.get("TABNAME_API"): return False, "终端 %s 下没有活着的 claude 会话" % term_pid
    return rename_api(term_pid, name)


def rename_api(term_pid, name):
    """旧法: 挨个桥问; 只有拥有该终端的那个窗口会 ok。会切一下标签(面板闪), 且之后标签变静态。→ (ok, 说明)"""
    why = []
    for port in bridges():
        req = urllib.request.Request("http://127.0.0.1:%d/rename" % port, method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"pid": int(term_pid), "name": name[:60]}).encode("utf-8"))
        try:
            with urllib.request.urlopen(req, timeout=3) as r: d = json.loads(r.read().decode("utf-8"))
        except Exception as e: why.append("%d:%r" % (port, e)); continue
        if d.get("ok"): return True, "port %d: %s → %s" % (port, d.get("from"), d.get("to"))
        why.append("%d:%s" % (port, d.get("why")))
    return False, "; ".join(why) or "没有桥"


def label(topic, ver, latest, name, sid):
    return "%s [%s%s, %s(%s)]" % (topic, ver, "-最新" if latest else "", name or "?", (sid or "")[:8])


def chain_of(d, sid):
    for cid, c in d["chains"].items():
        for m in c["members"]:
            if m["sid"] == sid: return cid, c, m
    return None, None, None


def cmd_handoff(a):
    d = load(); live = live_sessions()
    me = live.get(a.sid) or {}
    cid, c, m = chain_of(d, a.sid)
    if c is None:
        cid = "c" + time.strftime("%Y%m%d%H%M%S")
        c = d["chains"][cid] = {"topic": a.topic, "members": []}
        m = {"sid": a.sid, "name": me.get("name"), "ver": 0, "ts": time.time()}; c["members"].append(m)
    elif a.topic and a.topic != c["topic"]:
        c["topic"] = a.topic                                         # 话题可以随交接收窄/改写, 链不变
    m["name"] = m.get("name") or me.get("name")
    save(d)
    ok, info = rename(me.get("shell_pid"), label(c["topic"], m["ver"], False, m["name"], a.sid))
    print("老窗口改名:", ok, info)
    nv = max(x["ver"] for x in c["members"]) + 1
    if a.term_pid:                                                   # 新会话还没起来: 后台等它写出 sessions/<pid>.json 再改名
        pyw = Path(sys.executable).with_name("pythonw.exe")
        subprocess.Popen([str(pyw if pyw.exists() else sys.executable), __file__, "claim", "--chain", cid, "--ver", str(nv),
                          "--term-pid", str(a.term_pid), "--after", str(time.time() - 5), "--old-sid", a.sid],
                         creationflags=NOWIN | getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)
        ok2, info2 = rename(a.term_pid, label(c["topic"], nv, True, "启动中", ""))
        print("新窗口先改名(待认领):", ok2, info2)
    print("链 %s 话题「%s」, 新会话版本 %d" % (cid, c["topic"], nv))


def cmd_claim(a):
    deadline = time.time() + 180
    log = META.with_name("tab_chains.log")
    while time.time() < deadline:
        for sid, s in live_sessions().items():
            if s.get("shell_pid") == a.term_pid and (s.get("startedAt") or 0) / 1000 >= a.after:
                d = load(); c = d["chains"].get(a.chain)
                if c is None: return
                if not any(x["sid"] == sid for x in c["members"]):
                    c["members"].append({"sid": sid, "name": s.get("name"), "ver": a.ver, "ts": time.time()}); save(d)
                ok, info = rename(a.term_pid, label(c["topic"], a.ver, True, s.get("name"), sid))
                with open(log, "a", encoding="utf-8") as f: f.write("%s claim %s v%d %s %s %s\n" % (time.strftime("%H:%M:%S"), a.chain, a.ver, sid, ok, info))
                if getattr(a, "old_sid", None): close_old(a.old_sid, sid, log)
                return
        time.sleep(2)
    with open(log, "a", encoding="utf-8") as f: f.write("%s claim %s v%d 超时: 终端 %s 下 180 s 内没有新会话\n" % (time.strftime("%H:%M:%S"), a.chain, a.ver, a.term_pid))


KEEP_OLD = Path.home() / ".claude" / "handoff_keep_old"      # 存在这个文件 = 交接后不关旧对话


def close_old(old_sid, new_sid, log):
    """用户 2026-10-04:「以后handoff后，自动关闭上一个旧的对话」。
    新会话认领后, 等旧会话把交接这一轮说完(状态回到 idle 并持续 8 s), 再经桥 /close 关旧终端标签(dispose, 进程随之结束)。
    旧会话 30 分钟内一直没空闲(用户还在旧窗口聊) → 不关; 新会话不在了 → 不关。旧会话记录仍在磁盘, 可 claude --resume 找回。"""
    def w(m):
        with open(log, "a", encoding="utf-8") as f: f.write("%s close_old %s %s\n" % (time.strftime("%H:%M:%S"), old_sid[:8], m))
    if KEEP_OLD.exists(): return w("跳过: 存在 %s" % KEEP_OLD)
    deadline = time.time() + 1800; idle_since = None; old = None
    while time.time() < deadline:
        live = live_sessions(); old = live.get(old_sid)
        if not old: return w("旧会话已不在, 不用关")
        if new_sid not in live: return w("新会话 %s 不在了, 不关旧的" % new_sid[:8])
        if old.get("status") == "idle":
            idle_since = idle_since or time.time()
            if time.time() - idle_since >= 8: break
        else:
            idle_since = None
        time.sleep(2)
    else:
        return w("30 分钟内旧会话一直在忙, 不关")
    sp = old.get("shell_pid")
    for port in bridges():
        req = urllib.request.Request("http://127.0.0.1:%d/close" % port, data=json.dumps({"pid": sp}).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=4) as r: d = json.loads(r.read().decode("utf-8"))
        except Exception as ex: d = {"ok": False, "why": repr(ex)}
        if d.get("ok"): return w("已关闭终端 %s (port %d): %s" % (sp, port, d.get("closed")))
    w("没有桥认领终端 %s, 未关" % sp)


def cmd_refresh(a):
    d = load(); live = live_sessions(); n = 0
    for cid, c in d["chains"].items():
        top = max(x["ver"] for x in c["members"])
        for m in c["members"]:
            s = live.get(m["sid"])
            if not s: continue
            m["name"] = s.get("name") or m.get("name")
            ok, info = rename(s.get("shell_pid"), label(c["topic"], m["ver"], m["ver"] == top, m["name"], m["sid"])); n += ok
            print(cid, m["ver"], m["name"], ok, info)
    save(d); print("改名成功", n)


GENERIC = {"claude", "claude handoff", "claude code", "powershell", "pwsh", "cmd", "bash", "node", ""}


def term_name(term_pid):
    for port in bridges():
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/terminals" % port, timeout=2) as r:
                for t in json.loads(r.read().decode("utf-8")).get("terminals") or []:
                    if t.get("pid") == term_pid: return t.get("name") or ""
        except Exception: pass
    return None


def strip_spin(n):
    """Claude Code 的自动标题 = 转圈符(✳◐◑◒◓⠂… 等非字母数字) + 空格 + 标题。"""
    i = 0
    while i < len(n) and not (n[i].isalnum() or "一" <= n[i] <= "鿿"): i += 1
    return n[i:].strip()


def cmd_autotitle(a):
    """用户 2026-10-03: 新对话第一次提问后, 把 Claude 自动生成的窗口标题加上「[会话名(id8)]」, 暂不写版本号。
    已在交接链上(由 handoff 命名)或标签里已有「[」的不动。等最多 120 s 让 Claude 生成标题。"""
    log = META.with_name("tab_chains.log")
    def note(m):
        with open(log, "a", encoding="utf-8") as f: f.write("%s autotitle %s %s\n" % (time.strftime("%H:%M:%S"), a.sid[:8], m))
    d = load()
    if chain_of(d, a.sid)[0]: return note("在交接链上, 跳过")
    deadline = time.time() + 120; s = None
    while time.time() < deadline:
        s = live_sessions().get(a.sid)
        if s and s.get("shell_pid") and user_msgs(a.sid, n=1):
            n = term_name(s["shell_pid"])
            if n is None: return note("没有 VS Code 桥认领这个终端(不在 VS Code 里?), 跳过")
            if "[" in n and strip_spin(n.split(" [")[0].replace("▶", "")).lower() not in GENERIC and not n.replace("▶", "").strip().startswith("～"): return note("标签已有名字, 跳过: " + n)   # ～开头 = 提问时起的临时名, 照样换
            t, src = best_title(a.sid, n)
            if t and t not in ("未命名对话",):
                ok, info = rename(s["shell_pid"], "%s [%s(%s)]" % (t[:40], s.get("name") or "?", a.sid[:8]))
                return note("%s %s %s" % (src, ok, info))
        time.sleep(3)
    note("120 s 内没等到 Claude 生成的标题(最后: %s)" % (term_name(s["shell_pid"]) if s and s.get("shell_pid") else "无会话文件"))


def first_prompt(sid):
    """会话第一条真人发言(字符串内容), 读 jsonl 头部。"""
    hits = list((Path.home() / ".claude" / "projects").glob("*/%s.jsonl" % sid))
    if not hits: return ""
    try:
        with open(hits[0], encoding="utf-8", errors="replace") as f:
            for i, l in enumerate(f):
                if i > 400: break
                try: r = json.loads(l)
                except Exception: continue
                if r.get("type") != "user" or r.get("isMeta"): continue
                c = (r.get("message") or {}).get("content")
                if isinstance(c, list): c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<"): return c.strip()
    except Exception: pass
    return ""


def handoff_title(prompt):
    """「请读取 …HANDOFF_START_x.md …」→ 起始指令里的交接文件 → 其 H1 去掉「交接 <时间> ——」前缀。"""
    import re
    m = re.search(r"([A-Za-z]:[^\s\"']*HANDOFF_START_[0-9_]+\.md)", prompt)
    if not m: return ""
    try:
        st = Path(m.group(1)).read_text(encoding="utf-8")
        h = re.search(r"交接文件：`([^`]+)`", st)
        if not h: return ""
        h1 = next((l for l in Path(h.group(1)).read_text(encoding="utf-8").splitlines() if l.startswith("# ")), "")
        t = re.sub(r"^#\s*(交接|HANDOFF)\s*[0-9:\- _]*", "", h1).strip(" —-")
        t = re.sub(r"^.*?——\s*", "", t) if "——" in t else t
        t = re.sub(r"[（(]会话 ?[0-9a-f]{8}[^）)]*[）)]", "", t)          # 去「（会话 de177373，…）」
        t = re.sub(r"^会话 ?[0-9a-f]{8}\s*[（(]?", "", t).strip()       # 去开头「会话 7bfac852（」
        t = re.sub(r"^[：:\s]+", "", t).rstrip("）) ")
        return t[:28]
    except Exception:
        return ""


TITLER = Path(__file__).resolve().parent                         # 空 mcp / settings 在本目录; `claude -p` 的 cwd 也钉在这里(一次性会话落在本目录的 project 下, 不混进你的对话列表)
TAB_PROMPT = Path(__file__).with_name("tab_prompt.txt")


def user_msgs(sid, n=15, each=200):
    """真人发言(含斜杠命令的参数, 如 /goal 的目标), 去掉系统注入。"""
    hits = list((Path.home() / ".claude" / "projects").glob("*/%s.jsonl" % sid))
    out = []
    if not hits: return out
    try:
        with open(hits[0], encoding="utf-8", errors="replace") as f:
            for l in f:
                try: r = json.loads(l)
                except Exception: continue
                if r.get("type") != "user" or r.get("isMeta"): continue
                c = (r.get("message") or {}).get("content")
                if isinstance(c, list): c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                if not isinstance(c, str): continue
                c = c.strip()
                if "<command-name>" in c[:300]:                       # 斜杠命令记录有时以 <command-message> 开头
                    m = re.search(r"<command-args>(.*?)</command-args>", c, re.S)
                    cn = re.search(r"<command-name>(.*?)</command-name>", c)
                    c = ((cn.group(1) + " ") if cn else "") + (m.group(1).strip() if m else "")
                    c = c.strip()                                          # 不带参数的斜杠命令(/material-edge)也留着: 配合第一轮回答能起名(10-04)
                elif c.startswith("<") or c.startswith("[Request interrupted") or c.startswith("Caveat:"): continue
                if "HANDOFF_START_" in c:                                      # 交接起始句无信息: 换成交接文件标题 + 第 6 节第一项
                    ht = handoff_title(c); nx = handoff_next(c)
                    c = "接手交接: %s%s" % (ht, ("; 下一步: " + nx) if nx else "") if (ht or nx) else c
                if len(c) >= 3: out.append(c[:each])
                if len(out) >= n: break
    except Exception: pass
    return out


def first_reply(sid, each=800):
    """第一轮回答的最后一段文字(本轮结束 turn_duration 之前最后一条助手文字)。10-04 用户:「应该在第一次回答结束的时候，才去更新标题」——
    第一句常是「请读取 X」「另外这里…」, 只看提问起不出名字; 第一轮回答说清了在干什么。"""
    hits = list((Path.home() / ".claude" / "projects").glob("*/%s.jsonl" % sid))
    last = ""
    if not hits: return ""
    try:
        with open(hits[0], encoding="utf-8", errors="replace") as f:
            for l in f:
                try: r = json.loads(l)
                except Exception: continue
                if r.get("isSidechain"): continue
                if r.get("type") == "assistant":
                    c = (r.get("message") or {}).get("content")
                    if isinstance(c, list):
                        t = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text").strip()
                        if t: last = t
                elif r.get("type") == "system" and r.get("subtype") == "turn_duration" and last: break
    except Exception: pass
    return last[:each]


def concise_title(sid, timeout=90):
    """claude -p --model haiku 按 tab_prompt.txt 起 ≤12 字标签名; 失败返回 ""。约 20 s(CLI 冷启动)。"""
    msgs = user_msgs(sid)
    if not msgs: return ""
    p = TAB_PROMPT.read_text(encoding="utf-8") + chr(10) + chr(10).join("- " + m.replace(chr(10), " ") for m in msgs)
    rep = first_reply(sid)
    if rep: p += chr(10) + chr(10) + "助手第一次回答(节选, 用来判断这个对话实际在干什么):" + chr(10) + rep.replace(chr(10), " ")
    cmd = ["claude", "-p", "--model", "haiku", "--strict-mcp-config", "--mcp-config", str(TITLER / "empty_mcp.json"),
           "--settings", str(TITLER / "empty_settings.json"), "--system-prompt", "你是一个标签名生成器。只输出标签名本身，不做任何其他事，不使用任何工具。"]
    try:
        r = subprocess.run(cmd, input=p.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                           cwd=str(TITLER if TITLER.exists() else Path.home()), creationflags=NOWIN)
    except Exception:
        return ""
    t = r.stdout.decode("utf-8", "replace").split(chr(10))[0].strip().strip('"“”「」\'。. ')
    t = t.replace("（", "(").replace("）", ")")
    return t[:24] if r.returncode == 0 else ""


def handoff_next(prompt):
    """交接文件第 6 节(未决与下一步)第一条要点。"""
    m = re.search(r"([A-Za-z]:[^\s\"']*HANDOFF_START_[0-9_]+\.md)", prompt)
    if not m: return ""
    try:
        h = re.search(r"交接文件：`([^`]+)`", Path(m.group(1)).read_text(encoding="utf-8"))
        txt = Path(h.group(1)).read_text(encoding="utf-8") if h else ""
        sec = re.split(r"\n## ", txt)
        s6 = next((x for x in sec if x.startswith("6")), "")
        line = next((l for l in s6.splitlines()[1:] if l.strip().startswith(("1.", "-", "*"))), "")
        return re.sub(r"[*`]", "", line).strip(" -1.")[:120]
    except Exception:
        return ""


def best_title(sid, term_label):
    ct = concise_title(sid)
    if ct: return ct, "haiku 简名"
    t = strip_spin(term_label or "")
    if t and t.lower() not in GENERIC and "[" not in t: return t, "Claude 标题"
    fp = first_prompt(sid)
    ht = handoff_title(fp)
    if ht: return ht, "交接文件标题"
    fp = fp.replace(chr(10), " ")[:24].strip()
    return (fp, "第一句话") if fp else ("未命名对话", "兜底")


def cmd_nameall(a):
    """把所有活着的对话标签按规则改名: 交接链成员 → 链名(refresh); 其余 → 「<标题> [<会话名>(<id8>)]」, 已带「[」的不动(除非 --force)。"""
    d = load(); live = live_sessions(); n = 0
    inchain = {m["sid"] for c in d["chains"].values() for m in c["members"]}
    cmd_refresh(a)
    for sid, s in live.items():
        if sid in inchain or not s.get("shell_pid"): continue
        cur = term_name(s["shell_pid"])
        if cur is None: print("不在 VS Code:", s.get("name"), sid[:8]); continue
        if "[" in cur and not a.force: print("已命名:", cur); continue
        t, src = best_title(sid, cur.split(" [")[0] if a.force else cur)
        ok, info = rename(s["shell_pid"], "%s [%s(%s)]" % (t[:40], s.get("name") or "?", sid[:8])); n += ok
        print(src, ok, info)
    print("改名", n)


def cmd_show(a):
    d = load(); live = live_sessions()
    for cid, c in d["chains"].items():
        print(cid, c["topic"])
        for m in c["members"]: print("   v%d %s %s %s" % (m["ver"], m.get("name"), m["sid"], "活" if m["sid"] in live else "-"))
    print("重要:", d.get("pinned"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("handoff"); p.add_argument("--sid", required=True); p.add_argument("--topic", required=True); p.add_argument("--term-pid", type=int)
    p = sp.add_parser("claim"); p.add_argument("--chain", required=True); p.add_argument("--ver", type=int, required=True); p.add_argument("--term-pid", type=int, required=True); p.add_argument("--after", type=float, required=True); p.add_argument("--old-sid")
    sp.add_parser("refresh"); sp.add_parser("show")
    p = sp.add_parser("autotitle"); p.add_argument("--sid", required=True)
    p = sp.add_parser("nameall"); p.add_argument("--force", action="store_true")
    a = ap.parse_args()
    {"handoff": cmd_handoff, "claim": cmd_claim, "refresh": cmd_refresh, "show": cmd_show, "autotitle": cmd_autotitle, "nameall": cmd_nameall}[a.cmd](a)
