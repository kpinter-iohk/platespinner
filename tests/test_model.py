import pytest

from platespinner.config import Config
from platespinner.model import Board

T0 = 1_000_000.0


def ev(name, sid="s1", **kw):
    e = {"hook_event_name": name, "session_id": sid, "cwd": "/home/u/dev/api", "pid": 100,
         "interactive": True, "branch": "main"}
    e.update(kw)
    return e


@pytest.fixture
def board():
    return Board(Config())


def started(board, sid="s1", now=T0, **kw):
    board.apply(ev("SessionStart", sid, source="startup", **kw), now)
    return board.plates[sid]


# --- transitions -------------------------------------------------------------

def test_session_start_creates_spinning_plate_named_after_cwd(board):
    p = started(board, kitty_window_id="7", kitty_listen_on="unix:/tmp/kitty-5")
    assert p.state == "spinning"
    assert p.name == "api"
    assert p.branch == "main"
    assert p.kitty_window_id == "7"
    assert p.kitty_listen_on == "unix:/tmp/kitty-5"


def test_stop_wobbles_with_reason_done(board):
    started(board)
    board.apply(ev("Stop"), T0 + 1)
    p = board.plates["s1"]
    assert (p.state, p.reason, p.since) == ("wobbling", "done", T0 + 1)


@pytest.mark.parametrize("event,reason", [
    (ev("PermissionRequest", tool_name="Bash"), "permission"),
    (ev("Notification", notification_type="permission_prompt", message="Claude needs your permission"), "permission"),
    (ev("Notification", notification_type="elicitation_dialog", message="x"), "question"),
    (ev("PreToolUse", tool_name="AskUserQuestion"), "question"),
    (ev("Notification", notification_type="idle_prompt", message="Claude is waiting for your input"), "done"),
])
def test_waiting_events_wobble(board, event, reason):
    started(board)
    board.apply(event, T0 + 1)
    p = board.plates["s1"]
    assert (p.state, p.reason) == ("wobbling", reason)


def test_notification_reason_falls_back_to_message_text(board):
    started(board)
    board.apply(ev("Notification", message="Claude needs your permission to use Bash"), T0 + 1)
    assert board.plates["s1"].reason == "permission"


@pytest.mark.parametrize("event", [
    ev("UserPromptSubmit"), ev("PreToolUse", tool_name="Bash"), ev("PostToolUse", tool_name="Bash"),
    ev("PostToolUse", tool_name="AskUserQuestion"), ev("PostToolUseFailure", tool_name="Bash"),
])
def test_work_events_spin(board, event):
    started(board)
    board.apply(ev("Stop"), T0 + 1)
    board.apply(event, T0 + 2)
    assert board.plates["s1"].state == "spinning"


@pytest.mark.parametrize("event", [
    ev("SubagentStop"), ev("Notification", notification_type="auth_success", message="ok"),
])
def test_ignored_events_change_nothing(board, event):
    started(board)
    board.apply(ev("Stop"), T0 + 1)
    board.apply(event, T0 + 2)
    p = board.plates["s1"]
    assert (p.state, p.reason) == ("wobbling", "done")


def test_idle_prompt_does_not_downgrade_a_permission_wobble(board):
    started(board)
    board.apply(ev("PermissionRequest", tool_name="Bash"), T0 + 1)
    board.apply(ev("Notification", notification_type="idle_prompt", message="waiting"), T0 + 61)
    p = board.plates["s1"]
    assert (p.reason, p.since) == ("permission", T0 + 1)


def test_repeat_wobble_keeps_original_since(board):
    started(board)
    board.apply(ev("Stop"), T0 + 1)
    board.apply(ev("Notification", notification_type="idle_prompt", message="w"), T0 + 61)
    assert board.plates["s1"].since == T0 + 1


def test_event_for_unknown_session_creates_plate(board):
    board.apply(ev("Stop", "s9"), T0)
    assert board.plates["s9"].state == "wobbling"


def test_session_end_drops_and_sweep_removes_after_grace(board):
    started(board)
    board.apply(ev("SessionEnd", reason="prompt_input_exit"), T0 + 1)
    assert board.plates["s1"].state == "dropped"
    board.sweep(T0 + 10)
    assert "s1" in board.plates
    board.sweep(T0 + 32)
    assert "s1" not in board.plates


def test_same_pid_new_session_carries_over_name_mute_snooze(board):
    started(board)
    board.rename("s1", "backend")
    board.mute(["s1"])
    board.snooze(["s1"], T0 + 500)
    board.apply(ev("SessionEnd", reason="clear"), T0 + 1)
    board.apply(ev("SessionStart", "s2", source="clear"), T0 + 2)
    assert "s1" not in board.plates
    p = board.plates["s2"]
    assert (p.name, p.muted, p.snooze_until) == ("backend", True, T0 + 500)


def test_two_sessions_in_one_directory_get_suffixed_names(board):
    started(board, "s1", pid=1)
    started(board, "s2", pid=2)
    assert board.plates["s2"].name == "api #2"


def test_branch_updates_from_later_events(board):
    started(board)
    board.apply(ev("UserPromptSubmit", branch="feat/x"), T0 + 1)
    assert board.plates["s1"].branch == "feat/x"


def test_missing_kitty_fields_do_not_erase_known_ones(board):
    started(board, kitty_window_id="7", kitty_listen_on="unix:/tmp/kitty-5")
    board.apply(ev("Stop"), T0 + 1)
    assert board.plates["s1"].kitty_window_id == "7"


# --- notifications -----------------------------------------------------------

def test_debounce_suppresses_notification_until_wobble_settles(board):
    started(board)
    board.apply(ev("Stop"), T0 + 1)
    assert board.next_notice(T0 + 2) is None
    n = board.next_notice(T0 + 4)
    assert n is not None and n.plate_ids == ["s1"]


def test_wobble_that_spins_again_within_debounce_never_fires(board):
    started(board)
    board.apply(ev("PermissionRequest"), T0 + 1)
    board.apply(ev("PostToolUse"), T0 + 2)
    for t in range(0, 1000, 1):
        assert board.next_notice(T0 + t) is None


def test_single_plate_notice_text(board):
    started(board)
    board.apply(ev("PermissionRequest"), T0)
    n = board.next_notice(T0 + 420)
    assert n.title == "api (main)"
    assert n.body == "permission prompt - waiting 7m"
    assert n.jump_id == "s1"


@pytest.mark.parametrize("event,interval", [
    (ev("Stop"), 900), (ev("PermissionRequest"), 300), (ev("PreToolUse", tool_name="AskUserQuestion"), 300),
])
def test_reminder_interval_depends_on_reason(board, event, interval):
    started(board)
    board.apply(event, T0)
    assert board.next_notice(T0 + 3) is not None
    assert board.next_notice(T0 + 3 + interval - 1) is None
    assert board.next_notice(T0 + 3 + interval) is not None


def test_snooze_pauses_until_deadline(board):
    started(board)
    board.apply(ev("Stop"), T0)
    assert board.next_notice(T0 + 3)
    board.snooze(["s1"], T0 + 3 + 3600)
    assert board.next_notice(T0 + 3 + 900) is None
    assert board.next_notice(T0 + 3 + 3600) is not None


def test_snooze_shorter_than_interval_waits_for_interval(board):
    started(board)
    board.apply(ev("Stop"), T0)
    assert board.next_notice(T0 + 3)
    board.snooze(["s1"], T0 + 60)
    assert board.next_notice(T0 + 61) is None
    assert board.next_notice(T0 + 903) is not None


def test_wake_clears_snooze_and_fires_now(board):
    started(board)
    board.apply(ev("Stop"), T0)
    board.next_notice(T0 + 3)
    board.snooze(["s1"], T0 + 3600)
    board.wake(["s1"])
    assert board.next_notice(T0 + 10) is not None


def test_mute_silences_until_next_spin_then_wobble(board):
    started(board)
    board.apply(ev("Stop"), T0)
    board.mute(["s1"])
    assert board.next_notice(T0 + 5000) is None
    board.apply(ev("UserPromptSubmit"), T0 + 5001)
    assert board.plates["s1"].muted is False
    board.apply(ev("Stop"), T0 + 5002)
    assert board.next_notice(T0 + 5005) is not None


def test_spinning_clears_snooze(board):
    started(board)
    board.apply(ev("Stop"), T0)
    board.snooze(["s1"], T0 + 3600)
    board.apply(ev("UserPromptSubmit"), T0 + 10)
    assert board.plates["s1"].snooze_until is None


def test_global_pause(board):
    started(board)
    board.apply(ev("Stop"), T0)
    board.pause_until = T0 + 600
    assert board.next_notice(T0 + 10) is None
    assert board.next_notice(T0 + 600) is not None


def test_several_due_plates_coalesce_into_one_notice(board):
    started(board, "s1", pid=1)
    started(board, "s2", pid=2, cwd="/x/docs")
    board.apply(ev("Stop", "s1"), T0 + 100)
    board.apply(ev("PermissionRequest", "s2", cwd="/x/docs"), T0 + 50)
    n = board.next_notice(T0 + 200)
    assert n.title == "2 plates wobbling"
    assert n.plate_ids == ["s2", "s1"]
    assert n.jump_id == "s2"
    assert n.body.splitlines() == ["docs (main): permission prompt - 2m", "api (main): done - 1m"]
    # both reminders were reset by the shared notice
    assert board.next_notice(T0 + 201) is None


def test_non_interactive_sessions_are_ignored(board):
    board.apply(ev("SessionStart", interactive=False), T0)
    board.apply(ev("Stop", interactive=False), T0 + 1)
    assert board.next_notice(T0 + 10) is None
    assert board.visible() == []


def test_non_interactive_sessions_notify_when_configured():
    board = Board(Config(ignore_non_interactive=False))
    board.apply(ev("Stop", interactive=False), T0)
    assert board.next_notice(T0 + 10) is not None


def test_dropped_plates_never_notify(board):
    started(board)
    board.apply(ev("Stop"), T0)
    board.apply(ev("SessionEnd"), T0 + 1)
    assert board.next_notice(T0 + 10) is None


def test_visible_sorts_wobbling_first_by_longest_wait(board):
    started(board, "a", pid=1)
    started(board, "b", pid=2)
    started(board, "c", pid=3)
    board.apply(ev("Stop", "b"), T0 + 20)
    board.apply(ev("Stop", "c"), T0 + 10)
    assert [p.session_id for p in board.visible()] == ["c", "b", "a"]


def test_any_open_wobble_reports_close_when_nothing_left(board):
    started(board)
    board.apply(ev("Stop"), T0)
    assert board.next_notice(T0 + 3)
    assert board.has_eligible(T0 + 4)
    board.apply(ev("UserPromptSubmit"), T0 + 5)
    assert not board.has_eligible(T0 + 6)


# --- lookup ------------------------------------------------------------------

def test_find_by_name_or_id_prefix(board):
    started(board, "abc-123")
    assert board.find("api").session_id == "abc-123"
    assert board.find("abc").session_id == "abc-123"
    with pytest.raises(KeyError):
        board.find("zzz")


def test_longest_waiting(board):
    started(board, "a", pid=1)
    started(board, "b", pid=2)
    board.apply(ev("Stop", "a"), T0 + 20)
    board.apply(ev("Stop", "b"), T0 + 10)
    assert board.longest_waiting().session_id == "b"


# --- reconciliation ----------------------------------------------------------

def sess(pid, sid, status, at, **kw):
    d = {"pid": pid, "sessionId": sid, "cwd": "/home/u/dev/api", "kind": "interactive",
         "status": status, "statusUpdatedAt": at * 1000}
    d.update(kw)
    return d


def test_reconcile_drops_dead_pids(board):
    started(board)
    board.reconcile([], alive=lambda pid: False, now=T0 + 10)
    assert board.plates["s1"].state == "dropped"


def test_reconcile_adopts_unknown_sessions(board):
    board.reconcile([sess(5, "x", "idle", T0 - 100), sess(6, "y", "busy", T0 - 5),
                     sess(7, "z", "waiting", T0 - 50, waitingFor="permission")],
                    alive=lambda pid: True, now=T0)
    assert (board.plates["x"].state, board.plates["x"].reason, board.plates["x"].since) == ("wobbling", "done", T0 - 100)
    assert board.plates["y"].state == "spinning"
    assert (board.plates["z"].state, board.plates["z"].reason) == ("wobbling", "permission")


def test_reconcile_skips_dead_and_non_interactive_session_files(board):
    board.reconcile([sess(5, "x", "idle", T0), sess(6, "y", "idle", T0, kind="sdk")],
                    alive=lambda pid: pid != 5, now=T0)
    assert "x" not in board.plates
    assert board.plates["y"].interactive is False


def test_reconcile_newer_status_wins(board):
    started(board)
    board.apply(ev("PermissionRequest"), T0 + 1)
    # approved; long tool run - session file says busy, newer than our event
    board.reconcile([sess(100, "s1", "busy", T0 + 5)], alive=lambda pid: True, now=T0 + 10)
    assert board.plates["s1"].state == "spinning"


def test_reconcile_older_status_loses(board):
    started(board)
    board.apply(ev("PermissionRequest"), T0 + 5)
    board.reconcile([sess(100, "s1", "busy", T0 + 1)], alive=lambda pid: True, now=T0 + 10)
    assert board.plates["s1"].state == "wobbling"


def test_reconcile_uses_session_file_pid_for_plates_without_one(board):
    board.apply(ev("Stop", pid=None), T0)
    board.reconcile([sess(42, "s1", "idle", T0)], alive=lambda pid: True, now=T0 + 1)
    assert board.plates["s1"].pid == 42


# --- persistence -------------------------------------------------------------

def test_roundtrip(board):
    started(board, kitty_window_id="3")
    board.apply(ev("Stop"), T0 + 1)
    board.rename("s1", "x")
    board.snooze(["s1"], T0 + 99)
    board.pause_until = T0 + 7
    copy = Board.from_dict(Config(), board.to_dict())
    assert copy.plates["s1"] == board.plates["s1"]
    assert copy.pause_until == T0 + 7


# --- subagents ---------------------------------------------------------------

def test_subagent_tool_use_does_not_mark_a_waiting_session_busy(board):
    started(board)
    board.apply(ev("Stop"), T0)
    board.apply(ev("PreToolUse", tool_name="Bash", agent_id="a1"), T0 + 5)
    assert board.plates["s1"].state == "wobbling"


def test_subagent_permission_request_still_wobbles(board):
    started(board)
    board.apply(ev("PermissionRequest", tool_name="Bash", agent_id="a1"), T0 + 5)
    assert board.plates["s1"].reason == "permission"


def test_reconcile_idle_status_wobbles_a_spinning_plate(board):
    started(board)
    board.reconcile([sess(100, "s1", "idle", T0 + 5)], alive=lambda pid: True, now=T0 + 10)
    p = board.plates["s1"]
    assert (p.state, p.reason, p.since) == ("wobbling", "done", T0 + 5)


# --- focus -------------------------------------------------------------------

def test_focused_plate_is_not_notified(board):
    started(board)
    board.apply(ev("Stop"), T0)
    assert board.next_notice(T0 + 5, focused={"s1"}) is None
    assert not board.has_eligible(T0 + 5, focused={"s1"})


def test_focus_counts_as_seen_so_leaving_waits_an_interval(board):
    started(board)
    board.apply(ev("Stop"), T0)
    board.next_notice(T0 + 100, focused={"s1"})
    assert board.next_notice(T0 + 101) is None
    assert board.next_notice(T0 + 100 + 900) is not None


def test_focused_plate_is_left_out_of_a_coalesced_notice(board):
    started(board, "a", pid=1)
    started(board, "b", pid=2)
    board.apply(ev("Stop", "a"), T0)
    board.apply(ev("Stop", "b"), T0)
    n = board.next_notice(T0 + 5, focused={"a"})
    assert n.plate_ids == ["b"]
