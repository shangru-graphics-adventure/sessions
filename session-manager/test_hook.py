# -*- coding: utf-8 -*-
"""hook_state.py 的干跑测试: 四个事件各走一遍, 校验状态文件内容、stdout 干净、耗时。

跑法:  python test_hook.py
"""
import io
import os
import sys
import json
import time
import shutil
import subprocess

import config

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
HOOK = os.path.join(HERE, "hook_state.py")
SID = "TEST-dryrun-0001"
STATE = os.path.join(HERE, "state", SID + ".json")
TRANSCRIPT = os.path.join(config.PROJECTS_DIR,
                          config.project_slug(HOME), SID + ".jsonl")

GOAL = "测试一下状态 hook 能不能正确写入"
FAILED = []


def fire(event, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    t0 = time.time()
    p = subprocess.run([sys.executable, HOOK, event], input=body,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ms = (time.time() - t0) * 1000
    return p, ms


def load():
    with io.open(STATE, encoding="utf-8") as fh:
        return json.load(fh)


def check(name, cond, extra=""):
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        FAILED.append(name)


base = {"session_id": SID, "cwd": HOME,
        "transcript_path": TRANSCRIPT}

if os.path.exists(STATE):
    os.remove(STATE)

print("1) UserPromptSubmit")
p, ms = fire("UserPromptSubmit", dict(base, prompt=GOAL))
d = load()
check("exit 0", p.returncode == 0)
check("stdout 必须为空(否则会污染对话上下文)", p.stdout == b"", repr(p.stdout[:80]))
check("state=running", d.get("state") == "running")
check("goal 中文原样保存", d.get("goal") == GOAL, repr(d.get("goal"))[:60])
check("抓到了 claude.exe pid", bool(d.get("pid")), "pid=%s" % d.get("pid"))
check("抓到了终端窗口", bool(d.get("term_pid")),
      "%s pid=%s" % (d.get("term_name"), d.get("term_pid")))
print("     耗时 %.0f ms   payload_keys=%s" % (ms, d.get("payload_keys")))

print("2) Notification")
p, ms = fire("Notification", dict(base, message="Claude needs your permission to use Bash"))
d = load()
check("state=waiting", d.get("state") == "waiting")
check("note 有内容", bool(d.get("note")), repr(d.get("note"))[:60])
check("stdout 为空", p.stdout == b"")
print("     耗时 %.0f ms" % ms)

print("3) Stop")
p, ms = fire("Stop", dict(base))
d = load()
check("state=done", d.get("state") == "done")
check("抓到了我最后说的话", len(d.get("result") or "") > 10, repr(d.get("result"))[:70])
check("有耗时统计", d.get("took") is not None, "took=%s" % d.get("took"))
check("stdout 为空", p.stdout == b"")
print("     耗时 %.0f ms" % ms)

print("4) SessionEnd")
p, ms = fire("SessionEnd", dict(base, reason="clear"))
d = load()
check("state=closed", d.get("state") == "closed")
print("     耗时 %.0f ms" % ms)

print("5) 中文必须活着穿过 powershell(真实 hook 走的就是这条路)")
# 教训: 头一版测试只跑 python->python, 而线上是 claude->powershell->python。
# 两条路的控制台 code page 不同(65001 vs cp936), 结果线上中文全变成 "璇诲苟琛ラ綈",
# 测试却一路绿灯。所以这里必须按线上的调用形状测, 并且额外把 stdin 强制成 GBK 环境。
CN = "中文目标必须原样穿过 powershell"
if os.path.exists(STATE):
    os.remove(STATE)
body = json.dumps(dict(base, prompt=CN), ensure_ascii=False).encode("utf-8")
subprocess.run(["powershell", "-NoProfile", "-Command",
                '& "%s" "%s" UserPromptSubmit' % (sys.executable, HOOK)],
               input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
d = load()
check("经 powershell 后中文完好", d.get("goal") == CN, repr(d.get("goal"))[:70])

# 再模拟 code page 是 GBK 的环境 —— 这正是线上翻车的那种环境
env = dict(os.environ, PYTHONIOENCODING="cp936")
os.remove(STATE)
subprocess.run([sys.executable, HOOK, "UserPromptSubmit"], input=body, env=env,
               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
d = load()
check("stdin 被钉成 cp936 时仍完好", d.get("goal") == CN, repr(d.get("goal"))[:70])

print("6) 异常输入不能崩(空 stdin / 坏 json / 没有 session_id)")
for bad in [b"", b"{not json", json.dumps({"cwd": "x"}).encode()]:
    p = subprocess.run([sys.executable, HOOK, "Stop"], input=bad,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    check("exit 0 且 stdout 空 (%r)" % bad[:12],
          p.returncode == 0 and p.stdout == b"")

os.remove(STATE)
print("")
print("FAILED: %s" % (FAILED or "无, 全部通过"))
sys.exit(1 if FAILED else 0)
