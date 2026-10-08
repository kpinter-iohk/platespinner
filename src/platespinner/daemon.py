"""platespinnerd: the long-running process under systemd --user."""

import json
import os
import queue
import socketserver
import sys
import threading
import time
from pathlib import Path

from gi.repository import GLib

from . import procinfo, protocol
from .config import Config, config_path, load_config
from .core import Core, log
from .terminal import Terminals
from .install import ROOT, kitten_path
from .model import Board

ICON_DIR = ROOT / "icons"


def state_path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "platespinner" / "state.json"


def save_state(data):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def load_board(config):
    try:
        board = Board.from_dict(config, json.loads(state_path().read_text()))
    except FileNotFoundError:
        return Board(config)
    except (OSError, ValueError, TypeError) as e:
        log(f"ignoring unreadable state file: {e}")
        return Board(config)
    # The previous process closed its notification on exit (and a new D-Bus
    # connection could not replace it anyway), so re-post right away.
    for p in board.plates.values():
        p.last_notified = None
    return board


def enrich(plate):
    details = procinfo.inspect(plate.pid) if plate.pid else {}
    details["branch"] = procinfo.git_branch(plate.cwd)
    return details


def on_main(fn, *args):
    """Run fn on the GLib main thread and return its result."""
    box = queue.Queue(maxsize=1)

    def run():
        try:
            box.put((True, fn(*args)))
        except Exception as e:  # noqa: BLE001
            box.put((False, e))
        return False

    GLib.idle_add(run)
    ok, value = box.get(timeout=10)
    if not ok:
        raise value
    return value


def _once(fn, *args):
    fn(*args)
    return GLib.SOURCE_REMOVE


class Handler(socketserver.StreamRequestHandler):
    timeout = 2

    def handle(self):
        try:
            msg = json.loads(self.rfile.readline())
        except (OSError, ValueError):
            return
        core = self.server.core
        if msg.get("type") == "event":
            GLib.idle_add(_once, core.on_event, msg["event"])
        elif msg.get("type") == "cmd":
            try:
                reply = on_main(core.on_command, msg)
            except Exception as e:  # noqa: BLE001
                reply = {"ok": False, "error": f"daemon error: {e}"}
            self.wfile.write(json.dumps(reply).encode() + b"\n")


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(core):
    path = protocol.socket_path()
    try:
        protocol.send({"type": "cmd", "cmd": "ping"}, timeout=1, want_reply=True)
        sys.exit("platespinnerd: already running")
    except OSError:
        pass
    path.unlink(missing_ok=True)
    old = os.umask(0o177)
    try:
        server = Server(str(path), Handler)
    finally:
        os.umask(old)
    server.core = core
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    loop = GLib.MainLoop()
    cfg_file = config_path()
    try:
        config = load_config(cfg_file)
    except ValueError as e:
        log(f"bad config, using defaults: {e}")
        config = Config()
    board = load_board(config)
    terminals = Terminals()
    core = Core(board, notifier=None, jump=lambda p, tok: terminals.jump(p, tok, str(kitten_path())),
                clock=time.time, save=save_state)
    core.focused = terminals.focused
    core.quit = lambda: GLib.idle_add(_once, loop.quit)

    from .notifier import Notifier
    core.notifier = Notifier(str(ICON_DIR / "platespinner-wobble.svg"),
                             core.on_token, core.on_action, core.on_closed)

    tray = None
    try:
        from .tray import Tray
        tray = Tray(ICON_DIR, core.on_command)
    except (ImportError, ValueError) as e:
        log(f"tray disabled: {e}")

    def refresh_tray():
        if tray is not None:
            c = core.board.config
            tray.refresh(core.cmd_list({}), c.snooze_presets, c.snooze_default)

    core.on_change = refresh_tray

    server = serve(core)
    mtime = [cfg_file.stat().st_mtime if cfg_file.exists() else None]

    def reload_config():
        m = cfg_file.stat().st_mtime if cfg_file.exists() else None
        if m == mtime[0]:
            return
        mtime[0] = m
        try:
            core.board.config = load_config(cfg_file)
            log("config reloaded")
            schedule_reconcile()
        except ValueError as e:
            log(f"config not reloaded: {e}")

    def reconcile():
        reload_config()
        core.reconcile(procinfo.read_sessions(), procinfo.pid_alive, enrich)
        return GLib.SOURCE_CONTINUE

    reconcile_source = [None]

    def schedule_reconcile():
        if reconcile_source[0] is not None:
            GLib.source_remove(reconcile_source[0])
        secs = max(1, int(core.board.config.reconcile_interval))
        reconcile_source[0] = GLib.timeout_add_seconds(secs, reconcile)

    def tick():
        core.tick()
        refresh_tray()
        return GLib.SOURCE_CONTINUE

    reconcile()
    schedule_reconcile()
    refresh_tray()
    GLib.timeout_add_seconds(1, tick)
    for sig in (2, 15):  # SIGINT, SIGTERM
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: loop.quit() or GLib.SOURCE_REMOVE)
    log(f"listening on {protocol.socket_path()}")
    try:
        loop.run()
    finally:
        server.shutdown()
        protocol.socket_path().unlink(missing_ok=True)
        if core.current_id:
            core.notifier.close(core.current_id)
