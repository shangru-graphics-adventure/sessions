# -*- coding: utf-8 -*-
"""端到端验证实时状态: hook 写账 -> server 合成 -> /api/status 读出来。

用**本会话真实的 session_id**, 并让 hook 自己从父进程链找真实的 claude.exe pid,
所以这不是造假数据 —— 走的就是正式 hook 的同一条路。

跑法:  python test_status.py <session_id>
"""
import io
import os
import sys
import json
import time
import urllib.request
import subprocess

import config

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
HOOK = os.path.join(HERE, "hook_state.py")
API = "http://localhost:%d/api/status" % config.PORT
if len(sys.argv) < 2:
    sys.exit("用法: python test_status.py <session_id>   (用你当前这个对话的真实 id)")
SID = sys.argv[1]
TRANSCRIPT = os.path.join(config.PROJECTS_DIR,
                          config.project_slug(HOME), SID + ".jsonl")
FAILED = []


def fire(event, extra=None):
    p = dict({"session_id": SID, "cwd": HOME,
              "transcript_path": TRANSCRIPT}, **(extra or {}))
    subprocess.run([sys.executable, HOOK, event],
                   input=json.dumps(p, ensure_ascii=False).encode("utf-8"),
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def api():
    t0 = time.time()
    with urllib.request.urlopen(API, timeout=10) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d, (time.time() - t0) * 1000


def check(name, cond, extra=""):
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        FAILED.append(name)


print("会话: %s" % SID)

print("1) UserPromptSubmit -> 应该显示 正在跑 + 我的原话")
fire("UserPromptSubmit", {"prompt": "每个对话都实时管理, 我要知道现在在干嘛"})
d, ms = api()
s = d["status"].get(SID, {})
check("state=running", s.get("state") == "running", s.get("state"))
check("goal 是我的原话", "实时管理" in (s.get("goal") or ""), repr(s.get("goal"))[:50])
check("认出这个对话还活着(pid 存活)", s.get("pid") is not None, "pid=%s" % s.get("pid"))
print("     /api/status 耗时 %.0f ms, 后端 %sms, 活跃 %s 个" % (ms, d["ms"], d["live"]))

print("2) Notification -> 等你确认")
fire("Notification", {"message": "Claude needs your permission to run Bash"})
d, _ = api()
s = d["status"].get(SID, {})
check("state=waiting", s.get("state") == "waiting", s.get("state"))
check("note 带上了具体内容", "permission" in (s.get("note") or ""), repr(s.get("note"))[:60])

print("3) Stop -> 开着·等你 + 我刚说完的话")
fire("Stop")
d, _ = api()
s = d["status"].get(SID, {})
check("state=done", s.get("state") == "done", s.get("state"))
check("result 抓到了我最后一段话", len(s.get("result") or "") > 10, repr(s.get("result"))[:60])

print("4) 进程存活兜底: 把 pid 改成一个不存在的, 必须变 closed")
sp = os.path.join(HERE, "state", SID + ".json")
rec = json.load(io.open(sp, encoding="utf-8"))
real_pid, real_ct = rec.get("pid"), rec.get("pid_ctime")
rec["pid"] = 999999
json.dump(rec, io.open(sp, "w", encoding="utf-8"), ensure_ascii=False)
time.sleep(2.2)                       # 等 server 端 2 秒进程表缓存过期
d, _ = api()
check("死 pid -> closed", d["status"].get(SID, {}).get("state") == "closed",
      d["status"].get(SID, {}).get("state"))

print("5) pid 复用防护: pid 对但创建时间对不上, 也必须 closed")
rec["pid"] = real_pid
rec["pid_ctime"] = (real_ct or 0) - 9999
json.dump(rec, io.open(sp, "w", encoding="utf-8"), ensure_ascii=False)
time.sleep(2.2)
d, _ = api()
check("创建时间不匹配 -> closed", d["status"].get(SID, {}).get("state") == "closed",
      d["status"].get(SID, {}).get("state"))

# 还原成真实状态
rec["pid"], rec["pid_ctime"] = real_pid, real_ct
rec["state"] = "running"
rec["goal"] = "每个对话都实时管理, 我要知道现在在干嘛"
json.dump(rec, io.open(sp, "w", encoding="utf-8"), ensure_ascii=False)
time.sleep(2.2)
d, _ = api()
check("还原后重新变回 running", d["status"].get(SID, {}).get("state") == "running",
      d["status"].get(SID, {}).get("state"))

print("")
print("FAILED: %s" % (FAILED or "无, 全部通过"))
sys.exit(1 if FAILED else 0)
