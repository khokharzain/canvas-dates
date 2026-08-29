"""The menu bar app itself."""

from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import threading
import webbrowser

import rumps

from . import config, store
from .canvas import AuthError, CanvasError
from .fetcher import NeedsSetup, collect
from .items import (DueItem, bar_title, menu_label, parse_when,
                    remaining_by_unit, weight_label)

# rumps keys menu entries by their title, so every entry needs a unique one.
# Trailing spaces are invisible in a macOS menu and, unlike a zero-width
# format character, can't render as a stray box in some fonts.
def _pad(index: int) -> str:
    return " " * (index + 1)

LOOK_AHEAD_CHOICES = [14, 30, 60, 90, 180]


def ago(stamp: dt.datetime, now: dt.datetime) -> str:
    seconds = (now - stamp).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return "%d min ago" % round(seconds / 60)
    if seconds < 86400:
        return "%d hr ago" % round(seconds / 3600)
    return "%d days ago" % round(seconds / 86400)


class CanvasDates(rumps.App):
    def __init__(self):
        super().__init__("Canvas Dates", title="⏳ Canvas", quit_button=None)
        self.cfg = config.load()
        self.items: list[DueItem] = []
        self.courses: list[dict] = []
        self.fetched_at: dt.datetime | None = None
        self.error: str | None = None
        self.needs_setup = False
        self._rows: dict[str, DueItem] = {}
        self._pending = None
        self._lock = threading.Lock()
        self._loading = False

        self._restore_cache()
        self._rebuild()

        # Poll for background fetch results, and re-render countdowns.
        self._poller = rumps.Timer(self._collect, 1)
        self._poller.start()
        self._ticker = rumps.Timer(self._on_minute, 60)
        self._ticker.start()

        self._start_fetch()

    # -- state ------------------------------------------------------------

    def _restore_cache(self):
        cached = config.load_cache()
        if not cached:
            return
        try:
            self.items = [DueItem.from_dict(d) for d in cached.get("items", [])]
            self.courses = cached.get("courses", [])
            self.fetched_at = dt.datetime.fromisoformat(cached["fetched_at"])
        except (KeyError, TypeError, ValueError):
            self.items, self.courses, self.fetched_at = [], [], None

    def _prune(self):
        """Drop items that have aged past the overdue grace window."""
        cutoff = (dt.datetime.now().astimezone()
                  - dt.timedelta(days=int(self.cfg["overdue_grace_days"])))
        self.items = [i for i in self.items if i.due >= cutoff]

    # -- fetching ---------------------------------------------------------

    def _start_fetch(self):
        if self._loading:
            return
        self._loading = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            result = ("ok", collect(self.cfg))
        except NeedsSetup as exc:
            result = ("noauth", str(exc))
        except AuthError as exc:
            result = ("noauth", str(exc))
        except CanvasError as exc:
            result = ("error", str(exc))
        except Exception as exc:                      # never kill the app
            result = ("error", "Unexpected error: %s" % exc)
        with self._lock:
            self._pending = result

    def _collect(self, _timer):
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is None:
            return
        status, payload = pending
        self._loading = False

        if status == "ok":
            self.items = [DueItem.from_dict(d) for d in payload["items"]]
            self.courses = payload.get("courses", [])
            self.fetched_at = dt.datetime.fromisoformat(payload["fetched_at"])
            self.error = None
            self.needs_setup = False
            try:
                config.save_cache(payload)
            except OSError:
                pass
        elif status == "noauth":
            self.needs_setup = True
            self.error = payload
        else:
            self.error = payload
        self._rebuild()

    def _on_minute(self, _timer):
        self._prune()
        self._rebuild()
        if self.fetched_at is None:
            self._start_fetch()
            return
        due = dt.timedelta(minutes=int(self.cfg["refresh_minutes"]))
        if dt.datetime.now().astimezone() - self.fetched_at >= due:
            self._start_fetch()

    # -- menu -------------------------------------------------------------

    def _rebuild(self):
        now = dt.datetime.now().astimezone()
        self.title = bar_title(self.items, now,
                               self.cfg.get("show_weight_in_bar", True),
                               self.cfg.get("bar_details", False))
        if self.needs_setup:
            self.title = "⚙️"

        self.menu.clear()
        self._rows.clear()

        if self.needs_setup:
            self.menu.add(rumps.MenuItem(
                "Connect your Canvas calendar…", callback=self.set_feed))
            self.menu.add(rumps.separator)
        elif self.error:
            self.menu.add(rumps.MenuItem("⚠️  %s" % self.error[:70]))
            self.menu.add(rumps.separator)

        if not self.items and not self.needs_setup:
            self.menu.add(rumps.MenuItem(
                "Nothing due in the next %s days" % self.cfg["days_ahead"]))
        for index, item in enumerate(self.items[:int(self.cfg["max_items"])]):
            pad = _pad(index)
            row = rumps.MenuItem(menu_label(item, now) + pad)

            submit = "Mark as submitted" + pad
            self._rows[submit] = item
            row.add(rumps.MenuItem(submit, callback=self.mark_submitted))

            if item.url:
                opener = "Open in Canvas" + pad
                self._rows[opener] = item
                row.add(rumps.MenuItem(opener, callback=self.open_item))
            self.menu.add(row)

        if len(self.items) > int(self.cfg["max_items"]):
            self.menu.add(rumps.MenuItem(
                "…and %d more" % (len(self.items) - int(self.cfg["max_items"]))))

        rows = remaining_by_unit(self.items)
        if rows:
            self.menu.add(rumps.separator)
            summary = rumps.MenuItem("Still to hand in, by unit")
            for code, pct, count in rows:
                summary.add(rumps.MenuItem(
                    "%s — %s of the final grade across %d item%s"
                    % (code, weight_label(pct), count, "" if count == 1 else "s")))
            for note in self.courses:
                total = note.get("weight_total") or 0
                if note.get("weighted") and total and abs(total - 100) > 2:
                    summary.add(rumps.MenuItem(
                        "⚠️ %s weights add to %s in Canvas"
                        % (note["code"], weight_label(total))))
            self.menu.add(summary)

        missing = [c["code"] for c in self.courses
                   if not c.get("entered", True)]
        if missing:
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem(
                "No %% entered yet for %s" % ", ".join(missing[:3]),
                callback=self.edit_weights))

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Add a due date…", callback=self.add_due,
                                     key="n"))
        self.menu.add(rumps.MenuItem("Refresh now", callback=self.refresh,
                                     key="r"))
        if self.fetched_at:
            self.menu.add(rumps.MenuItem("Updated %s" % ago(self.fetched_at, now)))
        self.menu.add(rumps.separator)
        self.menu.add(self._settings_menu())
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application,
                                     key="q"))

    def _settings_menu(self) -> rumps.MenuItem:
        settings = rumps.MenuItem("Settings")
        settings.add(rumps.MenuItem("Open Canvas in browser",
                                    callback=self.open_canvas))

        hide = rumps.MenuItem("Hide work I've submitted",
                              callback=self.toggle_hide_submitted)
        hide.state = 1 if self.cfg.get("hide_submitted") else 0
        settings.add(hide)

        details = rumps.MenuItem("Show what's next in the menu bar",
                                 callback=self.toggle_bar_details)
        details.state = 1 if self.cfg.get("bar_details", False) else 0
        settings.add(details)

        if self.cfg.get("bar_details", False):
            pct = rumps.MenuItem("…and its %",
                                 callback=self.toggle_weight_in_bar)
            pct.state = 1 if self.cfg.get("show_weight_in_bar", True) else 0
            settings.add(pct)

        exams = rumps.MenuItem("Include every calendar event",
                               callback=self.toggle_all_events)
        exams.state = 1 if self.cfg.get("all_calendar_events") else 0
        settings.add(exams)

        ahead = rumps.MenuItem("Look ahead")
        for days in LOOK_AHEAD_CHOICES:
            entry = rumps.MenuItem("%d days" % days, callback=self.set_look_ahead)
            entry.state = 1 if int(self.cfg["days_ahead"]) == days else 0
            ahead.add(entry)
        settings.add(ahead)

        settings.add(rumps.separator)
        settings.add(rumps.MenuItem("Edit assessment weights…",
                                    callback=self.edit_weights))
        settings.add(rumps.MenuItem("Un-hide everything marked submitted",
                                    callback=self.restore_dismissed))
        settings.add(rumps.separator)
        settings.add(rumps.MenuItem("Replace calendar feed link…",
                                    callback=self.set_feed))
        settings.add(rumps.MenuItem("Reveal settings folder",
                                    callback=self.reveal_config))
        return settings

    # -- actions ----------------------------------------------------------

    def _row_for(self, sender) -> DueItem | None:
        return self._rows.get(getattr(sender, "title", ""))

    def open_item(self, sender):
        item = self._row_for(sender)
        if item and item.url:
            webbrowser.open(item.url)

    def add_due(self, _sender):
        window = rumps.Window(
            message=("Add a due date.\n\n"
                     "One line, separated by |\n"
                     "    unit | what it is | when | % of grade\n\n"
                     "The % is optional. Dates like '15 Oct', '15 Oct 5pm'\n"
                     "or '2026-10-15' all work; no time means 11:59pm."),
            title="Canvas Dates",
            default_text="CAB444 | Assignment 2 | 15 Oct | 50",
            ok="Add", cancel="Cancel",
            dimensions=(420, 24),
        )
        response = window.run()
        if not response.clicked:
            return
        problem = self._save_manual(response.text)
        if problem:
            rumps.alert("Couldn't add that", problem)
            return
        self._start_fetch()

    def _save_manual(self, text: str) -> str | None:
        """Parse one 'unit | title | when | %' line. Returns an error, or None."""
        parts = [p.strip() for p in (text or "").split("|")]
        if len(parts) < 3:
            return ("Needs at least three parts separated by | — "
                    "unit, what it is, and when.")
        course, title, when_text = parts[0], parts[1], parts[2]
        if not course or not title:
            return "Both the unit and a name are needed."

        due = parse_when(when_text)
        if due is None:
            return ("Couldn't read '%s' as a date. Try '15 Oct', "
                    "'15 Oct 5pm' or '2026-10-15'." % when_text)

        weight = None
        if len(parts) > 3 and parts[3]:
            try:
                weight = float(parts[3].rstrip("%").strip())
            except ValueError:
                return "'%s' isn't a percentage." % parts[3]
            if not 0 <= weight <= 100:
                return "A percentage has to be between 0 and 100."

        store.add_manual(course.upper(), title, due.isoformat(), weight)
        return None

    def open_canvas(self, _sender):
        webbrowser.open(self.cfg["base_url"])

    def refresh(self, _sender):
        self._start_fetch()

    def _save_and_refetch(self):
        config.save(self.cfg)
        self.fetched_at = None
        self._rebuild()
        self._start_fetch()

    def toggle_hide_submitted(self, _sender):
        self.cfg["hide_submitted"] = not self.cfg.get("hide_submitted")
        self._save_and_refetch()

    def toggle_all_events(self, _sender):
        self.cfg["all_calendar_events"] = not self.cfg.get("all_calendar_events")
        self._save_and_refetch()

    def toggle_bar_details(self, _sender):
        self.cfg["bar_details"] = not self.cfg.get("bar_details", False)
        config.save(self.cfg)
        self._rebuild()

    def toggle_weight_in_bar(self, _sender):
        self.cfg["show_weight_in_bar"] = not self.cfg.get("show_weight_in_bar", True)
        config.save(self.cfg)
        self._rebuild()

    def set_look_ahead(self, sender):
        self.cfg["days_ahead"] = int(sender.title.split()[0])
        self._save_and_refetch()

    def mark_submitted(self, sender):
        item = self._row_for(sender)
        if item is None:
            return
        if item.manual:
            # You added it, so drop it rather than hiding it forever.
            store.remove_manual(item.course, item.title, item.due_iso)
        else:
            data = store.load_dismissed()
            store.dismiss(data, item.course, item.title, item.due_iso)
            store.save_dismissed(data)
        self.items = [i for i in self.items if i is not item]
        self._rebuild()

    def restore_dismissed(self, _sender):
        store.save_dismissed({})
        self._start_fetch()

    def edit_weights(self, _sender):
        """Weight entry is a conversation, so hand it to Terminal."""
        helper = (pathlib.Path(__file__).resolve().parent.parent
                  / "set-weights.command")
        subprocess.run(["open", str(helper)], check=False)

    def set_feed(self, _sender):
        window = rumps.Window(
            message=("Paste your Canvas calendar feed link.\n\n"
                     "In Canvas: Calendar → 'Calendar Feed' at the bottom of\n"
                     "the right-hand column. Stored in your macOS Keychain."),
            title="Canvas Dates",
            default_text="",
            ok="Save", cancel="Cancel",
            dimensions=(420, 24),
        )
        response = window.run()
        if not response.clicked or not response.text.strip():
            return
        try:
            config.set_feed(response.text)
        except ValueError:
            return
        self.needs_setup = False
        self.error = None
        self._start_fetch()

    def reveal_config(self, _sender):
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(config.CONFIG_DIR)], check=False)


def main():
    CanvasDates().run()


if __name__ == "__main__":
    main()
