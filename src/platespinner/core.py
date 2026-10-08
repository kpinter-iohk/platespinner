"""Daemon logic, free of GLib so it can be tested with fakes.

daemon.py wires it to D-Bus, the socket, timers and the tray.
"""

import json
import os
import sys

from .config import format_duration, parse_duration
from .model import WOBBLING

DETAIL_KEYS = ("kitty_window_id", "kitty_listen_on", "tmux_socket", "tmux_pane", "branch")
DEDUP_WINDOW = 1.0  # GNOME delivers every click signal twice (report, gotcha 1)


def log(*args):
    print("platespinnerd:", *args, file=sys.stderr, flush=True)


class Core:
    def __init__(self, board, notifier, jump, clock, save):
        self.board = board
        self.notifier = notifier
        self.jump = jump
        self.clock = clock
        self._save = save
        self.current_id = 0
        self.notices = {}       # notification id -> plate ids it was about
        self.tokens = {}        # notification id -> activation token
        self.seen = {}          # (id, action) -> time handled
        self._saved = None
        self.on_change = lambda: None
        self.quit = lambda: None
        self.focused = lambda plates: set()   # plates -> ids whose terminal has focus

    # --- persistence ----------------------------------------------------------

    def save(self):
        data = self.board.to_dict()
        blob = json.dumps(data, sort_keys=True)
        if blob != self._saved:
            self._saved = blob
            self._save(data)
            self.on_change()

    # --- inputs ---------------------------------------------------------------

    def on_event(self, event):
        self.board.apply(event, self.clock())
        self.save()

    def reconcile(self, sessions, alive, enrich=None):
        """enrich(plate) -> dict of details (kitty ids, branch) read from the system."""
        self.board.reconcile(sessions, alive, self.clock())
        if enrich is not None:
            for p in self.board.visible():
                for key, value in enrich(p).items():
                    if value is not None and key in DETAIL_KEYS:
                        setattr(p, key, value)
        self.save()

    def tick(self):
        now = self.clock()
        self.board.sweep(now)
        wobbling = self.board.wobbling()
        focused = self.focused(wobbling) if wobbling else set()
        notice = self.board.next_notice(now, focused)
        if notice is not None:
            label = f"Snooze {format_duration(self.board.config.snooze_default)}"
            try:
                nid = self.notifier.post(self.current_id, notice.title, notice.body, label)
            except Exception as e:  # noqa: BLE001 - a dead replaces_id must not stop reminders
                log(f"post failed ({e}); retrying as a new notification")
                nid = self.notifier.post(0, notice.title, notice.body, label)
            self.current_id = nid
            self.tokens.pop(nid, None)
            self.notices[nid] = notice.plate_ids
            while len(self.notices) > 20:
                del self.notices[next(iter(self.notices))]
        elif self.current_id and not self.board.has_eligible(now, focused):
            self.notifier.close(self.current_id)
            self.current_id = 0
        self.save()

    def on_token(self, nid, token):
        self.tokens[nid] = token

    def on_action(self, nid, key):
        now = self.clock()
        if now - self.seen.get((nid, key), float("-inf")) < DEDUP_WINDOW:
            return
        self.seen = {k: t for k, t in self.seen.items() if now - t < DEDUP_WINDOW}
        self.seen[(nid, key)] = now
        sids = self.notices.get(nid)
        if sids is None:
            return
        token = self.tokens.pop(nid, "")
        if key in ("default", "jump"):
            target = self._jump_target(sids)
            if target is not None:
                self.jump(target, token)
        elif key == "snooze":
            self.board.snooze(sids, now + self.board.config.snooze_default)
        elif key == "mute":
            self.board.mute(sids)
        self.save()

    def on_closed(self, nid, reason):
        if nid == self.current_id:
            self.current_id = 0

    def _jump_target(self, sids):
        plates = [self.board.plates[s] for s in sids if s in self.board.plates]
        waiting = sorted((p for p in plates if p.state == WOBBLING), key=lambda p: p.since)
        return (waiting or plates or [None])[0]

    # --- commands -------------------------------------------------------------

    def on_command(self, msg):
        handler = getattr(self, "cmd_" + str(msg.get("cmd", "")), None)
        if handler is None:
            return {"ok": False, "error": f"unknown command {msg.get('cmd')!r}"}
        try:
            reply = handler(msg)
        except (KeyError, ValueError) as e:
            return {"ok": False, "error": str(e.args[0] if e.args else e)}
        self.save()
        return {"ok": True, **(reply or {})}

    def _targets(self, msg):
        if msg.get("all"):
            return [p.session_id for p in self.board.visible()]
        return [self.board.find(msg["target"]).session_id]

    def describe(self, p, now):
        return {"session_id": p.session_id, "name": p.name, "branch": p.branch, "state": p.state,
                "reason": p.reason, "wait": now - p.since, "muted": p.muted,
                "snooze_left": max(0, p.snooze_until - now) if p.snooze_until and p.snooze_until > now else 0,
                "term": "tmux" if p.tmux_pane else "kitty" if p.kitty_window_id and p.kitty_listen_on else "-",
                "cwd": p.cwd}

    def cmd_list(self, msg):
        now = self.clock()
        paused = self.board.pause_until if self.board.pause_until and self.board.pause_until > now else None
        return {"plates": [self.describe(p, now) for p in self.board.visible()],
                "paused_left": paused - now if paused else 0}

    def cmd_snooze(self, msg):
        dur = parse_duration(msg.get("duration") or self.board.config.snooze_default)
        self.board.snooze(self._targets(msg), self.clock() + dur)

    def cmd_wake(self, msg):
        self.board.wake(self._targets(msg))

    def cmd_mute(self, msg):
        self.board.mute(self._targets(msg))

    def cmd_name(self, msg):
        self.board.rename(self.board.find(msg["target"]).session_id, msg["name"])

    def cmd_pause(self, msg):
        dur = parse_duration(msg["duration"])
        self.board.pause_until = self.clock() + dur if dur else None

    def cmd_jump(self, msg):
        p = self.board.find(msg["target"]) if msg.get("target") else self.board.longest_waiting()
        if p is None:
            raise KeyError("no plate is waiting")
        self.jump(p, "")
        return {"name": p.name}

    def cmd_quit(self, msg):
        self.quit()

    def cmd_ping(self, msg):
        return {"pid": os.getpid(), "plates": len(self.board.plates), "notification": self.current_id}
