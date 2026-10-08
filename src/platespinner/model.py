"""Plate state machine. Pure: no I/O, time is always passed in."""

import os
from dataclasses import asdict, dataclass, fields

from .config import Config, format_duration

SPINNING, WOBBLING, DROPPED = "spinning", "wobbling", "dropped"
BLOCKING = ("permission", "question")
REASON_LABEL = {"permission": "permission prompt", "question": "question", "done": "done"}

# Notification types with no bearing on whether the session waits on you.
_IGNORED_NOTIFICATIONS = {"auth_success", "agent_completed"}


@dataclass
class Plate:
    session_id: str
    pid: int | None = None
    cwd: str = ""
    branch: str | None = None
    name: str = ""
    custom_name: bool = False
    interactive: bool = True
    kitty_window_id: str | None = None
    kitty_listen_on: str | None = None
    tmux_socket: str | None = None
    tmux_pane: str | None = None
    state: str = SPINNING
    reason: str | None = None
    since: float = 0.0          # when the current state began
    updated: float = 0.0        # last time an event or status touched the state
    snooze_until: float | None = None
    muted: bool = False
    last_notified: float | None = None
    dropped_at: float | None = None

    @property
    def title(self):
        return f"{self.name} ({self.branch})" if self.branch else self.name


@dataclass
class Notice:
    plate_ids: list
    title: str
    body: str
    jump_id: str


def classify(e):
    """Hook event -> (state, reason, soft) or None to ignore.

    A soft wobble never overrides an existing wobble: idle_prompt fires ~60s
    after the turn ended, and must not relabel a pending permission prompt.
    """
    name = e.get("hook_event_name")
    subagent = bool(e.get("agent_id"))
    if name in ("SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure"):
        return None if subagent and name != "SessionStart" else (SPINNING, None, False)
    if name == "PreToolUse":
        if e.get("tool_name") == "AskUserQuestion":
            return (WOBBLING, "question", False)
        return None if subagent else (SPINNING, None, False)
    if name == "PermissionRequest":
        return (WOBBLING, "permission", False)
    if name == "Notification":
        kind = e.get("notification_type")
        if kind == "permission_prompt":
            return (WOBBLING, "permission", False)
        if kind == "elicitation_dialog":
            return (WOBBLING, "question", False)
        if kind == "idle_prompt":
            return (WOBBLING, "done", True)
        if kind:
            return None
        msg = (e.get("message") or "").lower()
        if "permission" in msg:
            return (WOBBLING, "permission", False)
        if "waiting for your input" in msg:
            return (WOBBLING, "done", True)
        return None
    if name in ("Stop", "StopFailure"):
        return None if subagent else (WOBBLING, "done", False)
    if name == "SessionEnd":
        return (DROPPED, None, False)
    return None


def _status_target(s):
    """Session file status -> (state, reason) or None."""
    status = s.get("status")
    if status == "busy":
        return (SPINNING, None)
    if status == "idle":
        return (WOBBLING, "done")
    if status == "waiting":
        detail = (s.get("waitingFor") or "").lower()
        return (WOBBLING, "permission" if ("permission" in detail or "sandbox" in detail) else "question")
    return None


class Board:
    def __init__(self, config: Config):
        self.config = config
        self.plates: dict[str, Plate] = {}
        self.pause_until: float | None = None

    # --- events ---------------------------------------------------------------

    def apply(self, e, now):
        sid = e.get("session_id")
        if not sid:
            return
        target = classify(e)
        if target is None and sid in self.plates:
            self._update_details(self.plates[sid], e)
            return
        p = self._get_or_create(sid, e.get("pid"), e.get("cwd") or "", now)
        self._update_details(p, e)
        if target is not None:
            state, reason, soft = target
            if soft and p.state == WOBBLING:
                return
            self._transition(p, state, reason, now)

    def _update_details(self, p, e):
        for key in ("pid", "branch", "kitty_window_id", "kitty_listen_on", "tmux_socket", "tmux_pane"):
            if e.get(key) is not None:
                setattr(p, key, e[key])
        if "interactive" in e:
            p.interactive = bool(e["interactive"])
        if e.get("cwd") and e["cwd"] != p.cwd:
            p.cwd = e["cwd"]
            if not p.custom_name:
                p.name = self._unique_name(p)

    def _get_or_create(self, sid, pid, cwd, now):
        if sid in self.plates:
            return self.plates[sid]
        p = Plate(session_id=sid, pid=pid, cwd=cwd, since=now, updated=now)
        # /clear and /resume start a new session id in the same process
        prev = next((q for q in self.plates.values() if pid is not None and q.pid == pid), None)
        if prev is not None:
            del self.plates[prev.session_id]
            p.name, p.custom_name = prev.name, prev.custom_name
            p.muted, p.snooze_until = prev.muted, prev.snooze_until
            p.branch, p.interactive = prev.branch, prev.interactive
            p.kitty_window_id, p.kitty_listen_on = prev.kitty_window_id, prev.kitty_listen_on
            p.tmux_socket, p.tmux_pane = prev.tmux_socket, prev.tmux_pane
        else:
            p.name = self._unique_name(p)
        self.plates[sid] = p
        return p

    def _unique_name(self, p):
        base = os.path.basename(p.cwd.rstrip("/")) or "session"
        taken = {q.name for q in self.plates.values() if q.session_id != p.session_id and q.state != DROPPED}
        if base not in taken:
            return base
        n = 2
        while f"{base} #{n}" in taken:
            n += 1
        return f"{base} #{n}"

    def _transition(self, p, state, reason, now):
        p.updated = now
        if state == DROPPED:
            if p.state != DROPPED:
                p.state, p.reason, p.dropped_at, p.since = DROPPED, None, now, now
            return
        if state == WOBBLING:
            if p.state == WOBBLING:
                p.reason = reason
                return
            p.state, p.reason, p.since, p.last_notified = WOBBLING, reason, now, None
            return
        if p.state == WOBBLING:
            p.muted, p.snooze_until = False, None
        if p.state != SPINNING:
            p.since = now
        p.state, p.reason, p.dropped_at, p.last_notified = SPINNING, None, None, None

    # --- reconciliation -------------------------------------------------------

    def reconcile(self, sessions, alive, now):
        for s in sessions:
            sid, pid = s.get("sessionId"), s.get("pid")
            if not sid or not pid or not alive(pid):
                continue
            target = _status_target(s)
            at = (s.get("statusUpdatedAt") or s.get("updatedAt") or 0) / 1000
            p = self.plates.get(sid)
            if p is None:
                p = self._get_or_create(sid, pid, s.get("cwd") or "", now)
                p.interactive = s.get("kind", "interactive") == "interactive"
                if target:
                    self._transition(p, target[0], target[1], at or now)
                    p.updated = at or now
                continue
            if p.pid is None:
                p.pid = pid
            if target and at > p.updated and p.state != DROPPED:
                state, reason = target
                if (state, reason) != (p.state, p.reason) and not (state == WOBBLING and p.state == WOBBLING):
                    self._transition(p, state, reason, at)
        for p in self.plates.values():
            if p.pid is not None and p.state != DROPPED and not alive(p.pid):
                self._transition(p, DROPPED, None, now)

    def sweep(self, now):
        for sid in [p.session_id for p in self.plates.values()
                    if p.state == DROPPED and now - p.dropped_at >= self.config.drop_grace]:
            del self.plates[sid]

    # --- user actions ---------------------------------------------------------

    def rename(self, sid, name):
        p = self.plates[sid]
        p.name, p.custom_name = name, True

    def snooze(self, sids, until):
        for sid in sids:
            if sid in self.plates:
                self.plates[sid].snooze_until = until

    def wake(self, sids):
        for sid in sids:
            if sid in self.plates:
                p = self.plates[sid]
                p.snooze_until, p.muted, p.last_notified = None, False, None

    def mute(self, sids):
        for sid in sids:
            if sid in self.plates:
                self.plates[sid].muted = True

    def find(self, key):
        live = [p for p in self.plates.values() if p.state != DROPPED] or list(self.plates.values())
        for match in (lambda p: p.name == key, lambda p: p.name.lower() == key.lower(),
                      lambda p: p.session_id == key, lambda p: p.session_id.startswith(key)):
            hits = [p for p in live if match(p)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise KeyError(f"ambiguous: {key!r} matches {', '.join(p.name for p in hits)}")
        raise KeyError(f"no plate matches {key!r}")

    # --- queries --------------------------------------------------------------

    def _shown(self, p):
        return p.state != DROPPED and (p.interactive or not self.config.ignore_non_interactive)

    def visible(self):
        shown = [p for p in self.plates.values() if self._shown(p)]
        return sorted(shown, key=lambda p: (p.state != WOBBLING, p.since))

    def wobbling(self):
        return [p for p in self.visible() if p.state == WOBBLING]

    def longest_waiting(self):
        w = self.wobbling()
        return w[0] if w else None

    def eligible(self, now, focused=frozenset()):
        if self.pause_until is not None and now < self.pause_until:
            return []
        return [p for p in self.wobbling()
                if p.session_id not in focused
                and not p.muted
                and not (p.snooze_until is not None and p.snooze_until > now)
                and now - p.since >= self.config.debounce]

    def has_eligible(self, now, focused=frozenset()):
        return bool(self.eligible(now, focused))

    def interval(self, p):
        return self.config.blocking_interval if p.reason in BLOCKING else self.config.done_interval

    def next_notice(self, now, focused=frozenset()):
        """The notice to post now, or None. Marks the plates as notified.

        focused: ids of plates whose terminal you are looking at. Looking at a
        plate counts as having seen it, so its reminder clock restarts.
        """
        for p in self.wobbling():
            if p.session_id in focused:
                p.last_notified = now
        elig = self.eligible(now, focused)
        if not any(p.last_notified is None or now >= p.last_notified + self.interval(p) for p in elig):
            return None
        for p in elig:
            p.last_notified = now
        return self.notice_for(elig, now)

    def notice_for(self, plates, now):
        if len(plates) == 1:
            p = plates[0]
            body = f"{REASON_LABEL[p.reason]} - waiting {format_duration(now - p.since)}"
            return Notice([p.session_id], p.title, body, p.session_id)
        body = "\n".join(f"{p.title}: {REASON_LABEL[p.reason]} - {format_duration(now - p.since)}" for p in plates)
        return Notice([p.session_id for p in plates], f"{len(plates)} plates wobbling", body, plates[0].session_id)

    # --- persistence ----------------------------------------------------------

    def to_dict(self):
        return {"pause_until": self.pause_until, "plates": [asdict(p) for p in self.plates.values()]}

    @classmethod
    def from_dict(cls, config, data):
        b = cls(config)
        b.pause_until = data.get("pause_until")
        known = {f.name for f in fields(Plate)}
        for d in data.get("plates", []):
            p = Plate(**{k: v for k, v in d.items() if k in known})
            b.plates[p.session_id] = p
        return b
