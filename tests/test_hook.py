import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "bin" / "platespinner-hook"


def run_hook(payload, runtime_dir):
    env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime_dir))
    t = time.monotonic()
    r = subprocess.run([sys.executable, str(HOOK)], input=payload, env=env, capture_output=True, text=True)
    return r, time.monotonic() - t


def test_hook_forwards_event_with_details(tmp_path):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(tmp_path / "platespinner.sock"))
    srv.listen(1)
    srv.settimeout(5)
    event = {"hook_event_name": "Stop", "session_id": "s1", "cwd": str(ROOT)}
    r, _ = run_hook(json.dumps(event), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    conn, _ = srv.accept()
    msg = json.loads(conn.makefile().readline())
    assert msg["type"] == "event"
    got = msg["event"]
    assert got["hook_event_name"] == "Stop"
    assert got["session_id"] == "s1"
    assert "ts" in got


def test_hook_is_silent_and_fast_without_daemon(tmp_path):
    r, elapsed = run_hook(json.dumps({"hook_event_name": "Stop", "session_id": "s1"}), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert elapsed < 0.5


def test_hook_survives_garbage_input(tmp_path):
    r, _ = run_hook("not json", tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_hook_does_not_forward_transcript_or_prompt_text(tmp_path):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(tmp_path / "platespinner.sock"))
    srv.listen(1)
    srv.settimeout(5)
    event = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "secret",
             "last_assistant_message": "secret", "tool_input": {"command": "secret"}}
    run_hook(json.dumps(event), tmp_path)
    conn, _ = srv.accept()
    assert "secret" not in conn.makefile().readline()
