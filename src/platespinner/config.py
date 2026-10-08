"""Durations and ~/.config/platespinner/config.toml."""

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_PART = re.compile(r"(\d+(?:\.\d+)?)([smhd])")


def parse_duration(text) -> float:
    """'90', '3s', '5m', '1h30m' -> seconds."""
    if isinstance(text, (int, float)):
        return text
    s = str(text).strip().replace(" ", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s) if "." in s else int(s)
    if not s or _PART.sub("", s):
        raise ValueError(f"bad duration: {text!r}")
    total = sum(float(n) * _UNITS[u] for n, u in _PART.findall(s))
    return int(total) if total == int(total) else total


def format_duration(seconds) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "platespinner" / "config.toml"


@dataclass
class Config:
    blocking_interval: float = 300
    done_interval: float = 900
    debounce: float = 3
    snooze_default: float = 900
    snooze_presets: list = field(default_factory=lambda: [300, 900, 3600])
    ignore_non_interactive: bool = True
    reconcile_interval: float = 10
    drop_grace: float = 30


# (toml table, key) -> Config attribute, and whether it is a duration
_KEYS = {
    ("reminders", "blocking_interval"): ("blocking_interval", True),
    ("reminders", "done_interval"): ("done_interval", True),
    ("reminders", "debounce"): ("debounce", True),
    ("snooze", "default"): ("snooze_default", True),
    ("snooze", "presets"): ("snooze_presets", True),
    ("detection", "ignore_non_interactive"): ("ignore_non_interactive", False),
    ("detection", "reconcile_interval"): ("reconcile_interval", True),
    ("detection", "drop_grace"): ("drop_grace", True),
}


def load_config(path=None) -> Config:
    path = Path(path) if path else config_path()
    cfg = Config()
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return cfg
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path}: {e}") from e
    for (table, key), (attr, is_duration) in _KEYS.items():
        if key not in data.get(table, {}):
            continue
        value = data[table][key]
        if is_duration:
            value = [parse_duration(v) for v in value] if isinstance(value, list) else parse_duration(value)
        setattr(cfg, attr, value)
    return cfg
