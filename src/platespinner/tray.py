"""AppIndicator tray menu, rendered from traymodel."""

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import Gtk  # noqa: E402

from .traymodel import build  # noqa: E402


class Tray:
    def __init__(self, icon_dir, run_command):
        self.run_command = run_command
        self.ind = AppIndicator.Indicator.new(
            "platespinner", "platespinner-idle", AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_icon_theme_path(str(icon_dir))
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.ind.set_title("platespinner")
        self.shape = None
        self.look = None
        self.items = []
        self.footer_items = []
        self.menu = Gtk.Menu()
        self.ind.set_menu(self.menu)

    def refresh(self, listing, presets, snooze_default):
        m = build(listing, presets, snooze_default)
        if (m.icon, m.label) != self.look:
            self.look = (m.icon, m.label)
            self.ind.set_icon_full(m.icon, "plates wobbling" if m.label else "all plates spinning")
            self.ind.set_label(m.label, "99")
        # Rebuilding while the menu is open closes it, so only rebuild when the
        # structure changed; otherwise just relabel.
        shape = ([(r.session_id, [a.label for a in r.actions]) for r in m.rows],
                 [a.cmd for a in m.footer])
        if shape != self.shape:
            self._rebuild(m)
            self.shape = shape
        else:
            for item, text in zip(self.items + self.footer_items,
                                  [r.text for r in m.rows] + [a.label for a in m.footer]):
                if item.get_label() != text:
                    item.set_label(text)

    def _item(self, action):
        item = Gtk.MenuItem(label=action.label)
        item.connect("activate", lambda _w, cmd=action.cmd: self.run_command(cmd))
        return item

    def _rebuild(self, m):
        for child in self.menu.get_children():
            self.menu.remove(child)
        self.items = []
        if not m.rows:
            idle = Gtk.MenuItem(label="No Claude Code sessions")
            idle.set_sensitive(False)
            self.menu.append(idle)
        for row in m.rows:
            item = Gtk.MenuItem(label=row.text)
            sub = Gtk.Menu()
            for action in row.actions:
                sub.append(self._item(action))
            item.set_submenu(sub)
            self.menu.append(item)
            self.items.append(item)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.footer_items = [self._item(a) for a in m.footer]
        for item in self.footer_items:
            self.menu.append(item)
        self.menu.show_all()
