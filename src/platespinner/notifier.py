"""org.freedesktop.Notifications over one long-lived Gio connection.

replaces_id only works from the connection that created the notification, so
this object must live as long as the daemon (report, "Notification behaviour").
"""

from gi.repository import Gio, GLib

BUS = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"


class Notifier:
    def __init__(self, icon, on_token, on_action, on_closed):
        self.icon = icon
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        handlers = {
            "ActivationToken": lambda a: on_token(a[0], a[1]),
            "ActionInvoked": lambda a: on_action(a[0], a[1]),
            "NotificationClosed": lambda a: on_closed(a[0], a[1]),
        }

        def on_signal(conn, sender, path, iface, name, params, *_):
            if name in handlers:
                handlers[name](params.unpack())

        # Every click signal arrives twice; Core de-duplicates (report, gotcha 1).
        self.bus.signal_subscribe(None, BUS, None, PATH, None, Gio.DBusSignalFlags.NONE, on_signal)

    def post(self, replaces_id, title, body, snooze_label):
        actions = ["default", "Jump", "jump", "Jump", "snooze", snooze_label, "mute", "Mute"]
        hints = {"urgency": GLib.Variant("y", 2)}
        args = GLib.Variant("(susssasa{sv}i)",
                            ("platespinner", replaces_id, self.icon, title, body, actions, hints, -1))
        return self.bus.call_sync(BUS, PATH, BUS, "Notify", args, GLib.VariantType("(u)"),
                                  Gio.DBusCallFlags.NONE, -1, None).unpack()[0]

    def close(self, nid):
        try:
            self.bus.call_sync(BUS, PATH, BUS, "CloseNotification", GLib.Variant("(u)", (nid,)),
                               None, Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error:
            pass  # already gone
