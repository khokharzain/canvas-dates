"""The due-date model: one normalised item, plus how to render it."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, asdict

# QUT unit codes look like CAB444, IFB398, QUT006 — and Canvas appends a
# teaching period with an underscore ("CAB444_26se2"). A trailing \b would
# never fire there, because '_' counts as a word character, so match on
# "not followed by another code character" instead.
UNIT_CODE = re.compile(r"\b([A-Z]{2,4}\d{3})(?![0-9A-Z])")

# Rough signals that a calendar event is an exam rather than a lecture.
EXAM_WORDS = re.compile(
    r"\b(exam|final|midsem|mid-sem|test|quiz|viva|prac(tical)? test|"
    r"invigilat|assessment)\b", re.I)


def parse_time(value: str | None) -> dt.datetime | None:
    """Parse a Canvas ISO-8601 timestamp into an aware local datetime."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        stamp = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp.astimezone()


def short_code(course: dict) -> str:
    """Pull a unit code out of a Canvas course, e.g. 'CAB420'."""
    for field in ("course_code", "name"):
        match = UNIT_CODE.search((course.get(field) or "").upper())
        if match:
            return match.group(1)
    raw = (course.get("course_code") or course.get("name") or "Course").strip()
    return raw.split("_")[0][:12] or "Course"


def trim_title(title: str | None, course_code: str) -> str:
    """Strip a leading unit code so rows don't read 'CAB420 · CAB420 Exam'."""
    text = (title or "Untitled").strip()
    pattern = re.compile(r"^%s\b[\s:\-–_]*" % re.escape(course_code), re.I)
    trimmed = pattern.sub("", text).strip()
    return trimmed or text


@dataclass
class DueItem:
    title: str
    course: str
    course_name: str
    due_iso: str
    url: str
    kind: str          # assignment | quiz | exam | event
    submitted: bool
    # Estimated share of the unit's final grade, as a percentage.
    # None when Canvas gives no marks for the item (e.g. a calendar event).
    weight: float | None = None
    points: float | None = None
    # True for due dates you typed in yourself rather than read from Canvas.
    manual: bool = False

    @property
    def due(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.due_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DueItem":
        return cls(**data)


# -- formatting -----------------------------------------------------------

def countdown(item_due: dt.datetime, now: dt.datetime) -> str:
    """A tight relative label: '4h', '2d', 'now', '-3d' when overdue."""
    delta = item_due - now
    seconds = delta.total_seconds()
    overdue = seconds < 0
    seconds = abs(seconds)

    if seconds < 3600:
        label = "%dm" % max(1, round(seconds / 60))
    elif seconds < 86400:
        label = "%dh" % round(seconds / 3600)
    elif seconds < 86400 * 14:
        label = "%dd" % round(seconds / 86400)
    else:
        label = "%dw" % round(seconds / (86400 * 7))
    return (label + " late") if overdue else label


def urgency(item_due: dt.datetime, now: dt.datetime) -> str:
    """A coloured dot conveying how soon something is due."""
    hours = (item_due - now).total_seconds() / 3600
    if hours < 0:
        return "⏰"      # clock — overdue
    if hours < 24:
        return "\U0001f534"        # red — today
    if hours < 72:
        return "\U0001f7e0"        # orange — within 3 days
    if hours < 24 * 7:
        return "\U0001f7e1"        # yellow — this week
    return "⚪"                # white — later


def when(item_due: dt.datetime, now: dt.datetime) -> str:
    """A human date: 'Today 11:59pm', 'Tue 2 Sep, 11:59pm'."""
    time_part = item_due.strftime("%I:%M%p").lstrip("0").lower()
    days = (item_due.date() - now.date()).days
    if days == 0:
        return "Today %s" % time_part
    if days == 1:
        return "Tomorrow %s" % time_part
    if days == -1:
        return "Yesterday %s" % time_part
    if 0 < days < 7:
        return "%s %s" % (item_due.strftime("%a"), time_part)
    return "%s %s" % (item_due.strftime("%a %-d %b"), time_part)


def clip(text: str, limit: int = 58) -> str:
    """Shorten a long title from the middle, not the end.

    QUT splits one assignment across several submissions whose names differ
    only at the tail ("… - Implementation" vs "… - Report"), so trimming the
    end would make them identical in the menu.
    """
    if len(text) <= limit:
        return text
    tail = text[-20:]
    cut = tail.find(" ")
    if 0 <= cut <= 6:                 # don't start the tail mid-word
        tail = tail[cut + 1:]
    head = text[:max(8, limit - len(tail) - 1)]
    if " " in head[8:]:               # end the head on a whole word
        head = head[:head.rfind(" ")]
    return head.rstrip(" -–:,") + "…" + tail


def weight_label(weight: float | None) -> str:
    """'30%' / '12.5%' / '' when the item carries no marks."""
    if not weight:
        return ""
    if abs(weight - round(weight)) < 0.05:
        return "%d%%" % round(weight)
    return "%.1f%%" % weight


def menu_label(item: DueItem, now: dt.datetime) -> str:
    """One dropdown row."""
    title = clip(item.title)
    tick = " ✓" if item.submitted else ("  (added by you)" if item.manual else "")
    weight = weight_label(item.weight)
    weight = ("%s · " % weight) if weight else ""
    return "%s  %s · %s%s%s  —  %s (%s)" % (
        urgency(item.due, now), item.course, weight, title, tick,
        when(item.due, now), countdown(item.due, now),
    )


BAR_ICON = "\U0001f4c5"          # calendar — nothing overdue
LATE_ICON = "\u23f0"             # alarm clock — something is past due


def bar_title(items: list[DueItem], now: dt.datetime,
              show_weight: bool = True, details: bool = False) -> str:
    """What sits in the menu bar.

    By default just an icon — the list is one click away and a changing
    title makes the bar noisy. The icon becomes a clock when something is
    past its due date, so the one thing worth interrupting you for still
    gets through. Turn on details to have the next item's unit, countdown
    and weight alongside it.
    """
    icon = LATE_ICON if any(i.due < now for i in items) else BAR_ICON
    if not details:
        return icon
    if not items:
        return "%s Clear" % icon
    nxt = items[0]
    text = "%s %s %s" % (icon, nxt.course, countdown(nxt.due, now))
    weight = weight_label(nxt.weight) if show_weight else ""
    if weight:
        text += " · " + weight
    return text


def remaining_by_unit(items: list[DueItem]) -> list[tuple[str, float, int]]:
    """Per unit: (code, % of the final grade still outstanding, item count).

    Counts only unsubmitted, weighted items, so it answers 'how much of
    this unit is still riding on work I have not handed in?'
    """
    totals: dict[str, list[float]] = {}
    for item in items:
        if item.submitted or not item.weight:
            continue
        totals.setdefault(item.course, []).append(item.weight)
    rows = [(code, sum(vals), len(vals)) for code, vals in totals.items()]
    rows.sort(key=lambda row: row[1], reverse=True)
    return rows


# -- reading a date a human typed ----------------------------------------

_TIME_12H = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*$", re.I)
_TIME_24H = re.compile(r"(\d{1,2}):(\d{2})\s*$")

_DATE_FORMATS = [
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m", "%d-%m",
    "%d %b %Y", "%d %b", "%d %B %Y", "%d %B",
    "%b %d %Y", "%b %d", "%B %d %Y", "%B %d",
]


def parse_when(text: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """Read a hand-typed due date. Returns None if it makes no sense.

    Accepts "15 Oct", "15 Oct 5pm", "2026-10-15 23:59", "15/10/2026",
    "Oct 15 at 5:30pm". With no time, assumes 11:59pm — the QUT norm.
    With no year, assumes the next one that hasn't passed.
    """
    now = now or dt.datetime.now().astimezone()
    raw = re.sub(r"\bat\b|\bby\b|\bdue\b|,", " ", text or "", flags=re.I)
    raw = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", raw, flags=re.I)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return None

    clock = None
    match = _TIME_12H.search(raw)
    if match:
        hour12 = int(match.group(1))
        if not 1 <= hour12 <= 12:      # "25pm" is a typo, not 1pm
            return None
        hour = hour12 % 12
        if match.group(3).lower() == "pm":
            hour += 12
        clock = (hour, int(match.group(2) or 0))
        raw = raw[:match.start()].strip()
    else:
        match = _TIME_24H.search(raw)
        if match and len(raw[:match.start()].strip()) > 0:
            clock = (int(match.group(1)), int(match.group(2)))
            raw = raw[:match.start()].strip()

    for fmt in _DATE_FORMATS:
        try:
            parsed = dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue

        hour, minute = clock or (23, 59)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return None
        year = parsed.year if "%Y" in fmt else now.year
        try:
            result = dt.datetime(year, parsed.month, parsed.day,
                                 hour, minute).astimezone()
        except ValueError:
            return None
        # A bare "15 Oct" that has already gone means next year.
        if "%Y" not in fmt and result < now - dt.timedelta(days=1):
            try:
                result = result.replace(year=year + 1)
            except ValueError:
                return None
        return result
    return None
