import pytest

from platespinner.cli import format_list, parse


@pytest.mark.parametrize("argv,msg", [
    (["list"], {"cmd": "list"}),
    (["snooze", "api", "15m"], {"cmd": "snooze", "target": "api", "duration": "15m"}),
    (["snooze", "api"], {"cmd": "snooze", "target": "api", "duration": None}),
    (["snooze", "--all", "30m"], {"cmd": "snooze", "all": True, "duration": "30m"}),
    (["wake", "api"], {"cmd": "wake", "target": "api"}),
    (["mute", "api"], {"cmd": "mute", "target": "api"}),
    (["name", "abc", "my label"], {"cmd": "name", "target": "abc", "name": "my label"}),
    (["jump"], {"cmd": "jump", "target": None}),
    (["jump", "api"], {"cmd": "jump", "target": "api"}),
    (["pause", "1h"], {"cmd": "pause", "duration": "1h"}),
    (["resume"], {"cmd": "pause", "duration": 0}),
])
def test_parse_daemon_commands(argv, msg):
    kind, got = parse(argv)
    assert kind == "daemon"
    assert {"type": "cmd", **msg} == got


def test_snooze_all_with_target_is_an_error():
    with pytest.raises(SystemExit):
        parse(["snooze", "--all", "api", "5m"])


@pytest.mark.parametrize("argv", [["install-hooks"], ["uninstall-hooks"], ["install-kitten"],
                                  ["install-service"], ["doctor"]])
def test_parse_local_commands(argv):
    assert parse(argv) == ("local", argv[0])


def test_format_list():
    listing = {"paused_left": 0, "plates": [
        {"session_id": "abcdef12-3", "name": "api", "branch": "main", "state": "wobbling",
         "reason": "permission", "wait": 420, "muted": False, "snooze_left": 0, "term": "tmux"},
        {"session_id": "99999999-1", "name": "docs", "branch": None, "state": "spinning",
         "reason": None, "wait": 40, "muted": False, "snooze_left": 0, "term": "-"},
    ]}
    lines = format_list(listing).splitlines()
    assert lines[0].split() == ["ID", "NAME", "BRANCH", "STATE", "TERM"]
    assert lines[1].split() == ["abcdef12", "api", "main", "permission", "7m", "tmux"]
    assert lines[2].split() == ["99999999", "docs", "-", "spinning", "40s", "-"]


def test_format_empty_and_paused():
    assert format_list({"plates": [], "paused_left": 0}) == "no sessions"
    assert "paused, 30m left" in format_list({"plates": [], "paused_left": 1800})
