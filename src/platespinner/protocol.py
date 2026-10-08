"""Daemon socket: one JSON object per line."""

import json
import os
import socket
from pathlib import Path


def socket_path() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "platespinner.sock"


def send(msg, timeout=0.05, want_reply=False):
    """Send one message. Returns the decoded reply if want_reply, else None.

    Raises OSError when the daemon is unreachable.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(socket_path()))
        s.sendall(json.dumps(msg).encode() + b"\n")
        if not want_reply:
            return None
        s.shutdown(socket.SHUT_WR)
        line = s.makefile("rb").readline()
    if not line:
        raise OSError("daemon closed the connection without replying")
    return json.loads(line)
