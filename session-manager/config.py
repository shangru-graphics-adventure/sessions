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


def project_slug(path):
    """Claude Code's directory name for a cwd: every non-alphanumeric char -> '-'.

    Used to exclude the titler's own throwaway sessions from the listing.
    """
    return "".join(c if c.isalnum() else "-" for c in os.path.abspath(path))
