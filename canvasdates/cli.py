"""Terminal companion.

    python3 -m canvasdates.cli setup     connect your Canvas calendar feed
    python3 -m canvasdates.cli weights   enter what each assessment is worth
    python3 -m canvasdates.cli list      print what's due
    python3 -m canvasdates.cli doctor    diagnose problems
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys

from . import config, icsfeed, store
from .canvas import CanvasError
from .fetcher import NeedsSetup, collect, fetch_feed
from .items import DueItem, menu_label, remaining_by_unit, weight_label, when

FEED_HELP = """
QUT has switched off student API tokens, so Canvas Dates uses your private
calendar feed instead. To find it:

  1. Open Canvas and click Calendar in the left sidebar
  2. Scroll down the right-hand column and click "Calendar Feed"
  3. Copy the link it shows you

Treat that link like a password — anyone holding it can read your calendar.
It is stored in your macOS Keychain.
"""


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


# -- setup ----------------------------------------------------------------

def cmd_setup(argv: list[str]) -> int:
    cfg = config.load()
    print("Canvas Dates setup")
    print(FEED_HELP)

    url = ask("Paste the calendar feed link: ")
    if not url:
        print("Nothing entered. Nothing saved.")
        return 1

    print("\nChecking that link…")
    try:
        events = icsfeed.parse_events(icsfeed.download(url))
    except CanvasError as exc:
        print("  %s" % exc)
        return 1

    units = sorted({icsfeed.split_summary(
        (e.get("SUMMARY") or {}).get("value", ""))[1] or "?" for e in events})
    print("  Found %d calendar entries across %d unit(s):"
          % (len(events), len(units)))
    for unit in units:
        print("    • %s" % unit)

    where = config.set_feed(url)
    cfg["source"] = "feed"
    config.save(cfg)
    print("\nFeed saved to your %s."
          % ("macOS Keychain" if where == "keychain" else "config folder"))

    print("\nThe feed carries due dates but not marks, so the percentages")
    print("have to come from you — once per semester.")
    if ask("Enter them now? [Y/n]: ").lower() in ("", "y", "yes"):
        return cmd_weights([])
    print("Run this any time:  python3 -m canvasdates.cli weights")
    return 0


# -- weights --------------------------------------------------------------

def cmd_weights(argv: list[str]) -> int:
    redo_all = "all" in argv
    cfg = dict(config.load())
    cfg["days_ahead"] = 300          # cover the whole semester, exams included
    cfg["overdue_grace_days"] = 120  # and assessment already passed

    feed_url = config.get_feed()
    if not feed_url:
        print("No calendar feed saved. Run: python3 -m canvasdates.cli setup")
        return 1

    try:
        payload = fetch_feed(cfg, feed_url)
    except CanvasError as exc:
        print(exc)
        return 1

    items = [DueItem.from_dict(d) for d in payload["items"]]
    if not items:
        print("No assessment found in the feed.")
        return 1

    table = store.load_weights()
    now = dt.datetime.now().astimezone()

    print("\nEnter what each item is worth, from your unit outlines.")
    print("  Enter  skip this item     s  skip this unit for good")
    print("  q      stop and save\n")

    settings = config.load()
    skipped = set(settings.get("skip_weights") or [])

    break_all = False
    by_unit: dict[str, list[DueItem]] = {}
    for item in items:
        by_unit.setdefault(item.course, []).append(item)

    for unit, unit_items in sorted(by_unit.items()):
        print("─" * 66)
        print("  %s%s" % (unit, "   (set to due dates only)"
                          if unit in skipped else ""))
        print("─" * 66)
        if unit in skipped:
            print("  Skipping — type 's' here again to start weighting it.")
            if ask("  Weight this unit after all? [y/N]: ").lower() not in ("y", "yes"):
                print()
                continue
            skipped.discard(unit)
        for item in unit_items:
            existing = store.lookup_weight(table, unit, item.title)
            if existing is not None and not redo_all:
                print("  %-52s %5s  (saved)"
                      % (item.title[:52], weight_label(existing)))
                continue
            print("\n  %s" % item.title)
            print("  due %s" % when(item.due, now))
            answer = ask("  %% of %s's final grade: " % unit)
            if answer.lower() == "q":
                break_all = True
                break
            if answer.lower() == "s":
                skipped.add(unit)
                print("  %s set to due dates only — it won't ask again." % unit)
                break
            if not answer:
                continue
            try:
                value = float(answer.rstrip("%").strip())
            except ValueError:
                print("  Not a number — skipped.")
                continue
            store.set_weight(table, unit, item.title, value)
        print()
        if break_all:
            break

    store.save_weights(table)
    settings["skip_weights"] = sorted(skipped)
    config.save(settings)
    if break_all:
        print("\nStopped. What you entered is saved.")
        return 0

    print("═" * 66)
    totals = store.unit_totals(table)
    for unit in sorted(by_unit):
        if unit in skipped:
            print("  %-10s %6s" % (unit, "due dates only"))
            continue
        total = totals.get(unit, 0)
        flag = "" if abs(total - 100) < 0.5 else "   ← doesn't add to 100 yet"
        print("  %-10s %6s%s" % (unit, weight_label(total) or "0%", flag))
    print("═" * 66)
    print("\nSaved to %s" % store.WEIGHTS_PATH)
    return 0


# -- list / doctor --------------------------------------------------------

def cmd_list(argv: list[str]) -> int:
    try:
        payload = collect(config.load())
    except NeedsSetup as exc:
        print("%s\nRun: python3 -m canvasdates.cli setup" % exc)
        return 1
    except CanvasError as exc:
        print(exc)
        return 1

    config.save_cache(payload)
    items = [DueItem.from_dict(d) for d in payload["items"]]
    now = dt.datetime.now().astimezone()

    if not items:
        print("Nothing due in the next %d days." % config.load()["days_ahead"])
        return 0

    print()
    for item in items:
        print("  " + menu_label(item, now))

    rows = remaining_by_unit(items)
    if rows:
        print("\n  Still to hand in:")
        for code, pct, count in rows:
            print("    %-8s %-7s of the final grade  (%d item%s)"
                  % (code, weight_label(pct), count, "" if count == 1 else "s"))

    missing = [c["code"] for c in payload.get("courses", [])
               if not c.get("entered", True)]
    if missing:
        print("\n  No weights entered yet for: %s" % ", ".join(missing))
        print("  Run: python3 -m canvasdates.cli weights")
    print()
    return 0


def cmd_doctor(argv: list[str]) -> int:
    cfg = config.load()
    print("Source         : %s" % cfg.get("source", "feed"))
    print("Settings file  : %s" % config.CONFIG_PATH)

    feed_url = config.get_feed()
    print("Calendar feed  : %s" % ("saved" if feed_url else "MISSING — run setup"))
    if feed_url:
        try:
            events = icsfeed.parse_events(icsfeed.download(feed_url))
            print("Feed reachable : yes, %d entries" % len(events))
        except CanvasError as exc:
            print("Feed reachable : NO — %s" % exc)
            return 1

    for unit in sorted(cfg.get("skip_weights") or []):
        print("Weights %-6s : skipped on purpose (due dates only)" % unit)
    weights = store.load_weights()
    if not weights:
        print("Weights        : none entered — run 'weights'")
    else:
        for unit, total in sorted(store.unit_totals(weights).items()):
            note = "" if abs(total - 100) < 0.5 else "  (does not add to 100)"
            print("Weights %-6s : %s%s" % (unit, weight_label(total), note))

    dismissed = store.load_dismissed()
    print("Marked done    : %d item(s)" % len(dismissed))
    has_rumps = importlib.util.find_spec("rumps") is not None
    print("rumps          : %s" % ("installed" if has_rumps
                                   else "NOT installed — menu bar app won't start"))
    return 0


COMMANDS = {"setup": cmd_setup, "weights": cmd_weights,
            "list": cmd_list, "doctor": cmd_doctor}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 1
    try:
        return COMMANDS[argv[0]](argv[1:])
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
