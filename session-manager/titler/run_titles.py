# -*- coding: utf-8 -*-
"""用 claude -p --model haiku 批量生成对话标题, 并发 + 断点续跑。

用法: python run_titles.py [并发数] [限量]
产出: titles.jsonl  每行 {"sid","title","ms"}  已存在的 sid 自动跳过。
"""
import os, io, json, sys, time, subprocess, threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utf8_console
utf8_console.enable()

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "corpus.jsonl")
OUT = os.path.join(HERE, "titles.jsonl")
PROMPT = io.open(os.path.join(HERE, "prompt.txt"), encoding="utf-8").read()
SYSP = "你是一个标题生成器。只输出标题文本本身，不做任何其他事，不使用任何工具。"

CMD = ["claude", "-p", "--model", "haiku",
       "--strict-mcp-config", "--mcp-config", os.path.join(HERE, "empty_mcp.json"),
       "--settings", os.path.join(HERE, "empty_settings.json"),
       "--system-prompt", SYSP]

lock = threading.Lock()
done_n = [0]
t0 = time.time()


def one(row, total):
    sid = row["sid"]
    p = PROMPT + "\n" + "\n".join("- " + m for m in row["msgs"])
    st = time.time()
    title, err, tries = "", "", 0
    # 2026-08-23 实测: 并发 32 跑 3294 条时有 433 条(13%)秒退返回空 —— returncode=0、
    # stderr 空、耗时 41-241ms, 单独重跑立刻正常。是 CLI 侧的静默失败, 必须自己重试。
    for attempt in range(3):
        tries = attempt + 1
        try:
            r = subprocess.run(CMD, input=p.encode("utf-8"),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=180)
            title = r.stdout.decode("utf-8", "replace").strip()
            err = "" if r.returncode == 0 else r.stderr.decode("utf-8", "replace")[:200]
        except Exception as e:
            title, err = "", str(e)[:200]
        if title:
            break
        time.sleep(2 + 3 * attempt)          # 退避后再来
    # 清洗: 取第一行, 去引号, 超长截断
    title = title.split("\n")[0].strip().strip('"“”「」\'')
    if len(title) > 60 or not title:
        title = title[:60]
    rec = {"sid": sid, "title": title, "ms": int((time.time() - st) * 1000)}
    if tries > 1:
        rec["tries"] = tries
    if err:
        rec["err"] = err
    with lock:
        with io.open(OUT, "a", encoding="utf-8") as w:
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
        done_n[0] += 1
        n = done_n[0]
        if n % 10 == 0 or n == total:
            el = time.time() - t0
            eta = el / n * (total - n)
            sys.stderr.write("[%d/%d] %.0fs elapsed, ETA %.0f min\n"
                             % (n, total, el, eta / 60))
            sys.stderr.flush()
    return rec


def main():
    par = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    rows = [json.loads(l) for l in io.open(CORPUS, encoding="utf-8")]
    # ⚠ 只把"拿到了非空标题"算作已完成。CLI 秒退产生的空记录必须被当成没跑过,
    # 否则断点续跑会永久跳过它们(2026-08-23 踩过, 433 条)。
    have = set()
    if os.path.exists(OUT):
        for l in io.open(OUT, encoding="utf-8"):
            try:
                d = json.loads(l)
            except Exception:
                continue
            if (d.get("title") or "").strip():
                have.add(d["sid"])
            else:
                have.discard(d["sid"])
    todo = [r for r in rows if r["sid"] not in have]
    todo.sort(key=lambda r: -r["mtime"])      # 新对话优先
    if lim:
        todo = todo[:lim]
    print("待处理 %d 条(已有 %d), 并发 %d" % (len(todo), len(have), par))
    with ThreadPoolExecutor(max_workers=par) as ex:
        list(ex.map(lambda r: one(r, len(todo)), todo))
    print("完成, 用时 %.1f 分钟" % ((time.time() - t0) / 60))


if __name__ == "__main__":
    main()
