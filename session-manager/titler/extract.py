# -*- coding: utf-8 -*-
"""扫全部 jsonl, 提每个会话的用户发言, 产出 corpus.jsonl 供 haiku 生成标题。

口径(写死, 与 server.py 的 topics 不同 —— 那个 46 字截断+中间折叠正是漏搜的根源):
  - 只取 type=user 的真实文本(排除 tool_result / system-reminder / 命令回显)
  - 每条截 200 字; 全会话最多 40 条, 超了首 20 + 尾 20
"""
import os, io, json, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

import utf8_console
utf8_console.enable()

PROJ = config.PROJECTS_DIR
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus.jsonl")

# 本目录跑的 `claude -p` 自己会产生几千个会话文件, 重跑 extract 时必须排除,
# 否则语料里全是"给这个对话起标题"的元噪音。
IGNORE_PROJ = {config.project_slug(os.path.dirname(os.path.abspath(__file__)))}

MAX_MSGS = 40
MAX_CHARS = 200

JUNK_PREFIX = ("<command-name>", "<local-command", "<system-reminder", "Caveat:",
               "<command-message>", "[Request interrupted", "API Error",
               "<user-prompt-submit-hook>")


def user_texts(path):
    out = []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"type":"user"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") != "user":
                continue
            c = (d.get("message") or {}).get("content")
            if isinstance(c, list):
                parts = []
                for x in c:
                    if isinstance(x, dict) and x.get("type") == "text":
                        parts.append(x.get("text", ""))
                c = "\n".join(parts)
            if not isinstance(c, str):
                continue
            s = c.strip()
            if len(s) < 3 or s.startswith(JUNK_PREFIX):
                continue
            # 去掉夹在中间的 system-reminder 块
            if "<system-reminder>" in s:
                s = s.split("<system-reminder>")[0].strip()
                if len(s) < 3:
                    continue
            out.append(s[:MAX_CHARS])
    return out


def fold(msgs):
    """超过 MAX_MSGS 条就首尾各留一半, 中间折叠成一条计数。

    与 server.py 的 topics 折叠是两回事: 那个只留 14 条 × 46 字, 信息损失大到
    能把整场讨论藏起来(见 README §1)。这里留 40 条 × 200 字。
    """
    if len(msgs) <= MAX_MSGS:
        return msgs
    h = MAX_MSGS // 2
    return msgs[:h] + ["…(中间省略 %d 条)…" % (len(msgs) - h * 2)] + msgs[-h:]


def main():
    n_files = n_ok = 0
    with io.open(OUT, "w", encoding="utf-8") as w:
        for d in sorted(os.listdir(PROJ)):
            p = os.path.join(PROJ, d)
            if not os.path.isdir(p) or d in IGNORE_PROJ:
                continue
            for fn in sorted(os.listdir(p)):
                if not fn.endswith(".jsonl"):
                    continue
                fp = os.path.join(p, fn)
                n_files += 1
                try:
                    msgs = user_texts(fp)
                except Exception as e:
                    sys.stderr.write("skip %s: %s\n" % (fn, e))
                    continue
                if not msgs:
                    continue
                msgs = fold(msgs)
                n_ok += 1
                w.write(json.dumps({
                    "sid": fn[:-6], "proj": d, "n": len(msgs),
                    "mtime": os.path.getmtime(fp),
                    "msgs": msgs,
                }, ensure_ascii=False) + "\n")
    print("扫描 %d 个 jsonl, 产出 %d 条语料 -> %s" % (n_files, n_ok, OUT))


if __name__ == "__main__":
    main()
