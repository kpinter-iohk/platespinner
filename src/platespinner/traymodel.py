"""What the tray shows, as plain data. tray.py renders it with Gtk."""

from dataclasses import dataclass, field

from .config import format_duration


@dataclass
class Action:
    label: str
    cmd: dict


@dataclass
class Row:
    session_id: str
    text: str
    actions: list = field(default_factory=list)


@dataclass
class TrayModel:
    icon: str
    label: str
    rows: list
    footer: list


def _row(p, presets):
    sid = p["session_id"]
    jump = Action("Jump", {"cmd": "jump", "target": sid})
    wake = Action("Wake", {"cmd": "wake", "target": sid})
    head = f"{p['name']}  {p['branch']}" if p.get("branch") else p["name"]
    if p["state"] != "wobbling":
        return Row(sid, f"-  {head}  spinning {format_duration(p['wait'])}", [jump])
    what = f"{p['reason']} {format_duration(p['wait'])}"
    if p["muted"]:
        return Row(sid, f"m  {head}  muted, {what}", [jump, wake])
    if p["snooze_left"]:
        return Row(sid, f"z  {head}  snoozed, {format_duration(p['snooze_left'])} left", [jump, wake])
    snoozes = [Action(f"Snooze {format_duration(s)}", {"cmd": "snooze", "target": sid, "duration": s})
               for s in presets]
    return Row(sid, f"!  {head}  {what}", [jump, *snoozes, Action("Mute", {"cmd": "mute", "target": sid})])


def build(listing, presets, snooze_default):
    plates = listing["plates"]
    rows = [_row(p, presets) for p in plates]
    active = sum(1 for r in rows if r.text.startswith("!"))
    paused = listing.get("paused_left") or 0
    pause = (Action(f"Resume (paused, {format_duration(paused)} left)", {"cmd": "pause", "duration": 0})
             if paused else Action("Pause 1h", {"cmd": "pause", "duration": 3600}))
    footer = [Action(f"Snooze all {format_duration(snooze_default)}",
                     {"cmd": "snooze", "all": True, "duration": snooze_default}),
              pause, Action("Quit", {"cmd": "quit"})]
    icon = "platespinner-wobble" if active else "platespinner-idle"
    return TrayModel(icon, str(active) if active else "", rows, footer)
