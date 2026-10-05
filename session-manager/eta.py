"""对话报时(2026-10-04 用户「各个对话有较长计算的能否都给个时间预期，显示到对话管理器上：还有多长时间返回结果（全局规矩）」)。

  python eta.py set 12 "回放 30 天 × 4 并发" --basis "单日 1.5 分 × 30 ÷ 4 ≈ 11 分"
  python eta.py extend 10 "第 3 层比预期慢: 冷缓存"     # 追加, 必须写原因
  python eta.py done ["一句结果"]                       # 跑完; 记实际耗时
  python eta.py show                                   # 看本对话当前的钟

会话 id 取环境变量 CLAUDE_CODE_SESSION_ID(Claude Code 的工具 shell 都有), 没有就 --sid 指定。
写 state/eta/<sid>.json, 管理器(:8720)读它在左栏与状态框倒计时; done 时追加 state/eta_log.jsonl(预估 vs 实际, 供校准)。
同一对话同一时刻只有一个钟; 再 set 就覆盖(旧的记进日志, 标 replaced)。
"""
import argparse
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "state", "eta")
LOG = os.path.join(HERE, "state", "eta_log.jsonl")


def _sid(a):
    sid = a.sid or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if not sid:
        sys.exit("没有会话 id: 不在 Claude Code 的 shell 里就用 --sid <会话id>")
    return sid


def _path(sid):
    return os.path.join(DIR, sid + ".json")


def _load(sid):
    try:
        with io.open(_path(sid), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _save(sid, d):
    os.makedirs(DIR, exist_ok=True)
    tmp = _path(sid) + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    os.replace(tmp, _path(sid))


def _log(sid, d, how, note=""):
    now = time.time()
    rec = {"sid": sid, "what": d.get("what"), "basis": d.get("basis"), "est_s": round(d["end0"] - d["start"]),
           "final_est_s": round(d["end"] - d["start"]), "actual_s": round(now - d["start"]),
           "extends": d.get("extends", []), "how": how, "note": note,
           "start": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(d["start"]))}
    rec["ratio"] = round(rec["actual_s"] / rec["est_s"], 2) if rec["est_s"] else None
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with io.open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def main():
    ap = argparse.ArgumentParser(description="对话报时: 管理器显示「还有多久出结果」")
    ap.add_argument("--sid", default="")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("set", help="开钟: 预计 MIN 分钟后出结果")
    s.add_argument("min", type=float); s.add_argument("what"); s.add_argument("--basis", default="", help="依据: 单元耗时 × 单元数 ÷ 并发, 或上次同类用时")
    e = sp.add_parser("extend", help="加时 MIN 分钟(从现在的预计结束时刻往后), 必须写原因")
    e.add_argument("min", type=float); e.add_argument("why")
    d = sp.add_parser("done", help="跑完了, 清钟并记实际耗时"); d.add_argument("note", nargs="?", default="")
    sp.add_parser("show")
    a = ap.parse_args()
    sid = _sid(a)
    now = time.time()
    cur = _load(sid)
    if a.cmd == "set":
        if a.min <= 0:
            sys.exit("分钟数要 > 0")
        if cur:
            _log(sid, cur, "replaced")
        d = {"what": a.what.strip()[:200], "basis": a.basis.strip()[:300], "start": now, "end": now + a.min * 60,
             "end0": now + a.min * 60, "extends": []}
        _save(sid, d)
        print("已上钟: %s · 预计 %g 分钟(%s 出结果)" % (d["what"], a.min, time.strftime("%H:%M", time.localtime(d["end"]))))
    elif a.cmd == "extend":
        if not cur:
            sys.exit("这个对话现在没有钟, 先 set")
        if not a.why.strip():
            sys.exit("加时必须写原因")
        cur["end"] = max(cur["end"], now) + a.min * 60
        cur["extends"].append({"at": round(now), "min": a.min, "why": a.why.strip()[:200]})
        _save(sid, cur)
        print("已加时 %g 分钟: %s · 新的预计 %s" % (a.min, a.why.strip(), time.strftime("%H:%M", time.localtime(cur["end"]))))
    elif a.cmd == "done":
        if not cur:
            print("这个对话没有钟, 无事可做"); return
        rec = _log(sid, cur, "done", a.note)
        os.remove(_path(sid))
        print("已收钟: %s · 预计 %d 秒, 实际 %d 秒(×%s)" % (rec["what"], rec["est_s"], rec["actual_s"], rec["ratio"]))
    else:
        if not cur:
            print("没有钟"); return
        print(json.dumps(dict(cur, remain_s=round(cur["end"] - now)), ensure_ascii=False))


if __name__ == "__main__":
    main()
