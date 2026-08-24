# -*- coding: utf-8 -*-
"""窗口感知的回归测试: 多窗口检测 / 切过去 / 关闭。

**不碰任何真实对话。** 关闭那条路径是这个工具里唯一会杀进程的动作, 所以这里
自己造一个货真价实的 `claude.exe`(把 python.exe 复制一份改名) 让它睡着,
再让 server 去关它 —— 既走了完整的真实代码路径, 又不可能误伤你正开着的对话。

跑法(server 要先起来, 建议用一个空的 state 目录跑在别的端口上):

    set SESSIONS_PORT=8799 && python server.py
    python test_windows.py 8799
"""
import io
import os
import sys
import json
import time
import shutil
import tempfile
import subprocess
import urllib.request

import config
import utf8_console
utf8_console.enable()

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, "state")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else config.PORT
API = "http://127.0.0.1:%d" % PORT
SID_A = "TEST-win-aaaa"
SID_B = "TEST-win-bbbb"
ALIVE_TTL = 2.3
BS = chr(92)          # 反斜杠, 拼 Windows 路径字面量用
HOME = os.path.expanduser("~")
FAILED = []


def check(name, cond, extra=""):
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        FAILED.append(name)


def post(path, body):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))


def status():
    return json.loads(urllib.request.urlopen(API + "/api/status", timeout=20)
                      .read().decode("utf-8"))["status"]


def write_state(sid, procs):
    os.makedirs(STATE_DIR, exist_ok=True)
    rec = {"sid": sid, "cwd": os.path.expanduser("~"), "ts": time.time(),
           "state": "done", "goal": "窗口测试", "result": "", "note": "",
           "procs": procs}
    p = procs[-1]
    rec.update({k: p.get(k) for k in ("pid", "pid_ctime", "term_pid", "term_name", "hwnd")})
    with io.open(os.path.join(STATE_DIR, sid + ".json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False)


def spawn_fake_claude(tmp, n):
    """一个真的叫 claude.exe 的进程 —— 名字校验是关闭路径的第一道闸, 必须真过一遍。

    两个假进程放在两个子目录里, 都叫 claude.exe: 名字必须**一模一样**, 否则
    alive_pids / close_claude 的进程名校验会直接把它们当成"不是 claude", 测试
    就会在一个假前提上全绿或全红(第一版正是这么翻的车)。
    """
    d = os.path.join(tmp, "p%d" % n)
    os.makedirs(d, exist_ok=True)
    exe = os.path.join(d, "claude.exe")
    shutil.copyfile(sys.executable, exe)
    proc = subprocess.Popen([exe, "-c", "import time; time.sleep(300)"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.6)                       # 等它真的起来, 好取到创建时间
    import psutil
    return proc, round(psutil.Process(proc.pid).create_time(), 3), exe


def cleanup(procs, tmp):
    for pr in procs:
        try:
            pr.kill()
        except Exception:
            pass
    for sid in (SID_A, SID_B):
        try:
            os.remove(os.path.join(STATE_DIR, sid + ".json"))
        except OSError:
            pass
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    try:
        import psutil                     # noqa: F401
    except ImportError:
        sys.exit("需要 psutil: pip install psutil")

    tmp = tempfile.mkdtemp(prefix="fakeclaude-")
    p1, ct1, exe1 = spawn_fake_claude(tmp, 1)
    p2, ct2, _ = spawn_fake_claude(tmp, 2)
    print("假 claude 进程: %d, %d  (%s)" % (p1.pid, p2.pid, exe1))

    try:
        print("1) 一个窗口 -> wins 有一条, resume 应该是「切过去」而不是新开")
        write_state(SID_A, [{"pid": p1.pid, "pid_ctime": ct1, "term_pid": None,
                             "term_name": "cmd.exe", "hwnd": None,
                             "win_title": "假窗口 A"}])
        time.sleep(ALIVE_TTL)              # 等 server 端逐 pid 存活缓存过期
        s = status().get(SID_A, {})
        check("state 不是 closed", s.get("state") == "done", s.get("state"))
        check("wins 有 1 条", len(s.get("wins") or []) == 1, str(s.get("wins")))
        r = post("/api/resume", {"id": SID_A, "cwd": HOME, "dry_run": True})
        check("resume 走了切换分支(没有新开窗口)", r.get("switched") is True, json.dumps(r, ensure_ascii=False)[:120])
        check("没有 hwnd 时如实说切不过去", r.get("ok") is False, str(r.get("ok")))

        print("2) 同一个对话两个进程 -> 必须告警, resume 必须拒绝")
        write_state(SID_B, [
            {"pid": p1.pid, "pid_ctime": ct1, "term_pid": None, "term_name": "cmd.exe",
             "hwnd": None, "win_title": "假窗口 B1"},
            {"pid": p2.pid, "pid_ctime": ct2, "term_pid": None, "term_name": "cmd.exe",
             "hwnd": None, "win_title": "假窗口 B2"}])
        time.sleep(ALIVE_TTL)
        s = status().get(SID_B, {})
        check("wins 有 2 条", len(s.get("wins") or []) == 2, str(len(s.get("wins") or [])))
        r = post("/api/resume", {"id": SID_B, "cwd": HOME, "dry_run": True})
        check("resume 被拒绝", r.get("ok") is False and r.get("conflict") is True,
              json.dumps(r, ensure_ascii=False)[:120])
        check("拒绝时把两个窗口都列了出来", len(r.get("wins") or []) == 2)
        r = post("/api/focus", {"id": SID_B})
        check("不指明 pid 的 focus 也被拒绝", r.get("conflict") is True, str(r)[:80])

        print("3) 关闭的三道闸: pid 不存在 / 不是 claude.exe / 创建时间对不上")
        import actions
        r = actions.close_claude(999999)
        check("不存在的 pid 不炸", r.get("ok") is True and r.get("already"), str(r)[:80])
        r = actions.close_claude(os.getpid())
        check("拒绝杀非 claude.exe(本测试进程自己)", r.get("ok") is False, str(r)[:90])
        r = actions.close_claude(p1.pid, ct1 - 9999)
        check("创建时间对不上就拒绝(防 pid 复用)", r.get("ok") is False, str(r)[:90])
        check("被拒之后进程还活着", p1.poll() is None)

        print("4) 真关一个: 进程必须没, 状态必须变 closed")
        r = post("/api/close", {"id": SID_B, "pid": p2.pid})
        check("接口报成功", r.get("ok") is True, json.dumps(r, ensure_ascii=False)[:120])
        time.sleep(0.5)
        check("进程真的没了", p2.poll() is not None, "poll=%s" % p2.poll())
        time.sleep(ALIVE_TTL + 0.3)        # 等存活缓存过期
        s = status().get(SID_B, {})
        check("wins 从 2 条降到 1 条", len(s.get("wins") or []) == 1, str(len(s.get("wins") or [])))
        r = post("/api/resume", {"id": SID_B, "cwd": HOME, "dry_run": True})
        check("剩一个之后 resume 又能走切换了", r.get("switched") is True, str(r)[:100])

        print("4b) 查单个会话不能污染全局存活缓存(否则别的对话徽章会集体消失)")
        write_state(SID_A, [{"pid": p1.pid, "pid_ctime": ct1, "term_pid": None,
                             "term_name": "cmd.exe", "hwnd": None, "win_title": "假窗口 A"}])
        time.sleep(ALIVE_TTL)
        check("A 还开着", len(status().get(SID_A, {}).get("wins") or []) == 1)
        post("/api/focus", {"id": SID_B, "pid": p1.pid})     # 只问 B 的 pid
        s2 = status()
        check("问完 B 之后 A 仍然是开着的",
              len(s2.get(SID_A, {}).get("wins") or []) == 1,
              "A.wins=%s" % len(s2.get(SID_A, {}).get("wins") or []))

        print("5) 全关掉 -> closed, resume 回到「新开窗口」")
        post("/api/close", {"id": SID_A, "pid": p1.pid})
        time.sleep(ALIVE_TTL + 0.4)
        s = status().get(SID_A, {})
        check("state=closed", s.get("state") == "closed", s.get("state"))
        check("wins 空了", not (s.get("wins") or []), str(s.get("wins")))
        r = post("/api/resume", {"id": SID_A, "cwd": HOME, "dry_run": True})
        check("resume 回到新开窗口(dry-run 不真开)", r.get("dry") is True and not r.get("switched"),
              str(r)[:100])
        print("6) 自动 trust: 只翻一个布尔, 别的一个字节都不许动")
        import actions
        fake = os.path.join(tmp, ".claude.json")
        known = "C:@Users@me@known".replace("@", BS)
        orig = {"numStartups": 7, "userID": "abc",
                "projects": {"C:/Users/me/known": {"hasTrustDialogAccepted": False,
                                                   "lastCost": 1.5}}}
        with io.open(fake, "w", encoding="utf-8") as fh:
            json.dump(orig, fh, ensure_ascii=False)
        check("已有条目改得动", actions.trust_folder(known, fake) is True)
        d = json.load(io.open(fake, encoding="utf-8"))
        e = d["projects"].get("C:/Users/me/known") or {}
        check("key 用正斜杠", "C:/Users/me/known" in d["projects"])
        check("信任位翻了", e.get("hasTrustDialogAccepted") is True)
        check("同条目其它字段没丢", e.get("lastCost") == 1.5)
        check("顶层字段没丢", d.get("numStartups") == 7 and d.get("userID") == "abc")
        newdir = "D:@new@proj".replace("@", BS)
        check("没见过的目录会新建", actions.trust_folder(newdir, fake) is True
              and json.load(io.open(fake, encoding="utf-8"))["projects"]
                  .get("D:/new/proj", {}).get("hasTrustDialogAccepted") is True)
        check("文件不存在时不炸", actions.trust_folder(newdir, fake + ".nope") is False)
        with io.open(fake, "w", encoding="utf-8") as fh:
            fh.write("{ 这不是 json")
        check("坏 json 不炸也不覆盖", actions.trust_folder(newdir, fake) is False
              and io.open(fake, encoding="utf-8").read().startswith("{ 这不是"))
    finally:
        cleanup([p1, p2], tmp)

    print("")
    print("FAILED: %s" % (FAILED or "无, 全部通过"))
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
