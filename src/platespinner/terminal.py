"""Where a plate lives (kitty window, optionally tmux pane), whether you are
looking at it, and how to jump there."""

import json
import os
import subprocess
from dataclasses import dataclass

from . import procinfo


def log(*args):
    from .core import log as _log
    _log(*args)


def _run(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return r.returncode, r.stdout if r.returncode == 0 else (r.stderr or r.stdout)


@dataclass
class TmuxTarget:
    socket: str
    pane: str
    client_tty: str | None
    pane_visible: bool       # the client shows this pane right now


@dataclass
class Location:
    kitty_listen_on: str | None
    kitty_window_id: str | None
    tmux: TmuxTarget | None


def kitty_commands(listen_on, window_id, token, kitten):
    if not listen_on or not window_id:
        return []
    base = ["kitty", "@", "--to", listen_on]
    cmds = []
    if kitten:
        cmds.append(base + ["kitten", "--match", f"id:{window_id}", kitten, token])
    cmds.append(base + ["focus-window", "--match", f"id:{window_id}"])
    return cmds


class Terminals:
    def __init__(self, run=_run, environ=procinfo.environ_of):
        self.run = run
        self.environ = environ

    # --- tmux -----------------------------------------------------------------

    def _tmux(self, plate, *args):
        return self.run(["tmux", "-S", plate.tmux_socket, *args])

    def _tmux_target(self, plate):
        code, out = self._tmux(plate, "display-message", "-p", "-t", plate.tmux_pane,
                               "#{session_name}\t#{window_active}\t#{pane_active}")
        if code != 0 or out.count("\t") < 2:
            return None, {}
        session, window_active, pane_active = out.strip("\n").split("\t")[:3]
        code, out = self._tmux(plate, "list-clients", "-F",
                               "#{client_pid}\t#{client_tty}\t#{client_session}\t#{client_activity}")
        clients = []
        for line in out.splitlines() if code == 0 else []:
            parts = line.split("\t")
            if len(parts) == 4 and parts[0].isdigit():
                clients.append((int(parts[0]), parts[1], parts[2], int(parts[3] or 0)))
        # prefer a client showing the pane's session, then the most recently used one
        clients.sort(key=lambda c: (c[2] == session, c[3]), reverse=True)
        if not clients:
            return TmuxTarget(plate.tmux_socket, plate.tmux_pane, None, False), {}
        pid, tty, client_session, _ = clients[0]
        visible = client_session == session and window_active == "1" and pane_active == "1"
        return TmuxTarget(plate.tmux_socket, plate.tmux_pane, tty, visible), self.environ(pid)

    # --- public ---------------------------------------------------------------

    def locate(self, plate):
        if plate.tmux_socket and plate.tmux_pane:
            # kitty ids in claude's env come from whichever kitty started the
            # tmux server; the attached client's env says where it is now.
            target, env = self._tmux_target(plate)
            if target is not None:
                return Location(env.get("KITTY_LISTEN_ON"), env.get("KITTY_WINDOW_ID"), target)
        return Location(plate.kitty_listen_on, plate.kitty_window_id, None)

    def _kitty_focused_window(self, listen_on):
        code, out = self.run(["kitty", "@", "--to", listen_on, "ls"])
        if code != 0:
            return None
        try:
            for osw in json.loads(out):
                if not osw.get("is_focused"):
                    continue
                for tab in osw.get("tabs", []):
                    for w in tab.get("windows", []):
                        if w.get("is_focused"):
                            return str(w["id"])
        except (ValueError, TypeError, KeyError):
            pass
        return None

    def focused(self, plates):
        """Ids of the plates whose terminal pane has keyboard focus."""
        cache, out = {}, set()
        for p in plates:
            if not (p.tmux_pane or p.kitty_window_id):
                continue
            loc = self.locate(p)
            if loc.tmux is not None and not loc.tmux.pane_visible:
                continue
            if not loc.kitty_listen_on or not loc.kitty_window_id:
                continue
            if loc.kitty_listen_on not in cache:
                cache[loc.kitty_listen_on] = self._kitty_focused_window(loc.kitty_listen_on)
            if cache[loc.kitty_listen_on] == loc.kitty_window_id:
                out.add(p.session_id)
        return out

    def jump(self, plate, token, kitten):
        """Show the plate's tmux pane, then raise its kitty window."""
        loc = self.locate(plate)
        ok = False
        if loc.tmux is not None and loc.tmux.client_tty:
            t = loc.tmux
            ok = self._tmux(plate, "switch-client", "-c", t.client_tty, "-t", t.pane)[0] == 0
            self._tmux(plate, "select-window", "-t", t.pane)
            self._tmux(plate, "select-pane", "-t", t.pane)
        kitten = kitten if kitten and os.path.exists(kitten) else None
        for argv in kitty_commands(loc.kitty_listen_on, loc.kitty_window_id, token, kitten):
            code, out = self.run(argv)
            if code == 0 and "no such window" not in out:
                log(f"jump: {plate.name} via {argv[4]}" + (" with token" if token else " without token"))
                return True
            log(f"jump: {argv[4]} failed: {out.strip()}")
        if not ok:
            log(f"jump: {plate.name} is not in a reachable kitty window or tmux pane")
        return ok
