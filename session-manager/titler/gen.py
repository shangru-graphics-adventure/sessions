# -*- coding: utf-8 -*-
"""单个会话的标题生成 —— 供管理器的"↻ 重起标题"按钮调用。

与批处理 (run_titles.py) 共用同一套口径:
  语料 = extract.user_texts + extract.fold   (只看用户发言, 每条 200 字, 最多 40 条)
  提示 = prompt.txt
  模型 = claude -p --model haiku

⚠ 每次调用都会触发 user-level 的 Stop hook(响铃 + 归档 + state 文件), 详见 README。
单条约 18-25 秒, 全在 CLI 冷启动上。
"""
import os, io, json, sys, time, subprocess, threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import utf8_console
utf8_console.enable()

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = config.PROJECTS_DIR
TITLES = os.path.join(HERE, "titles.jsonl")

import sys
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import extract                                    # noqa: E402  复用提取口径

SYSP = "你是一个标题生成器。只输出标题文本本身，不做任何其他事，不使用任何工具。"
CMD = ["claude", "-p", "--model", "haiku",
       "--strict-mcp-config", "--mcp-config", os.path.join(HERE, "empty_mcp.json"),
       "--settings", os.path.join(HERE, "empty_settings.json"),
       "--system-prompt", SYSP]

_write_lock = threading.Lock()


def find_jsonl(sid):
    for d in os.listdir(PROJ):
        fp = os.path.join(PROJ, d, sid + ".jsonl")
        if os.path.exists(fp):
            return fp
    return None


def msgs_for(sid):
    fp = find_jsonl(sid)
    if not fp:
        return None
    return extract.fold(extract.user_texts(fp))


def clean(title):
    title = (title or "").split("\n")[0].strip().strip('"“”「」\'')
    return title[:60]


def gen_title(msgs, timeout=180):
    """跑一次 haiku, 返回 (title, err)。"""
    prompt = io.open(os.path.join(HERE, "prompt.txt"), encoding="utf-8").read()
    p = prompt + "\n" + "\n".join("- " + m for m in msgs)
    try:
        r = subprocess.run(CMD, input=p.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
    except Exception as e:
        return "", str(e)[:200]
    t = clean(r.stdout.decode("utf-8", "replace"))
    err = "" if r.returncode == 0 else r.stderr.decode("utf-8", "replace")[:200]
    return t, err


def retitle(sid):
    """重新生成一个会话的标题并落盘。titles.jsonl 是追加写, 后写的赢。"""
    t0 = time.time()
    msgs = msgs_for(sid)
    if msgs is None:
        return {"ok": False, "error": "找不到该会话的 jsonl"}
    if not msgs:
        return {"ok": False, "error": "这个会话里没有可用的用户发言"}
    title, err = gen_title(msgs)
    ms = int((time.time() - t0) * 1000)
    if not title:
        return {"ok": False, "error": err or "模型返回空", "ms": ms}
    rec = {"sid": sid, "title": title, "ms": ms, "manual": True}
    if err:
        rec["err"] = err
    with _write_lock:
        with io.open(TITLES, "a", encoding="utf-8") as w:
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "title": title, "ms": ms, "n_msgs": len(msgs)}


if __name__ == "__main__":
    print(json.dumps(retitle(sys.argv[1]), ensure_ascii=False))
