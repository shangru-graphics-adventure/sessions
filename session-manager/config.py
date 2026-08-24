# -*- coding: utf-8 -*-
"""Machine-local configuration.

Everything here has a sane default derived from your home directory, so the
tool runs with zero setup.  Override any of it by dropping a `config.json`
next to this file (it is gitignored), or with an environment variable:

    config.json                     env var                 default
    --------------------------------------------------------------------
    "port": 8720                    SESSIONS_PORT           8720
    "claude_home": "~/.claude"      CLAUDE_CONFIG_DIR       ~/.claude
    "recap_dir": "..."              SESSIONS_RECAP_DIR      ~/session_recaps
    "archive_dirs": ["D:/archive"]  SESSIONS_ARCHIVE_DIRS   (none)
    "project_roots": ["C:/work"]    SESSIONS_PROJECT_ROOTS  ~

`archive_dirs` / `project_roots` are only used to find *markdown transcripts*
that some setups export via a Stop/SessionEnd hook:

    <archive_dir>/<any>/<any>/<session-id>.md
    <project_root>/<any>/_docs/chat/<any>/<session-id>.md

Leave them empty if you do not export transcripts — the tool works fine
without them, it just will not show a "markdown copy" link.
"""
import io
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")

_cfg = {}
_path = os.path.join(HERE, "config.json")
if os.path.exists(_path):
    try:
        with io.open(_path, encoding="utf-8") as fh:
            _cfg = json.load(fh)
    except Exception as exc:                     # a broken config must not brick the tool
        print("[config] ignoring %s: %s" % (_path, exc))


def _expand(p):
    return os.path.abspath(os.path.expanduser(os.path.expandvars(p)))


def _list(key, env, default):
    raw = os.environ.get(env)
    if raw:
        vals = [v for v in raw.split(os.pathsep) if v.strip()]
    else:
        vals = _cfg.get(key, default)
        if isinstance(vals, str):
            vals = [vals]
    return [_expand(v) for v in vals]


def _one(key, env, default):
    return _expand(os.environ.get(env) or _cfg.get(key) or default)


PORT = int(os.environ.get("SESSIONS_PORT") or _cfg.get("port") or 8720)

# Claude Code keeps one jsonl per conversation under <claude_home>/projects/<slug>/
CLAUDE_HOME = _one("claude_home", "CLAUDE_CONFIG_DIR", os.path.join(HOME, ".claude"))
PROJECTS_DIR = os.path.join(CLAUDE_HOME, "projects")
SETTINGS_PATH = os.path.join(CLAUDE_HOME, "settings.json")

RECAP_DIR = _one("recap_dir", "SESSIONS_RECAP_DIR", os.path.join(HOME, "session_recaps"))
ARCHIVE_DIRS = _list("archive_dirs", "SESSIONS_ARCHIVE_DIRS", [])
PROJECT_ROOTS = _list("project_roots", "SESSIONS_PROJECT_ROOTS", [HOME])


# "这个对话还开着吗" = 它的进程还在吗。进程名默认就是 claude.exe; 如果你用别的
# 封装(某些安装方式下宿主是 node.exe)就在这里加, 否则实时状态与窗口感知会全灭。
CLAUDE_PROCS = tuple(
    n.strip().lower() for n in
    (os.environ.get("SESSIONS_CLAUDE_PROCS") or "").split(os.pathsep) if n.strip()
) or tuple(n.lower() for n in _cfg.get("claude_procs", ["claude.exe"]))


# Resume 一个对话时, 先把它的工作目录标记成"已信任", 免得新窗口第一屏是那个
# "Do you trust the files in this folder?" 对话框。默认开: 本工具 resume 的都是
# **你自己已经在里面工作过**的目录, 那个提示在这个场景下是纯噪音。
# 不想要就 config.json 里 "auto_trust": false, 或环境变量 SESSIONS_AUTO_TRUST=0。
_at = os.environ.get("SESSIONS_AUTO_TRUST")
AUTO_TRUST = (_at not in ("0", "false", "no")) if _at is not None     else bool(_cfg.get("auto_trust", True))


# 关掉一个对话时, 顺手把它的窗口也关掉 —— 但**只对纯终端宿主**这么做。
# 这是白名单不是黑名单, 因为认不出来的宿主一律当作"不能关"才安全:
#   · Code.exe   —— VS Code 集成终端。那个窗口里还装着你的编辑器, 关掉 = 关掉整个 IDE
#   · explorer.exe —— 关掉 = 桌面和任务栏一起没
# 这两个都出现在 hook 认终端的名单里(它们确实可能是 claude 的祖先进程),
# 所以少了这道白名单, "关闭对话"会变成"关闭你的编辑器"。
CLOSABLE_TERMS = tuple(n.lower() for n in _cfg.get("closable_terms", [
    "windowsterminal.exe", "conhost.exe", "openconsole.exe",
    "cmd.exe", "powershell.exe", "pwsh.exe",
]))


def project_slug(path):
    """Claude Code's directory name for a cwd: every non-alphanumeric char -> '-'.

    Used to exclude the titler's own throwaway sessions from the listing.
    """
    return "".join(c if c.isalnum() else "-" for c in os.path.abspath(path))
