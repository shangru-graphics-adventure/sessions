# -*- coding: utf-8 -*-
"""把 VS Code 终端标签改名为对应 Claude 会话名(~/.claude/sessions/<pid>.json 的 name)。
终端的 processId = claude 进程的父进程(shell) pid; 逐个问桥端口 8721..8728 的 /rename。
用法: python rename_tabs.py        (需先 Reload Window 或 Restart Extension Host 让桥加载 /rename)
"""
import glob, json, os, subprocess, sys, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
SESS = os.path.expanduser("~/.claude/sessions")


def parent_pid(pid):
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ParentProcessId"],
                         capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    s = out.stdout.strip()
    return int(s) if s.isdigit() else None


def post(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "why": str(e)}


def main():
    n_sess = n_ok = 0
    for f in glob.glob(os.path.join(SESS, "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        name, pid = d.get("name"), d.get("pid")
        if not name or not pid:
            continue
        ppid = parent_pid(pid)
        if not ppid:
            continue  # 进程已不在
        n_sess += 1
        res = None
        for port in range(8721, 8729):
            res = post(port, "/rename", {"pid": ppid, "name": name})
            if res.get("ok"):
                n_ok += 1
                print(f"{name}: {res.get('from')} -> {res.get('to')} (端口 {port})")
                break
        else:
            print(f"{name}: 没找到终端(shell pid {ppid}); 最后一次回复 {res}")
    print(f"在世会话 {n_sess} 个, 改名成功 {n_ok} 个")
    return 0 if n_ok or not n_sess else 1


if __name__ == "__main__":
    sys.exit(main())
