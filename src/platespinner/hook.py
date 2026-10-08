"""Run by Claude Code on hook events. Must exit 0 fast and print nothing:
stdout from some hook events is injected into the conversation."""

import json
import os
import sys
import time

# Forwarded hook fields. Prompt text, tool input and transcript contents are not.
_FIELDS = ("hook_event_name", "session_id", "cwd", "notification_type", "message",
           "tool_name", "source", "reason", "agent_id")


def build_event(raw, parent_pid):
    from . import procinfo
    e = {k: raw[k] for k in _FIELDS if k in raw}
    e.update(procinfo.inspect(parent_pid))
    e["branch"] = procinfo.git_branch(raw.get("cwd"))
    e["ts"] = time.time()
    return e


def main():
    try:
        raw = json.load(sys.stdin)
        if isinstance(raw, dict) and raw.get("session_id"):
            from .protocol import send
            send({"type": "event", "event": build_event(raw, os.getppid())})
    except Exception:
        pass
    sys.exit(0)
