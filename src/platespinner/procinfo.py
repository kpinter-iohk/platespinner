"""Reads /proc and .git without spawning anything: the hook must stay fast."""

import json
import os
from pathlib import Path

PROC = Path("/proc")


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))


def sessions_dir() -> Path:
    return claude_dir() / "sessions"


def _stat(pid, proc_root):
    """-> (comm, ppid, tty_nr) or None."""
    try:
        raw = (proc_root / str(pid) / "stat").read_text()
    except OSError:
        return None
    head, _, rest = raw.rpartition(")")
    comm = head.partition("(")[2]
    f = rest.split()
    return comm, int(f[1]), int(f[4])


def environ_of(pid, proc_root=PROC):
    try:
        raw = (proc_root / str(pid) / "environ").read_bytes()
    except OSError:
        return {}
    pairs = (item.split(b"=", 1) for item in raw.split(b"\0") if b"=" in item)
    return {k.decode(errors="replace"): v.decode(errors="replace") for k, v in pairs}


def _session_file(pid, sessions):
    try:
        return json.loads((sessions / f"{pid}.json").read_text())
    except (OSError, ValueError):
        return None


def inspect(start_pid, proc_root=PROC, sessions=None):
    """Find the claude process at or above start_pid; return its details."""
    sessions = Path(sessions) if sessions else sessions_dir()
    pid = start_pid
    for _ in range(16):
        st = _stat(pid, proc_root)
        if st is None:
            return {}
        comm, ppid, tty = st
        session = _session_file(pid, sessions)
        if session is not None or comm == "claude":
            env = environ_of(pid, proc_root)
            if session is not None and "kind" in session:
                interactive = session["kind"] == "interactive"
            else:
                interactive = tty != 0
            tmux = env.get("TMUX")
            return {"pid": pid, "kitty_window_id": env.get("KITTY_WINDOW_ID"),
                    "kitty_listen_on": env.get("KITTY_LISTEN_ON"), "interactive": interactive,
                    "tmux_socket": tmux.split(",")[0] if tmux else None,
                    "tmux_pane": env.get("TMUX_PANE") if tmux else None}
        if ppid <= 1:
            return {}
        pid = ppid
    return {}


def git_branch(cwd):
    """Branch name for cwd, short sha if detached, None outside a repo."""
    if not cwd:
        return None
    d = Path(cwd)
    for cand in (d, *d.parents):
        dot = cand / ".git"
        try:
            if dot.is_dir():
                head = (dot / "HEAD").read_text().strip()
            elif dot.is_file():
                gitdir = dot.read_text().strip().removeprefix("gitdir:").strip()
                head = ((cand / gitdir) / "HEAD").read_text().strip()
            else:
                continue
        except OSError:
            return None
        if head.startswith("ref:"):
            return head[4:].strip().removeprefix("refs/heads/")
        return head[:7]
    return None


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_sessions(sessions=None):
    out = []
    for f in (Path(sessions) if sessions else sessions_dir()).glob("*.json"):
        try:
            out.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    return out
