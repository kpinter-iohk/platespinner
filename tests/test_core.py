import pytest

from platespinner.config import Config
from platespinner.core import Core
from platespinner.model import Board

T0 = 1_000_000.0


class FakeNotifier:
    def __init__(self):
        self.posted, self.closed, self.next_id = [], [], 10

    def post(self, replaces_id, title, body, snooze_label):
        nid = replaces_id or self.next_id
        self.next_id += 1
        self.posted.append((replaces_id, title, body, snooze_label))
        return nid

    def close(self, nid):
        self.closed.append(nid)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    clock = Clock()
    notifier = FakeNotifier()
    jumps = []
    saves = []
    core = Core(Board(Config()), notifier, jump=lambda plate, token: jumps.append((plate.session_id, token)),
                clock=clock, save=lambda d: saves.append(d))
    return core, notifier, jumps, clock, saves


def ev(name, sid="s1", **kw):
    e = {"hook_event_name": name, "session_id": sid, "cwd": "/w/api", "pid": 100, "interactive": True}
    e.update(kw)
    return e


def wobble(core, clock, sid="s1", **kw):
    core.on_event(ev("SessionStart", sid, **kw))
    core.on_event(ev("Stop", sid, **kw))
    clock.t += 5
    core.tick()


def test_posts_after_debounce_and_reuses_id_for_reminders(env):
    core, n, _, clock, _ = env
    wobble(core, clock)
    assert n.posted == [(0, "api", "done - waiting 5s", "Snooze 15m")]
    clock.t += 900
    core.tick()
    assert n.posted[-1][0] == 10


def test_click_jump_uses_token_and_dedups_double_signal(env):
    core, n, jumps, clock, _ = env
    wobble(core, clock)
    core.on_token(10, "tok")
    core.on_action(10, "default")
    core.on_token(10, "tok")
    core.on_action(10, "default")
    assert jumps == [("s1", "tok")]


def test_jump_on_a_later_reminder_is_not_swallowed_by_dedup(env):
    core, n, jumps, clock, _ = env
    wobble(core, clock)
    core.on_token(10, "t1")
    core.on_action(10, "jump")
    core.on_closed(10, 2)
    clock.t += 900
    core.tick()
    nid = core.current_id
    core.on_token(nid, "t2")
    core.on_action(nid, "jump")
    assert jumps == [("s1", "t1"), ("s1", "t2")]


def test_snooze_and_mute_actions(env):
    core, n, _, clock, _ = env
    wobble(core, clock)
    core.on_action(10, "snooze")
    assert core.board.plates["s1"].snooze_until == clock.t + 900
    core.on_action(10, "mute")
    assert core.board.plates["s1"].muted


def test_closed_notification_is_not_replaced(env):
    core, n, _, clock, _ = env
    wobble(core, clock)
    core.on_closed(10, 2)
    clock.t += 900
    core.tick()
    assert n.posted[-1][0] == 0


def test_notification_closes_when_nothing_wobbles(env):
    core, n, _, clock, _ = env
    wobble(core, clock)
    core.on_event(ev("UserPromptSubmit"))
    core.tick()
    assert n.closed == [10]
    assert core.current_id == 0


def test_actions_from_unknown_notifications_are_ignored(env):
    core, n, jumps, clock, _ = env
    wobble(core, clock)
    core.on_action(999, "jump")
    assert jumps == []


def test_coalesced_jump_goes_to_longest_still_waiting(env):
    core, n, jumps, clock, _ = env
    core.on_event(ev("SessionStart", "a", pid=1))
    core.on_event(ev("SessionStart", "b", pid=2))
    core.on_event(ev("Stop", "a", pid=1))
    clock.t += 1
    core.on_event(ev("Stop", "b", pid=2))
    clock.t += 5
    core.tick()
    assert n.posted[-1][1] == "2 plates wobbling"
    core.on_event(ev("UserPromptSubmit", "a", pid=1))
    core.on_action(10, "default")
    assert jumps == [("b", "")]


def test_state_saved_on_change(env):
    core, _, _, clock, saves = env
    core.on_event(ev("SessionStart"))
    assert saves and saves[-1]["plates"][0]["session_id"] == "s1"


# --- commands ----------------------------------------------------------------

def cmd(core, cmd_name, **kw):
    return core.on_command({"type": "cmd", "cmd": cmd_name, **kw})


def test_list_command(env):
    core, _, _, clock, _ = env
    wobble(core, clock)
    r = cmd(core, "list")
    assert r["ok"]
    assert r["plates"][0]["name"] == "api"
    assert r["plates"][0]["wait"] == 5


def test_snooze_wake_mute_name_commands(env):
    core, _, _, clock, _ = env
    wobble(core, clock)
    assert cmd(core, "snooze", target="api", duration="1h")["ok"]
    assert core.board.plates["s1"].snooze_until == clock.t + 3600
    assert cmd(core, "wake", target="api")["ok"]
    assert core.board.plates["s1"].snooze_until is None
    assert cmd(core, "mute", target="s1")["ok"]
    assert core.board.plates["s1"].muted
    assert cmd(core, "name", target="s1", name="backend")["ok"]
    assert core.board.plates["s1"].name == "backend"


def test_snooze_all_and_pause(env):
    core, _, _, clock, _ = env
    wobble(core, clock, "a", pid=1)
    wobble(core, clock, "b", pid=2)
    cmd(core, "snooze", all=True, duration="30m")
    assert all(p.snooze_until == clock.t + 1800 for p in core.board.plates.values())
    cmd(core, "pause", duration="1h")
    assert core.board.pause_until == clock.t + 3600


def test_jump_command_defaults_to_longest_waiting(env):
    core, _, jumps, clock, _ = env
    wobble(core, clock)
    assert cmd(core, "jump")["ok"]
    assert jumps == [("s1", "")]


def test_unknown_target_reports_error(env):
    core, _, _, _, _ = env
    r = cmd(core, "mute", target="nope")
    assert not r["ok"] and "nope" in r["error"]


def test_reconcile_enriches_plates_missing_details(env):
    core, _, _, clock, _ = env
    sessions = [{"pid": 7, "sessionId": "x", "cwd": "/w/docs", "kind": "interactive", "status": "busy",
                 "statusUpdatedAt": clock.t * 1000}]
    details = {7: {"kitty_window_id": "3", "kitty_listen_on": "unix:/tmp/kitty-1"}}
    core.reconcile(sessions, alive=lambda pid: True,
                   enrich=lambda p: {**details.get(p.pid, {}), "branch": "main"})
    p = core.board.plates["x"]
    assert (p.kitty_window_id, p.kitty_listen_on, p.branch) == ("3", "unix:/tmp/kitty-1", "main")


def test_tick_skips_the_focused_plate_and_closes_its_banner(env):
    core, n, _, clock, _ = env
    wobble(core, clock)
    assert n.posted
    core.focused = lambda plates: {p.session_id for p in plates}
    clock.t += 1
    core.tick()
    assert n.closed == [10]


def test_focus_probe_not_called_when_nothing_wobbles(env):
    core, n, _, clock, _ = env
    calls = []
    core.focused = lambda plates: calls.append(plates) or set()
    core.on_event(ev("SessionStart"))
    core.tick()
    assert calls == []
