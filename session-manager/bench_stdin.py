# -*- coding: utf-8 -*-
"""定位 hook payload 里中文变乱码的那一层。

真实 hook 走的是: claude -> powershell -> python;
而 test_hook.py 走的是: python(subprocess) -> python。
两条路的 stdin 解码行为不同, 所以测试通过不代表线上正确 —— 这里把两条都实测一遍。
"""
import io
import os
import sys
import json
import locale
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "_stdin_probe.py")
TEXT = "读并补齐 中文测试"

io.open(PROBE, "w", encoding="utf-8").write(
    "# -*- coding: utf-8 -*-\n"
    "import sys, json, io, locale\n"
    "raw_text = sys.stdin.read()\n"
    "out = {\n"
    "  'stdin_encoding': sys.stdin.encoding,\n"
    "  'preferred': locale.getpreferredencoding(),\n"
    "  'text_read': raw_text.strip(),\n"
    "}\n"
    "io.open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps(out, ensure_ascii=False))\n"
)

PROBE2 = os.path.join(HERE, "_stdin_probe2.py")
io.open(PROBE2, "w", encoding="utf-8").write(
    "# -*- coding: utf-8 -*-\n"
    "import sys, json, io\n"
    "raw = sys.stdin.buffer.read()\n"
    "out = {'bytes_read': raw.decode('utf-8', 'replace').strip()}\n"
    "io.open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps(out, ensure_ascii=False))\n"
)

payload = json.dumps({"t": TEXT}, ensure_ascii=False).encode("utf-8")
print("送进去的原文: %s" % TEXT)
print("locale.getpreferredencoding() = %s" % locale.getpreferredencoding())
print("")


def run(label, args, use_shell=False, input_bytes=payload):
    out_path = os.path.join(HERE, "_stdin_out.json")
    if os.path.exists(out_path):
        os.remove(out_path)
    subprocess.run(args, input=input_bytes, shell=use_shell,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        d = json.load(io.open(out_path, encoding="utf-8"))
    except Exception as e:
        print("%-42s 读不到结果: %s" % (label, e))
        return
    got = d.get("text_read") or d.get("bytes_read") or ""
    ok = TEXT in got
    print("%-42s %s" % (label, "OK  中文完好" if ok else "乱码 -> " + repr(got)[:70]))
    if "stdin_encoding" in d:
        print("%-42s   sys.stdin.encoding=%s" % ("", d["stdin_encoding"]))


OUT = os.path.join(HERE, "_stdin_out.json")

# 路线 1: python 直接调 python(= test_hook.py 走的路)
run("1 python->python, sys.stdin.read()", [sys.executable, PROBE, OUT])
run("1b python->python, stdin.buffer.read()", [sys.executable, PROBE2, OUT])

# 路线 2: 经过 powershell(= 真实 hook 走的路)
ps = ['powershell', '-NoProfile', '-Command',
      '& "%s" "%s" "%s"' % (sys.executable, PROBE, OUT)]
run("2 ps->python, sys.stdin.read()", ps)
ps2 = ['powershell', '-NoProfile', '-Command',
       '& "%s" "%s" "%s"' % (sys.executable, PROBE2, OUT)]
run("2b ps->python, stdin.buffer.read()", ps2)

# 路线 3: 经过 powershell 且先把控制台输入编码钉成 UTF-8
ps3 = ['powershell', '-NoProfile', '-Command',
       '[Console]::InputEncoding=[Text.Encoding]::UTF8; '
       '& "%s" "%s" "%s"' % (sys.executable, PROBE2, OUT)]
run("3 ps(UTF8 InputEncoding)->buffer.read()", ps3)

# 路线 4: 给 python 设 PYTHONIOENCODING=utf-8
env = dict(os.environ, PYTHONIOENCODING="utf-8")
out_path = OUT
if os.path.exists(out_path):
    os.remove(out_path)
subprocess.run(['powershell', '-NoProfile', '-Command',
                '& "%s" "%s" "%s"' % (sys.executable, PROBE, out_path)],
               input=payload, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    d = json.load(io.open(out_path, encoding="utf-8"))
    print("%-42s %s" % ("4 ps + PYTHONIOENCODING=utf-8",
                        "OK  中文完好" if TEXT in d.get("text_read", "")
                        else "乱码 -> " + repr(d.get("text_read"))[:70]))
except Exception as e:
    print("4 读不到: %s" % e)

for f in (PROBE, PROBE2, OUT):
    try:
        os.remove(f)
    except OSError:
        pass
