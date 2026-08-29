#!/usr/bin/env python3
"""Offline self-test: runs the whole pipeline against a FAKE Canvas.

Nothing here is read from your real Canvas — no network call, no feed, no
token. The unit codes are yours so the output looks familiar, but the marks,
weightings and dates are invented purely to check the logic. Your real
numbers only appear once the menu bar app runs.

Covers both sources: the calendar feed (what you actually use) and the API
token path (kept in case QUT ever issues one).

    python3 selftest.py
"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

NOW = dt.datetime.now().astimezone()


def at(**kw):
    return (NOW + dt.timedelta(**kw)).astimezone(dt.timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")



# ── Part 1: the calendar feed, the source Zain actually uses ──────────────

FEED = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Instructure//Canvas//EN\r
BEGIN:VEVENT\r
DTSTART:{d6}\r
UID:event-assignment-9911@instructure.com\r
SUMMARY:CAB444 Assignment 1: Design and implementation of secure network\r
  architecture and services - Implementation [CAB444_26se2]\r
DESCRIPTION:Submit configs\\, scripts\\; see the rubric.\r
URL:https://canvas.qut.edu.au/courses/301/assignments/9911\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART:{d6}\r
UID:event-assignment-9912@instructure.com\r
SUMMARY:CAB444 Assignment 1: Design and implementation of secure network\r
  architecture and services - Report [CAB444_26se2]\r
URL:https://canvas.qut.edu.au/courses/301/assignments/9912\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART:{d6}\r
UID:event-assignment-9913@instructure.com\r
SUMMARY:Assessment 1.2: Week 7 [IFB398_26se2]\r
URL:https://canvas.qut.edu.au/courses/302/assignments/9913\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;TZID=Australia/Brisbane:{tz20}\r
UID:event-assignment-9914@instructure.com\r
SUMMARY:Assessment 1.3: Week 9 [IFB398_26se2]\r
URL:https://canvas.qut.edu.au/courses/302/assignments/9914\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:{day16}\r
UID:event-calendar-event-7001@instructure.com\r
SUMMARY:CAB432 Mid-semester Test [CAB432_26se2]\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:{day4}\r
UID:event-calendar-event-7002@instructure.com\r
SUMMARY:IFB398 Week 8 Lecture [IFB398_26se2]\r
END:VEVENT\r
END:VCALENDAR\r
"""


def build_feed():
    return FEED.format(
        d6=(NOW + dt.timedelta(days=6)).astimezone(dt.timezone.utc)
            .strftime("%Y%m%dT%H%M%SZ"),
        tz20=(NOW + dt.timedelta(days=20)).strftime("%Y%m%dT%H%M%S"),
        day16=(NOW + dt.timedelta(days=16)).strftime("%Y%m%d"),
        day4=(NOW + dt.timedelta(days=4)).strftime("%Y%m%d"),
    )


def run_feed_checks(out):
    """Feed → items → weights → menu rows."""
    from canvasdates import config, fetcher, icsfeed, items, store

    icsfeed.download = lambda url, timeout=25: build_feed()

    # Weights as Zain would enter them from his unit outlines.
    table = {}
    store.set_weight(table, "CAB444", "Assignment 1: Design and implementation "
                     "of secure network architecture and services - "
                     "Implementation", 30)
    store.set_weight(table, "CAB444", "Assignment 1: Design and implementation "
                     "of secure network architecture and services - Report", 20)
    store.set_weight(table, "IFB398", "Assessment 1.2: Week 7", 25)
    store.set_weight(table, "IFB398", "Assessment 1.3: Week 9", 25)
    store.save_weights(table)

    payload = fetcher.fetch_feed(dict(config.DEFAULTS), "https://fake/feed.ics")
    got = [items.DueItem.from_dict(d) for d in payload["items"]]

    out.append("\n  MENU BAR:  [ %s ]\n" % items.bar_title(got, NOW))
    out.append("  DROPDOWN:")
    for item in got:
        out.append("    " + items.menu_label(item, NOW))
    out.append("\n  STILL TO HAND IN, BY UNIT:")
    for code, pct, count in items.remaining_by_unit(got):
        out.append("    %s — %s of the final grade across %d item(s)"
                   % (code, items.weight_label(pct), count))

    titles = [i.title for i in got]
    joined = " | ".join(titles)
    weights = {i.title: i.weight for i in got}
    rows = [items.menu_label(i, NOW) for i in got if i.course == "CAB444"]

    checks = [
        ("feed: underscore unit codes parsed",
         {i.course for i in got} == {"CAB444", "IFB398", "CAB432"}),
        ("feed: folded summary rebuilt exactly (no lost space)",
         ("Assignment 1: Design and implementation of secure network "
          "architecture and services - Implementation") in titles),
        ("feed: plain lecture excluded", "Week 8 Lecture" not in joined),
        ("feed: calendar exam included", "Mid-semester Test" in joined),
        ("feed: all-day event lands end of day",
         all(i.due.hour == 23 for i in got if "Mid-semester" in i.title)),
        ("feed: sorted by due date",
         got == sorted(got, key=lambda i: i.due_iso)),
        ("feed: split assignment rows stay distinct",
         len(rows) >= 2 and rows[0] != rows[1]),
        ("weights: Implementation 30%",
         abs((weights.get([t for t in titles if t.endswith("Implementation")][0])
              or 0) - 30) < .01),
        ("weights: Report 20%",
         abs((weights.get([t for t in titles if t.endswith("Report")][0])
              or 0) - 20) < .01),
        ("weights: Week 7 25%",
         abs((weights.get("Assessment 1.2: Week 7") or 0) - 25) < .01),
        ("weights: unweighted exam shows no %",
         [i for i in got if "Mid-semester" in i.title][0].weight is None),
        ("weights: CAB432 flagged as not entered",
         any(c["code"] == "CAB432" and not c["entered"]
             for c in payload["courses"])),
    ]

    # Marking something done removes it and survives a refetch.
    target = [i for i in got if i.title == "Assessment 1.2: Week 7"][0]
    store.save_dismissed(store.dismiss({}, target.course, target.title,
                                       target.due_iso))
    again = fetcher.fetch_feed(dict(config.DEFAULTS), "https://fake/feed.ics")
    checks.append(("done: dismissed item stays hidden after refresh",
                   not any(i["title"] == "Assessment 1.2: Week 7"
                           for i in again["items"])))
    checks.append(("done: other items untouched",
                   len(again["items"]) == len(got) - 1))
    store.save_dismissed({})
    return checks


# ── Part 2: the API token path, kept alive in case QUT ever issues one ────

# Zain's real units, with invented assessment structure.
FAKE = {
    "courses": [
        {"id": 1, "name": "Secure Network Architectures",
         "course_code": "CAB444_26se2", "apply_assignment_group_weights": True},
        {"id": 2, "name": "IT Capstone Project (Phase 1)",
         "course_code": "IFB398_26se2", "apply_assignment_group_weights": False},
        {"id": 3, "name": "Cloud Computing",
         "course_code": "CAB432_26se2", "apply_assignment_group_weights": True},
    ],
    "courses/1/assignment_groups": [
        {"id": 10, "name": "Assignment 1", "group_weight": 50},
        {"id": 11, "name": "Final Exam", "group_weight": 50}],
    "courses/2/assignment_groups": [{"id": 20, "name": "Portfolio",
                                     "group_weight": 0}],
    "courses/3/assignment_groups": [
        {"id": 30, "name": "Assessment", "group_weight": 70},
        {"id": 31, "name": "Participation", "group_weight": 30}],

    # Two submissions for one assignment, differing only at the tail —
    # exactly the shape that broke the old title truncation.
    "courses/1/assignments": [
        {"id": 101, "name": ("CAB444 Assignment 1: Design and implementation "
                             "of secure network architecture and services - "
                             "Implementation"),
         "assignment_group_id": 10, "points_possible": 30, "due_at": at(days=6),
         "html_url": "https://canvas/1/a/101",
         "submission_types": ["online_upload"], "submission": {}},
        {"id": 102, "name": ("CAB444 Assignment 1: Design and implementation "
                             "of secure network architecture and services - "
                             "Report"),
         "assignment_group_id": 10, "points_possible": 20, "due_at": at(days=6),
         "html_url": "https://canvas/1/a/102",
         "submission_types": ["online_upload"], "submission": {}},
        {"id": 103, "name": "CAB444 Final Exam", "assignment_group_id": 11,
         "points_possible": 100, "due_at": at(days=48),
         "html_url": "https://canvas/1/a/103",
         "submission_types": ["on_paper"], "submission": {}},
        {"id": 104, "name": "Practice lab (ungraded)", "assignment_group_id": 10,
         "grading_type": "not_graded", "due_at": at(days=3), "submission": {}},
    ],
    "courses/2/assignments": [
        {"id": 201, "name": "Assessment 1.1: Week 5", "assignment_group_id": 20,
         "points_possible": 20, "due_at": at(days=-8),
         "submission": {"submitted_at": at(days=-9)}},
        {"id": 202, "name": "Assessment 1.2: Week 7", "assignment_group_id": 20,
         "points_possible": 20, "due_at": at(days=6),
         "html_url": "https://canvas/2/a/202", "submission": {}},
        {"id": 203, "name": "Assessment 1.3: Week 9", "assignment_group_id": 20,
         "points_possible": 20, "due_at": at(days=20),
         "html_url": "https://canvas/2/a/203", "submission": {}},
        {"id": 204, "name": "Draft not yet published", "assignment_group_id": 20,
         "points_possible": 40, "published": False, "due_at": at(days=10),
         "submission": {}},
    ],
    "courses/3/assignments": [
        {"id": 301, "name": "CAB432 Assessment 1: Cloud Services Exercises",
         "assignment_group_id": 30, "points_possible": 35, "due_at": at(days=13),
         "html_url": "https://canvas/3/a/301", "submission": {}},
        {"id": 302, "name": "Weekly participation", "assignment_group_id": 31,
         "points_possible": 10, "due_at": at(days=1, hours=4),
         "html_url": "https://canvas/3/a/302",
         "submission_types": ["online_quiz"], "submission": {}},
    ],
    "calendar_events": [
        {"id": 900, "title": "CAB432 Mid-semester Test", "context_code": "course_3",
         "start_at": at(days=16), "html_url": "https://canvas/e/900"},
        {"id": 901, "title": "IFB398 Week 8 Lecture", "context_code": "course_2",
         "start_at": at(days=4), "html_url": "https://canvas/e/901"},
    ],
}


def fake_get(self, path, params=None):
    return FAKE.get(path.split("?")[0], [])


def isolate():
    """Point config at a scratch dir so the real settings are never touched."""
    import pathlib, tempfile
    from canvasdates import config, store
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="canvas-dates-selftest-"))
    config.CONFIG_DIR = scratch
    config.CONFIG_PATH = scratch / "config.json"
    config.CACHE_PATH = scratch / "cache.json"
    store.WEIGHTS_PATH = scratch / "weights.json"
    store.DISMISSED_PATH = scratch / "dismissed.json"
    store.MANUAL_PATH = scratch / "manual.json"
    return scratch


def run_token_checks(out):
    """The API-token path: weight maths straight from Canvas."""
    from canvasdates import canvas, fetcher, items
    from canvasdates.config import DEFAULTS

    canvas.CanvasClient.get = fake_get
    payload = fetcher.fetch(dict(DEFAULTS), "fake-token")
    got = [items.DueItem.from_dict(d) for d in payload["items"]]

    titles = [i.title for i in got]
    joined = " | ".join(titles)
    weights = {i.title: i.weight for i in got}
    checks = [
        ("token: ungraded practice excluded",
         "Practice lab (ungraded)" not in titles),
        ("token: unpublished draft excluded",
         "Draft not yet published" not in titles),
        ("token: submitted work excluded",
         "Assessment 1.1: Week 5" not in titles),
        ("token: plain lecture excluded", "Week 8 Lecture" not in joined),
        ("token: sorted by due date",
         got == sorted(got, key=lambda i: i.due_iso)),
    ]
    for needle, want in [("Implementation", 30), ("Report", 20),
                         ("Final Exam", 50), ("Assessment 1.2: Week 7", 33.33),
                         ("Cloud Services Exercises", 70),
                         ("Weekly participation", 30)]:
        found = [v for k, v in weights.items() if needle in k]
        checks.append(("token: %s worth %g%%" % (needle[:34], want),
                       bool(found) and abs((found[0] or 0) - want) < 0.05))
    return checks


# ── Part 3: the menu itself, against a stand-in for rumps ────────────────

RUMPS_STUB = """
separator = object()          # a sentinel, as in real rumps — not a string
class MenuItem:
    def __init__(self, title, callback=None, key=None):
        self.title, self.callback, self.state, self.children = title, callback, 0, []
    def add(self, item): self.children.append(item)
    def clear(self): self.children = []
class App:
    def __init__(self, name, title=None, icon=None, menu=None, quit_button="Quit"):
        self.name, self.title, self.menu = name, title, MenuItem("root")
    def run(self): pass
class Timer:
    def __init__(self, cb, interval): self.cb, self.interval = cb, interval
    def start(self): pass
class Window:
    def __init__(self, **kw): pass
    def run(self): return type("R", (), {"clicked": 0, "text": ""})()
def alert(*a, **k): pass
def quit_application(*a, **k): pass
"""


def run_menu_checks(out):
    """Build the real menu, but with rumps replaced by a stand-in.

    Runs the same way on any machine, so the menu code is covered even
    though this script can't open a real menu bar.
    """
    import types
    stub = types.ModuleType("rumps")
    exec(RUMPS_STUB, stub.__dict__)
    sys.modules["rumps"] = stub

    from canvasdates import config, icsfeed, items, store
    icsfeed.download = lambda url, timeout=25: build_feed()
    config.get_feed = lambda: "https://fake/feed.ics"

    table = {}
    store.set_weight(table, "CAB444", "Assignment 1: Design and implementation "
                     "of secure network architecture and services - "
                     "Implementation", 30)
    store.save_weights(table)
    store.save_dismissed({})

    from canvasdates.app import CanvasDates
    app = CanvasDates()
    app._worker()
    app._collect(None)

    out.append("\n  MENU TREE:")

    def walk(node, depth=2):
        for child in node.children:
            if child == stub.separator:
                out.append(" " * depth + "─────────")
                continue
            mark = " [x]" if getattr(child, "state", 0) else ""
            out.append(" " * depth + child.title + mark)
            walk(child, depth + 2)
    walk(app.menu)

    def child(row, prefix):
        return next(c for c in row.children if c.title.startswith(prefix))

    def is_row(node):
        # A due-date row is a MenuItem whose submenu offers "Mark as
        # submitted"; separators have no children.
        kids = getattr(node, "children", None)
        return bool(kids) and any(
            getattr(c, "title", "").startswith("Mark as submitted")
            for c in kids)

    rows = [m for m in app.menu.children if is_row(m)]

    checks = [
        ("menu: no exotic characters in titles",
         all(ch.isprintable() or ch == " "
             for r in rows for ch in getattr(r, "title", ""))),
        ("menu: bar is the icon alone by default",
         app.title == "\U0001f4c5"),
        ("menu: every due row opens a submenu", len(rows) == len(app.items)),
        ("menu: each row offers 'Mark as submitted'",
         all(any(c.title.startswith("Mark as submitted") for c in r.children)
             for r in rows)),
        ("menu: each row offers 'Open in Canvas'",
         all(any(c.title.startswith("Open in Canvas") for c in r.children)
             for r in rows)),
    ]

    # Marking submitted from inside a row drops that row, not another.
    target = app._rows["Mark as submitted "]
    before = len(app.items)
    app.mark_submitted(child(rows[0], "Mark as submitted"))
    checks += [
        ("menu: marking submitted removes that row",
         len(app.items) == before - 1),
        ("menu: it removed the right one",
         all(i.title != target.title for i in app.items)),
        ("menu: dismissal persisted", len(store.load_dismissed()) == 1),
    ]
    app.restore_dismissed(None)
    app._worker(); app._collect(None)
    checks.append(("menu: un-hiding brings it back", len(app.items) == before))

    # Adding a due date by hand.
    checks += [
        ("add: rejects a line with too few parts",
         app._save_manual("just this") is not None),
        ("add: rejects an unreadable date",
         app._save_manual("CAB432 | Thing | someday | 10") is not None),
        ("add: rejects a silly percentage",
         app._save_manual("CAB432 | Thing | 15 Oct | 400") is not None),
        ("add: accepts a good line",
         app._save_manual("cab432 | Assignment 2 | 15 Oct 5pm | 40") is None),
    ]
    app._worker(); app._collect(None)
    added = [i for i in app.items if i.manual]
    checks += [
        ("add: it shows up in the list", len(added) == 1),
        ("add: unit upper-cased", added and added[0].course == "CAB432"),
        ("add: time understood", added and added[0].due.hour == 17),
        ("add: weight carried through", added and added[0].weight == 40),
        ("add: urgency worked out automatically",
         added and items.urgency(added[0].due, NOW) in ("\u26a0\ufe0f", "\u23f0",
                                                        "\U0001f534", "\U0001f7e0",
                                                        "\U0001f7e1", "\u26aa")),
        ("add: marked as yours in the row",
         added and "added by you" in items.menu_label(added[0], NOW)),
    ]
    # Marking a manual item submitted deletes it outright.
    app._rebuild()
    tag = next(t for t, i in app._rows.items()
               if i.manual and t.startswith("Mark as submitted"))
    app.mark_submitted(type("S", (), {"title": tag})())
    checks.append(("add: marking yours submitted deletes it",
                   store.load_manual() == []))

    # Settings must persist and survive a tick.
    app.set_look_ahead(type("S", (), {"title": "30 days"})())
    app._on_minute(None)
    checks.append(("menu: settings persist", config.load()["days_ahead"] == 30))

    kept = len(app.items)
    app._pending = ("error", "Could not reach Canvas: offline")
    app._collect(None)
    checks.append(("menu: outage keeps the cached list",
                   len(app.items) == kept and "offline" in (app.error or "")))

    app._pending = ("noauth", "No Canvas calendar feed saved yet.")
    app._collect(None)
    checks.append(("menu: missing feed shows setup prompt",
                   app.needs_setup and app.title == "⚙️"))

    app.items, app.needs_setup, app.error = [], False, None
    app._rebuild()
    checks.append(("menu: empty semester still just the icon",
                   app.title == "\U0001f4c5"))

    # Turning details on brings the next item back into the bar.
    app.items = [items.DueItem.from_dict(d) for d in
                 __import__("canvasdates.fetcher", fromlist=["x"])
                 .fetch_feed(config.load(), "https://fake/feed.ics")["items"]]
    app.toggle_bar_details(None)
    checks.append(("menu: details mode shows the next item",
                   "CAB444" in app.title and app.title.startswith("\U0001f4c5")))
    app.toggle_bar_details(None)
    checks.append(("menu: toggling back returns to the icon alone",
                   app.title == "\U0001f4c5"))

    # Something past its due date must change the icon to a clock.
    late = items.DueItem.from_dict(dict(
        title="Overdue thing", course="CAB432", course_name="CAB432",
        due_iso=(NOW - dt.timedelta(days=1)).isoformat(), url="",
        kind="assignment", submitted=False))
    checks += [
        ("bar: clock when something is overdue",
         items.bar_title([late], NOW) == "\u23f0"),
        ("bar: calendar when nothing is overdue",
         items.bar_title(app.items, NOW) == "\U0001f4c5"),
        ("bar: clock wins even if the next item is not the late one",
         items.bar_title(sorted(app.items + [late],
                                key=lambda i: i.due_iso), NOW) == "\u23f0"),
        ("bar: clock carries into details mode",
         items.bar_title([late], NOW, details=True).startswith("\u23f0")),
        ("bar: empty list is a calendar",
         items.bar_title([], NOW) == "\U0001f4c5"),
    ]
    store.save_manual([])
    return checks


def main():
    isolate()
    out, checks = [], []

    out.append("\n" + "=" * 68)
    out.append("  FAKE DATA — invented marks and dates. Not your Canvas.")
    out.append("=" * 68)

    checks += run_feed_checks(out)
    checks += run_token_checks(out)
    checks += run_menu_checks(out)

    print("\n".join(out))
    print()
    failed = 0
    for name, ok in checks:
        print("   %s %s" % ("PASS" if ok else "FAIL", name))
        failed += 0 if ok else 1
    print()
    if failed:
        print("  %d of %d check(s) failed." % (failed, len(checks)))
        return 1
    print("  All %d checks passed." % len(checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
