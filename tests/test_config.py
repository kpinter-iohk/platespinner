import pytest

from platespinner.config import Config, load_config, parse_duration, format_duration


@pytest.mark.parametrize("text,seconds", [
    ("3s", 3), ("5m", 300), ("1h", 3600), ("1h30m", 5400), ("90", 90), ("1.5m", 90),
])
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "5x", "m"])
def test_parse_duration_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_duration(text)


@pytest.mark.parametrize("seconds,text", [
    (0, "0s"), (40, "40s"), (60, "1m"), (420, "7m"), (3600, "1h"), (3900, "1h 5m"),
])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


def test_missing_config_file_gives_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg == Config()
    assert cfg.blocking_interval == 300
    assert cfg.done_interval == 900
    assert cfg.debounce == 3
    assert cfg.snooze_default == 900
    assert cfg.snooze_presets == [300, 900, 3600]
    assert cfg.ignore_non_interactive is True
    assert cfg.reconcile_interval == 10
    assert cfg.drop_grace == 30


def test_config_file_overrides_some_keys(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[reminders]\nblocking_interval = "2m"\n[snooze]\npresets = ["1m"]\n')
    cfg = load_config(p)
    assert cfg.blocking_interval == 120
    assert cfg.snooze_presets == [60]
    assert cfg.done_interval == 900


def test_bad_config_file_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[reminders]\nblocking_interval = "soon"\n')
    with pytest.raises(ValueError):
        load_config(p)
