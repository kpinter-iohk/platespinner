"""platespinner command line."""

import argparse
import sys

from .config import format_duration


def build_parser():
    p = argparse.ArgumentParser(prog="platespinner", description="Keep every Claude Code session spinning.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show all sessions")
    s = sub.add_parser("snooze", help="pause reminders for a session, or all with --all")
    s.add_argument("--all", action="store_true")
    s.add_argument("args", nargs="*", metavar="[ID|NAME] [DURATION]")
    for name, helptext in (("wake", "end a snooze or mute"), ("mute", "silence until the next wobble")):
        sub.add_parser(name, help=helptext).add_argument("target", metavar="ID|NAME")
    n = sub.add_parser("name", help="label a session")
    n.add_argument("target", metavar="ID|NAME")
    n.add_argument("name")
    j = sub.add_parser("jump", help="focus a session's kitty tab (default: longest waiting)")
    j.add_argument("target", nargs="?", metavar="ID|NAME")
    sub.add_parser("pause", help="pause all notifications").add_argument("duration")
    sub.add_parser("resume", help="end a pause")
    sub.add_parser("install-hooks", help="add platespinner hooks to ~/.claude/settings.json")
    sub.add_parser("uninstall-hooks", help="remove them again")
    sub.add_parser("install-kitten", help="copy focus_with_token.py to ~/.config/kitty/")
    sub.add_parser("install-service", help="install and start the systemd user service")
    sub.add_parser("doctor", help="check the whole setup")
    return p


LOCAL = ("install-hooks", "uninstall-hooks", "install-kitten", "install-service", "doctor")


def parse(argv):
    """-> ("local", command) or ("daemon", message)."""
    parser = build_parser()
    a = parser.parse_args(argv)
    if a.command in LOCAL:
        return "local", a.command
    msg = {"type": "cmd", "cmd": a.command}
    if a.command == "snooze":
        if a.all:
            if len(a.args) > 1:
                parser.error("snooze --all takes only a duration")
            msg.update(all=True, duration=a.args[0] if a.args else None)
        else:
            if not a.args or len(a.args) > 2:
                parser.error("usage: snooze ID|NAME [DURATION] | snooze --all [DURATION]")
            msg.update(target=a.args[0], duration=a.args[1] if len(a.args) > 1 else None)
    elif a.command in ("wake", "mute", "jump"):
        msg["target"] = a.target
    elif a.command == "name":
        msg.update(target=a.target, name=a.name)
    elif a.command == "pause":
        msg["duration"] = a.duration
    elif a.command == "resume":
        msg.update(cmd="pause", duration=0)
    return "daemon", msg


def _status(p):
    if p["state"] != "wobbling":
        return f"spinning {format_duration(p['wait'])}"
    if p["muted"]:
        return f"muted ({p['reason']} {format_duration(p['wait'])})"
    if p["snooze_left"]:
        return f"snoozed {format_duration(p['snooze_left'])} ({p['reason']} {format_duration(p['wait'])})"
    return f"{p['reason']} {format_duration(p['wait'])}"


def format_list(listing):
    out = []
    if listing.get("paused_left"):
        out.append(f"notifications paused, {format_duration(listing['paused_left'])} left")
    plates = listing["plates"]
    if not plates:
        out.append("no sessions")
        return "\n".join(out)
    rows = [("ID", "NAME", "BRANCH", "STATE", "TERM")]
    rows += [(p["session_id"][:8], p["name"], p["branch"] or "-", _status(p), p["term"])
             for p in plates]
    widths = [max(len(r[i]) for r in rows) for i in range(5)]
    out += ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]
    return "\n".join(out)


def run_local(command):
    from . import install
    if command == "install-hooks":
        backup = install.install_hooks()
        print(f"hooks installed in {install.settings_path()}" + (f" (backup: {backup})" if backup else ""))
    elif command == "uninstall-hooks":
        backup = install.uninstall_hooks()
        print(f"hooks removed from {install.settings_path()}" + (f" (backup: {backup})" if backup else ""))
    elif command == "install-kitten":
        print(f"kitten installed at {install.install_kitten()}")
    elif command == "install-service":
        print(f"service installed at {install.install_service()} and started")
    elif command == "doctor":
        from .doctor import run
        return run()
    return 0


def main(argv=None):
    kind, what = parse(sys.argv[1:] if argv is None else argv)
    if kind == "local":
        sys.exit(run_local(what))
    from .protocol import send
    try:
        reply = send(what, timeout=10, want_reply=True)
    except OSError:
        sys.exit("platespinnerd is not running. Start it with: systemctl --user start platespinnerd")
    if not reply.get("ok"):
        sys.exit(f"platespinner: {reply.get('error')}")
    if what["cmd"] == "list":
        print(format_list(reply))
    elif what["cmd"] == "jump":
        print(f"jumped to {reply['name']}")
