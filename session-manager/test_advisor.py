# -*- coding: utf-8 -*-
"""advisor.py 的回归测试 —— 不调模型, 只测"模型手抖时我们还救不救得回来"。

为什么值得写: 「建议」按钮的失败模式几乎全在**解析**上。模型返回的东西是随机的,
线上第一次实跑就撞到了一次解析失败(见 README)。这里把见过的和能想到的畸形输出
全钉成用例 —— 先造已知的阳性, 再信它的阴性。

    python test_advisor.py
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import advisor

OK = json.dumps({"topic": "做了 A 和 B", "goal": "想要 C",
                 "status": "部分达成: D 还没跑", "next": ["先 E", "再 F"]},
                ensure_ascii=False)

CASES = [
    ("干净 JSON", OK, 2),
    ("裹了 ```json", "```json\n" + OK + "\n```", 2),
    ("前后加了话", "好的，这是复盘：\n" + OK + "\n希望有帮助！", 2),
    ("JSON 后面又补了一段带 } 的话", OK + "\n注: 若 {x} 成立则另说。", 2),
    # 见过的真实手抖: 字符串里塞裸换行
    ("字符串里裸换行", '{"topic": "第一行\n第二行", "goal": "g", '
                       '"status": "未达成", "next": ["a", "b"]}', 2),
    ("被截断(少了右括号)", '{"topic": "t", "goal": "g", "status": "s", '
                           '"next": ["a", "b"', 2),
    ("next 写成了一整段", '{"topic": "t", "goal": "g", "status": "s", '
                          '"next": "- 先 E\\n- 再 F"}', 2),
]

BAD = [
    ("纯废话没有 JSON", "抱歉，我无法完成这个任务。"),
    ("空输出", ""),
    ("有括号但不是对象", "[1, 2, 3]"),
    ("四个字段全空", '{"topic": "", "goal": "", "status": "", "next": []}'),
]


def main():
    fails = []

    # ---- 阳性: 这些都必须救回来 ----
    for name, raw, want_next in CASES:
        d = advisor._parse(raw)
        if d is None:
            fails.append("[救不回来] %s" % name)
            continue
        if not d.get("topic") and not d.get("status"):
            fails.append("[字段全空] %s" % name)
        if len(d.get("next") or []) != want_next:
            fails.append("[next 条数 %d != %d] %s"
                         % (len(d.get("next") or []), want_next, name))
        print("  ok  %-24s -> topic=%r next=%d"
              % (name, d["topic"][:18], len(d["next"])))

    # ---- 阴性: 这些必须判死, 否则解析器就是个只会说 yes 的坏仪器 ----
    for name, raw in BAD:
        d = advisor._parse(raw)
        if d is not None:
            fails.append("[该拒收却收了] %s -> %r" % (name, d))
        else:
            print("  ok  %-24s -> 正确拒收" % name)

    # ---- 语料构造: 头尾给全文, 中间只给提问 ----
    turns = [{"q": "问题%d" % i, "a": "回答%d\n· Bash 干活" % i, "tools": 1, "sub": 0}
             for i in range(100)]
    t0 = time.time()
    corpus, full = advisor.build_corpus(turns)
    ms = (time.time() - t0) * 1000
    if full != advisor.HEAD + advisor.TAIL:
        fails.append("[全文轮数 %d != %d]" % (full, advisor.HEAD + advisor.TAIL))
    if "· Bash" in corpus:
        fails.append("[工具流水没剔干净]")
    if "回答50" in corpus:
        fails.append("[中间轮次不该带回复]")
    if "问题50" not in corpus:
        fails.append("[中间轮次的提问丢了]")
    if "回答99" not in corpus or "回答0" not in corpus:
        fails.append("[头尾的回复没给全]")
    if ms > 50:
        fails.append("[build_corpus 太慢: %.1fms]" % ms)
    print("  ok  %-24s -> %d 轮全文 / %d 字 / %.1fms"
          % ("语料构造(100 轮)", full, len(corpus), ms))

    print()
    if fails:
        print("失败 %d 项:" % len(fails))
        for f in fails:
            print("  " + f)
        return 1
    print("全部通过 (%d 阳性 + %d 阴性 + 语料构造)" % (len(CASES), len(BAD)))
    return 0


if __name__ == "__main__":
    try:
        import utf8_console
        utf8_console.enable()
    except Exception:
        pass
    sys.exit(main())
