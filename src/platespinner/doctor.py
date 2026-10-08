"""platespinner doctor: check every moving part."""

import filecmp
import glob
import json
import re
import subprocess

from . import install, protocol
from .install import kitten_path


def _run(argv, timeout=5):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    return r.returncode, (r.stdout + r.stderr).strip()


def check_daemon():
    try:
        r = protocol.send({"type": "cmd", "cmd": "ping"}, timeout=2, want_reply=True)
    except OSError as e:
        return False, f"not reachable at {protocol.socket_path()} ({e}); systemctl --user start platespinnerd"
    return True, f"pid {r['pid']}, tracking {r['plates']} plate(s)"


def check_service():
    code, out = _run(["systemctl", "--user", "is-enabled", "platespinnerd.service"])
    return code == 0, out or "not installed; run platespinner install-service"


def check_hooks():
    missing = install.missing_hooks()
    if missing:
        return False, f"missing for {', '.join(missing)}; run platespinner install-hooks"
    return True, f"all {len(install.EVENTS)} events in {install.settings_path()}"


def kitty_sockets():
    return sorted(glob.glob("/tmp/kitty-*"))


def check_kitty_config():
    conf = install.kitty_dir() / "kitty.conf"
    try:
        text = conf.read_text()
    except OSError:
        return False, f"{conf} not found"
    rc = re.search(r"^\s*allow_remote_control\s+(\S+)", text, re.M)
    listen = re.search(r"^\s*listen_on\s+(\S+)", text, re.M)
    if not rc or rc.group(1) not in ("yes", "socket", "socket-only", "password"):
        return False, "set 'allow_remote_control yes' in kitty.conf"
    if not listen:
        return False, "set 'listen_on unix:/tmp/kitty' in kitty.conf"
    return True, f"allow_remote_control {rc.group(1)}, listen_on {listen.group(1)}"


def check_kitty_sockets():
    socks = kitty_sockets()
    if not socks:
        return None, "no kitty running; socket check skipped"
    bad = [s for s in socks if _run(["kitty", "@", "--to", f"unix:{s}", "ls"])[0] != 0]
    if bad:
        return False, f"unreachable: {', '.join(bad)}"
    return True, f"{len(socks)} kitty instance(s) reachable"


def check_kitten():
    dest = kitten_path()
    if not dest.exists():
        return False, f"{dest} missing; run platespinner install-kitten"
    if not filecmp.cmp(dest, install.KITTEN_SRC, shallow=False):
        return False, f"{dest} differs from {install.KITTEN_SRC}; run platespinner install-kitten"
    return True, str(dest)


def check_focus():
    """Run the kitten against the already-active window of a live kitty."""
    socks = kitty_sockets()
    if not socks or not kitten_path().exists():
        return None, "needs a running kitty and the kitten; skipped"
    to = f"unix:{socks[0]}"
    code, out = _run(["kitty", "@", "--to", to, "ls"])
    if code != 0:
        return False, out
    try:
        osw = next(w for w in json.loads(out) if w.get("is_active")) if out else None
        tab = next(t for t in osw["tabs"] if t.get("is_active"))
        win = next(w for w in tab["windows"] if w.get("is_active"))
    except (ValueError, StopIteration, KeyError, TypeError):
        return False, "could not find the active kitty window"
    code, out = _run(["kitty", "@", "--to", to, "kitten", "--match", f"id:{win['id']}", str(kitten_path()), ""])
    if code == 0 and out.startswith(f"focused {win['id']}"):
        return True, f"kitten focused window {win['id']}"
    return False, f"kitten failed ({out}); kitty may have changed its internals. Jump falls back to a tab switch."


def check_appindicator():
    try:
        import gi
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3  # noqa: F401
    except (ImportError, ValueError) as e:
        return False, f"{e}; sudo apt install gir1.2-ayatanaappindicator3-0.1"
    code, out = _run(["gnome-extensions", "info", "ubuntu-appindicators@ubuntu.com"])
    state = re.search(r"(?:State|Enabled):\s*(\S+)", out or "")
    if code != 0:
        return False, "ubuntu-appindicators GNOME extension not found"
    return True, f"bindings present, extension {state.group(1) if state else 'installed'}"


def check_claude():
    code, out = _run(["claude", "--version"])
    return code == 0, out or "claude not found on PATH"


CHECKS = [
    ("daemon", check_daemon),
    ("service", check_service),
    ("hooks", check_hooks),
    ("kitty config", check_kitty_config),
    ("kitty sockets", check_kitty_sockets),
    ("kitten", check_kitten),
    ("focus test", check_focus),
    ("tray", check_appindicator),
    ("claude code", check_claude),
]


def run():
    failed = 0
    for name, check in CHECKS:
        try:
            ok, detail = check()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"check crashed: {e}"
        mark = {True: "ok  ", False: "FAIL", None: "skip"}[ok]
        failed += ok is False
        print(f"[{mark}] {name:14} {detail}")
    return 1 if failed else 0
