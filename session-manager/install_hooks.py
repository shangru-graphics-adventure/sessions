# -*- coding: utf-8 -*-
"""把实时状态 hook 装进 ~/.claude/settings.json(幂等, 可反复跑)。

  python install_hooks.py            装/更新
  python install_hooks.py --remove   卸载(只删本工具加的那几条, 不碰你原有的 hook)

原有 hook 一条都不动 —— 只在对应事件的 hooks 数组里追加/替换带 MARK 标记的那一条。
"""
import io
import os
import sys
import json
import time
import shutil

import config

import utf8_console
utf8_console.enable()

SETTINGS = config.SETTINGS_PATH
HOOK_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook_state.py")
MARK = "hook_state.py"          # 认领标记: 命令里含这个字符串的就是本工具装的

# SessionStart 是"这个对话又被开到一个新窗口里了"的最早信号(resume / 新开 / clear),
# 没有它, 刚 resume 出来还没说话的窗口不会被记上, 而"你已经开着一个了"的提醒
# 恰恰要在你重复 resume 之前给出来。
EVENTS = ["SessionStart", "UserPromptSubmit", "Stop", "Notification", "SessionEnd"]


def entry(event):
    return {
        "hooks": [{
            "type": "command",
            "command": 'python "%s" %s' % (HOOK_PY, event),
            "shell": "powershell",
            "async": True,
        }]
    }


def main():
    remove = "--remove" in sys.argv
    if os.path.exists(SETTINGS):
        with io.open(SETTINGS, encoding="utf-8") as fh:
            cfg = json.load(fh)
        bak = SETTINGS + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(SETTINGS, bak)
    else:
        # 全新机器还没有 settings.json: 建一个空的, 没有旧内容可备份
        if remove:
            sys.exit("没有 %s, 无需卸载。" % SETTINGS)
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        cfg, bak = {}, "(无, 原来就没有这个文件)"

    hooks = cfg.setdefault("hooks", {})
    changed = []
    for ev in EVENTS:
        arr = hooks.setdefault(ev, [])
        # 先摘掉本工具以前装的
        before = len(arr)
        arr[:] = [g for g in arr
                  if not any(MARK in (h.get("command") or "")
                             for h in g.get("hooks", []))]
        removed = before - len(arr)
        if remove:
            if removed:
                changed.append("%s: 移除 %d 条" % (ev, removed))
            if not arr:
                hooks.pop(ev, None)
            continue
        arr.append(entry(ev))
        changed.append("%s: %s" % (ev, "已更新" if removed else "已添加"))

    tmp = SETTINGS + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS)

    print("备份: %s" % bak)
    for c in changed:
        print("  " + c)
    print("现有 hook 事件: %s" % ", ".join(sorted(cfg.get("hooks", {}))))
    for ev in sorted(cfg.get("hooks", {})):
        n = sum(len(g.get("hooks", [])) for g in cfg["hooks"][ev])
        print("    %-18s %d 条" % (ev, n))


if __name__ == "__main__":
    main()
