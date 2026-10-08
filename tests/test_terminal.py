import json

from platespinner.model import Plate
from platespinner.terminal import Terminals

KITTY_LS = json.dumps([
    {"id": 1, "is_focused": True, "tabs": [
        {"is_active": True, "windows": [{"id": 4, "is_focused": True}, {"id": 5, "is_focused": False}]},
        {"is_active": False, "windows": [{"id": 6, "is_focused": False}]}]},
])
KITTY_UNFOCUSED = json.dumps([
    {"id": 1, "is_focused": False, "tabs": [{"is_active": True, "windows": [{"id": 4, "is_focused": False}]}]},
])


class FakeRun:
    def __init__(self, responses):
        self.responses = responses   # tuple(argv) prefix match -> (code, stdout)
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        for prefix, reply in self.responses.items():
            if tuple(argv[:len(prefix)]) == prefix:
                return reply
        return (1, "")


def kitty_plate(wid="4", sid="s1"):
    return Plate(session_id=sid, kitty_window_id=wid, kitty_listen_on="unix:/tmp/kitty-9")


def tmux_plate(sid="t1"):
    # kitty ids from the tmux server environment are stale and must be ignored
    return Plate(session_id=sid, kitty_window_id="99", kitty_listen_on="unix:/tmp/kitty-old",
                 tmux_socket="/tmp/tmux-1000/default", tmux_pane="%3")


TMUX = ("tmux", "-S", "/tmp/tmux-1000/default")


def tmux_responses(window_active="1", pane_active="1", client_session="work"):
    return {
        TMUX + ("display-message",): (0, f"work\t{window_active}\t{pane_active}\n"),
        TMUX + ("list-clients",): (0, f"700\t/dev/pts/3\t{client_session}\t100\n"
                                      f"701\t/dev/pts/4\tother\t50\n"),
    }


ENV = {700: {"KITTY_WINDOW_ID": "4", "KITTY_LISTEN_ON": "unix:/tmp/kitty-9"},
       701: {"KITTY_WINDOW_ID": "8", "KITTY_LISTEN_ON": "unix:/tmp/kitty-9"}}


def terms(responses):
    run = FakeRun(responses)
    return Terminals(run=run, environ=lambda pid: ENV.get(pid, {})), run


# --- focus -------------------------------------------------------------------

def test_kitty_plate_in_focused_window_is_focused():
    t, _ = terms({("kitty", "@", "--to", "unix:/tmp/kitty-9", "ls"): (0, KITTY_LS)})
    assert t.focused([kitty_plate("4", "a"), kitty_plate("5", "b")]) == {"a"}


def test_nothing_focused_when_kitty_lacks_os_focus():
    t, _ = terms({("kitty", "@", "--to", "unix:/tmp/kitty-9", "ls"): (0, KITTY_UNFOCUSED)})
    assert t.focused([kitty_plate("4")]) == set()


def test_kitty_ls_runs_once_per_instance():
    t, run = terms({("kitty", "@", "--to", "unix:/tmp/kitty-9", "ls"): (0, KITTY_LS)})
    t.focused([kitty_plate("4", "a"), kitty_plate("5", "b")])
    assert len(run.calls) == 1


def test_unreachable_kitty_means_not_focused():
    t, _ = terms({})
    assert t.focused([kitty_plate()]) == set()


def test_plate_outside_kitty_and_tmux_is_never_focused():
    t, run = terms({})
    assert t.focused([Plate(session_id="p")]) == set()
    assert run.calls == []


def test_tmux_plate_focused_when_its_pane_is_visible_in_focused_kitty():
    t, _ = terms({**tmux_responses(), ("kitty", "@", "--to", "unix:/tmp/kitty-9", "ls"): (0, KITTY_LS)})
    assert t.focused([tmux_plate()]) == {"t1"}


def test_tmux_plate_not_focused_when_another_window_is_shown():
    t, _ = terms({**tmux_responses(window_active="0"),
                  ("kitty", "@", "--to", "unix:/tmp/kitty-9", "ls"): (0, KITTY_LS)})
    assert t.focused([tmux_plate()]) == set()


def test_tmux_plate_not_focused_when_client_shows_another_session():
    t, _ = terms({**tmux_responses(client_session="other"),
                  ("kitty", "@", "--to", "unix:/tmp/kitty-9", "ls"): (0, KITTY_LS)})
    assert t.focused([tmux_plate()]) == set()


# --- locate ------------------------------------------------------------------

def test_tmux_locate_uses_the_attached_clients_kitty_window():
    t, _ = terms(tmux_responses())
    loc = t.locate(tmux_plate())
    assert (loc.kitty_listen_on, loc.kitty_window_id) == ("unix:/tmp/kitty-9", "4")
    assert loc.tmux.client_tty == "/dev/pts/3"


def test_tmux_locate_falls_back_to_most_recent_client_when_session_is_detached():
    responses = tmux_responses()
    responses[TMUX + ("list-clients",)] = (0, "700\t/dev/pts/3\ta\t10\n701\t/dev/pts/4\tb\t50\n")
    t, _ = terms(responses)
    loc = t.locate(tmux_plate())
    assert loc.tmux.client_tty == "/dev/pts/4"
    assert loc.kitty_window_id == "8"


def test_tmux_without_clients_has_no_kitty_window():
    responses = tmux_responses()
    responses[TMUX + ("list-clients",)] = (0, "")
    t, _ = terms(responses)
    loc = t.locate(tmux_plate())
    assert loc.kitty_window_id is None and loc.tmux.client_tty is None


# --- jump --------------------------------------------------------------------

def test_kitty_jump_runs_kitten_with_token(tmp_path):
    kitten = tmp_path / "k.py"
    kitten.write_text("")
    t, run = terms({("kitty",): (0, "focused 4 token=yes")})
    assert t.jump(kitty_plate(), "tok", str(kitten))
    assert run.calls == [["kitty", "@", "--to", "unix:/tmp/kitty-9", "kitten", "--match", "id:4", str(kitten), "tok"]]


def test_kitty_jump_falls_back_to_focus_window(tmp_path):
    kitten = tmp_path / "k.py"
    kitten.write_text("")
    t, run = terms({("kitty", "@", "--to", "unix:/tmp/kitty-9", "focus-window"): (0, "")})
    assert t.jump(kitty_plate(), "tok", str(kitten))
    assert run.calls[-1][4] == "focus-window"


def test_tmux_jump_switches_client_then_focuses_kitty(tmp_path):
    kitten = tmp_path / "k.py"
    kitten.write_text("")
    t, run = terms({**tmux_responses(), TMUX + ("switch-client",): (0, ""), TMUX + ("select-window",): (0, ""),
                    TMUX + ("select-pane",): (0, ""), ("kitty",): (0, "focused 4 token=yes")})
    assert t.jump(tmux_plate(), "tok", str(kitten))
    cmds = [c[3] if c[0] == "tmux" else c[4] for c in run.calls]
    assert cmds == ["display-message", "list-clients", "switch-client", "select-window", "select-pane", "kitten"]
    assert run.calls[2] == list(TMUX) + ["switch-client", "-c", "/dev/pts/3", "-t", "%3"]
    assert run.calls[-1][6] == "id:4"


def test_jump_outside_kitty_and_tmux_fails():
    t, run = terms({})
    assert not t.jump(Plate(session_id="p"), "tok", None)
    assert run.calls == []
