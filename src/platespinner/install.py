"""install-hooks, uninstall-hooks, install-kitten, install-service."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .procinfo import claude_dir

ROOT = Path(__file__).resolve().parent.parent.parent
HOOK_PATH = ROOT / "bin" / "platespinner-hook"
DAEMON_PATH = ROOT / "bin" / "platespinnerd"
KITTEN_SRC = ROOT / "kitten" / "focus_with_token.py"
HOOK_MARK = "platespinner-hook"

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
          "PermissionRequest", "Notification", "Stop", "StopFailure", "SessionEnd")


def settings_path():
    return claude_dir() / "settings.json"


def kitty_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")) / "kitty"


def kitten_path():
    return kitty_dir() / "focus_with_token.py"


def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}


def _save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.platespinner-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def _backup(path):
    path = Path(path)
    if not path.exists():
        return None
    dest = path.with_name(f"{path.name}.bak-platespinner-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, dest)
    return dest


def _is_ours(handler):
    return HOOK_MARK in str(handler.get("command", ""))


def _strip_ours(hooks):
    """Remove our handlers; drop groups and events left empty."""
    for event in list(hooks):
        groups = []
        for g in hooks[event]:
            kept = [h for h in g.get("hooks", []) if not _is_ours(h)]
            if kept:
                groups.append({**g, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]


def install_hooks(settings=None, hook=None):
    """Merge our hook into every event. Returns the backup path, if any."""
    settings = Path(settings or settings_path())
    data = _load(settings)
    backup = _backup(settings)
    hooks = data.setdefault("hooks", {})
    _strip_ours(hooks)
    for event in EVENTS:
        hooks.setdefault(event, []).append(
            {"hooks": [{"type": "command", "command": str(hook or HOOK_PATH), "timeout": 5}]})
    _save(settings, data)
    return backup


def uninstall_hooks(settings=None):
    settings = Path(settings or settings_path())
    data = _load(settings)
    backup = _backup(settings)
    hooks = data.get("hooks", {})
    _strip_ours(hooks)
    if not hooks:
        data.pop("hooks", None)
    _save(settings, data)
    return backup


def missing_hooks(settings=None):
    hooks = _load(settings or settings_path()).get("hooks", {})
    return [e for e in EVENTS
            if not any(_is_ours(h) for g in hooks.get(e, []) for h in g.get("hooks", []))]


def install_kitten(dest_dir=None):
    dest = Path(dest_dir or kitty_dir()) / KITTEN_SRC.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(KITTEN_SRC, dest)
    return dest


UNIT = """[Unit]
Description=platespinner: notifications for Claude Code sessions waiting on you
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart={daemon}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical-session.target
"""


def unit_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"))
    return base / "systemd" / "user" / "platespinnerd.service"


def install_service():
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(UNIT.format(daemon=DAEMON_PATH))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "platespinnerd.service"], check=True)
    return path
