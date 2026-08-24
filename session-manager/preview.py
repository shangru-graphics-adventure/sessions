# -*- coding: utf-8 -*-
"""产物预览 —— 把一个文件渲染成能在浏览器里直接看的页面。

从 server.py 拆出来单独放, 原因很实际: 这里全是正则与转义, 塞在主文件里
每改一次都要跟 shell/JSON 的多层转义打架。
不引第三方 markdown 库 —— 这个工具的价值是"双击就能用", 多一个 pip 依赖
就多一次"在别的机器上跑不起来"。
"""
import io
import os
import re
import subprocess

PREVIEW_MAX = 8 * 1024 * 1024      # 超过这个就不读了, 只报大小
CSV_ROWS = 400                     # csv 最多渲染多少行

# 大块中间数据: 不预览, 只报大小(与 server.ART_DATA_EXT 保持一致)
DATA_EXT = (".npz", ".npy", ".parquet", ".pkl", ".pickle", ".h5",
            ".db", ".sqlite", ".feather", ".arrow")

_INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<b>\1</b>"),
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r'<a href="\2" target="_blank">\1</a>'),
]


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def md(text):
    """够用就好的 markdown: 标题 / 代码块 / 表格 / 列表 / 行内格式。"""
    out = []
    fence = False
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("```"):
            out.append("</pre>" if fence else "<pre>")
            fence = not fence
            i += 1
            continue
        if fence:
            out.append(esc(ln))
            i += 1
            continue
        # 表格: |a|b| 且下一行是 |---|---|
        if (ln.strip().startswith("|") and i + 1 < len(lines)
                and set(lines[i + 1].replace("|", "").strip()) <= set("-: ")
                and lines[i + 1].strip()):
            rows = []
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            t = ["<table><thead><tr>"]
            t += ["<th>%s</th>" % esc(c) for c in rows[0]]
            t.append("</tr></thead><tbody>")
            for r in rows[2:]:
                t.append("<tr>" + "".join("<td>%s</td>" % esc(c) for c in r) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            i = j
            continue
        h = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if h:
            n = len(h.group(1))
            out.append("<h%d>%s</h%d>" % (n, esc(h.group(2)), n))
            i += 1
            continue
        if re.match(r"^\s*[-*+]\s+", ln):
            out.append("<div class=li>&bull; %s</div>"
                       % esc(re.sub(r"^\s*[-*+]\s+", "", ln)))
            i += 1
            continue
        out.append("<p>%s</p>" % esc(ln) if ln.strip() else "<div class=sp></div>")
        i += 1
    html = "\n".join(out)
    for rx, rep in _INLINE:
        html = rx.sub(rep, html)
    return html


def csv_table(text, cap=CSV_ROWS):
    import csv as _csv
    rows = list(_csv.reader(io.StringIO(text)))
    if not rows:
        return "<p>(空表)</p>"
    t = ["<table><thead><tr>"]
    t += ["<th>%s</th>" % esc(c) for c in rows[0]]
    t.append("</tr></thead><tbody>")
    for r in rows[1:cap + 1]:
        t.append("<tr>" + "".join("<td>%s</td>" % esc(c) for c in r) + "</tr>")
    t.append("</tbody></table>")
    if len(rows) - 1 > cap:
        t.append("<p class=dim>&hellip;还有 %d 行未显示</p>" % (len(rows) - 1 - cap))
    return "".join(t)


CSS = """<style>
:root{
  --bg:#fbfcfe; --panel:#ffffff; --panel2:#f1f5f9;
  --line:#e2e8f0; --line2:#cbd5e1;
  --fg:#1f2933; --dim:#6b7885;
  --accent:#0d76b0; --accent-bright:#2ea8e6; --accent-soft:#e3f4fd;
  --pink:#c73f70; --pink-soft:#fde8f0; --pink-line:#f7b8ce;
  --shadow:0 1px 2px #1f29330d, 0 6px 18px -12px #1f293326;
  --mono:ui-monospace,"Cascadia Mono",Consolas,monospace;
}
body{background:var(--bg);color:var(--fg);margin:0;padding:28px 30px 80px;
     font:14.5px/1.65 "Segoe UI",-apple-system,"PingFang SC",
          "Microsoft YaHei",sans-serif}
h1,h2,h3,h4{color:var(--fg);margin:24px 0 9px;line-height:1.28;
            font-weight:700;letter-spacing:-.01em}
h1{font-size:25px} h2{font-size:19px;border-bottom:2px solid var(--pink-line);
   padding-bottom:5px} h3{font-size:16px;color:var(--pink)}
p{margin:5px 0} .sp{height:9px} .li{margin:3px 0 3px 8px}
a{color:var(--accent);text-underline-offset:2px}
code{background:var(--accent-soft);padding:1px 5px;border-radius:4px;
     font-family:var(--mono);font-size:13px;color:#0b6595}
pre{background:var(--panel);border:1px solid var(--line);border-radius:6px;
    padding:13px 15px;overflow-x:auto;font-family:var(--mono);
    font-size:12.5px;line-height:1.6;color:var(--fg);box-shadow:var(--shadow)}
table{border-collapse:collapse;margin:12px 0;font-size:13.5px;display:block;
      overflow-x:auto;max-width:100%;font-variant-numeric:tabular-nums}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;
      vertical-align:top}
th{background:var(--pink-soft);color:#8d2f56;font-weight:600;
   position:sticky;top:0}
td{background:var(--panel)}
tr:nth-child(even) td{background:#fafcfe}
.hdr{border-bottom:2px solid var(--pink-line);padding-bottom:12px;margin-bottom:18px}
.hdr h1{margin:0}
.hdr .pth{font-family:var(--mono);font-size:11px;color:var(--dim);
          margin-top:7px;word-break:break-all}
.dim{color:var(--dim);font-size:12.5px}
</style>"""


def preview_html(fp):
    """渲染一个产物。看不了的(二进制/超大)就老实说看不了, 别给个空白页。"""
    hdr = ('<div class=hdr><h1 style="margin:0">%s</h1><div class=pth>%s</div></div>'
           % (esc(os.path.basename(fp)), esc(fp)))
    try:
        sz = os.path.getsize(fp)
    except OSError as e:
        return CSS + hdr + "<p>读不到: %s</p>" % esc(str(e))
    ext = os.path.splitext(fp)[1].lower()
    if ext in DATA_EXT or sz > PREVIEW_MAX:
        return (CSS + hdr + "<p class=dim>%s, %.1f MB &mdash; 这类文件不在浏览器里"
                "预览, 用列表里的 &#8982; 在资源管理器里定位。</p>"
                % (ext or "无后缀", sz / 1048576.0))
    try:
        with io.open(fp, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except Exception as e:
        return CSS + hdr + "<p>读不到: %s</p>" % esc(str(e))
    if ext in (".html", ".htm"):
        return text                       # 报告类产物本来就是网页, 原样给
    if ext in (".csv", ".tsv"):
        return CSS + hdr + csv_table(text)
    if ext == ".md":
        return CSS + hdr + md(text)
    return CSS + hdr + "<pre>%s</pre>" % esc(text)


def reveal(fp):
    """在资源管理器里选中它。选中而不是打开 —— 打开可能触发一堆东西。"""
    if not fp or not os.path.exists(fp):
        return {"ok": False, "why": "文件不在了"}
    try:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(fp)])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "why": str(e)}
