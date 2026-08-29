"""Pull upcoming due dates out of Canvas and work out what each is worth."""

from __future__ import annotations

import datetime as dt

from . import config, icsfeed, store
from .canvas import AuthError, CanvasClient, CanvasError
from .items import DueItem, EXAM_WORDS, parse_time, short_code, trim_title


class NeedsSetup(Exception):
    """No feed URL or token saved yet."""


def is_real_assessment(assignment: dict, include_ungraded: bool = False) -> bool:
    """Should this show up at all?

    Unpublished drafts and items Canvas marks 'not graded' are practice or
    admin scaffolding, not assessment, so they stay out by default.
    """
    if assignment.get("published") is False:
        return False
    if not include_ungraded and assignment.get("grading_type") == "not_graded":
        return False
    return True


def counts_toward_grade(assignment: dict) -> bool:
    """Does this assignment contribute marks to the final grade?"""
    if assignment.get("published") is False:
        return False
    if assignment.get("grading_type") == "not_graded":
        return False
    if assignment.get("omit_from_final_grade"):
        return False
    return assignment.get("points_possible") is not None


def compute_weights(course: dict, groups: list[dict],
                    assignments: list[dict]) -> tuple[dict[int, float], float]:
    """Map assignment id -> estimated % of the unit's final grade.

    Canvas grades a unit one of two ways:

      * weighted groups - each assignment group is worth a fixed slice of
        the final grade (e.g. "Assignments 40%, Exam 60%"), and items
        inside a group split that slice by their marks;
      * straight points - every item's share is its marks over the total.

    Returns (weights, total) where total is the sum of all weights, which
    should land near 100 when the data is complete.
    """
    graded = [a for a in assignments if counts_toward_grade(a)]
    weights: dict[int, float] = {}

    if course.get("apply_assignment_group_weights"):
        group_weight = {g["id"]: float(g.get("group_weight") or 0.0)
                        for g in groups if g.get("id")}
        points_in_group: dict[int, float] = {}
        count_in_group: dict[int, int] = {}
        for a in graded:
            gid = a.get("assignment_group_id")
            points_in_group[gid] = (points_in_group.get(gid, 0.0)
                                    + float(a.get("points_possible") or 0.0))
            count_in_group[gid] = count_in_group.get(gid, 0) + 1

        for a in graded:
            gid = a.get("assignment_group_id")
            slice_pct = group_weight.get(gid, 0.0)
            group_points = points_in_group.get(gid, 0.0)
            points = float(a.get("points_possible") or 0.0)
            if group_points > 0:
                weights[a["id"]] = slice_pct * points / group_points
            else:
                # A group whose items are all worth zero marks: split evenly.
                weights[a["id"]] = slice_pct / max(1, count_in_group.get(gid, 1))
    else:
        total_points = sum(float(a.get("points_possible") or 0.0)
                           for a in graded)
        for a in graded:
            points = float(a.get("points_possible") or 0.0)
            weights[a["id"]] = (points / total_points * 100.0
                                if total_points > 0 else 0.0)

    return weights, sum(weights.values())


def is_submitted(assignment: dict) -> bool:
    sub = assignment.get("submission") or {}
    if sub.get("submitted_at"):
        return True
    if sub.get("workflow_state") in ("submitted", "graded", "complete"):
        return True
    return sub.get("score") is not None


def kind_of(assignment: dict) -> str:
    types = assignment.get("submission_types") or []
    if assignment.get("is_quiz_assignment") or "online_quiz" in types:
        return "quiz"
    if EXAM_WORDS.search(assignment.get("name") or ""):
        return "exam"
    return "assignment"


def fetch(cfg: dict, token: str) -> dict:
    """Return a cache-shaped payload of everything coming up."""
    client = CanvasClient(cfg["base_url"], token)
    now = dt.datetime.now().astimezone()
    horizon = now + dt.timedelta(days=int(cfg["days_ahead"]))
    floor = now - dt.timedelta(days=int(cfg["overdue_grace_days"]))

    courses = client.active_courses()
    items: list[DueItem] = []
    course_notes: list[dict] = []
    skipped: list[str] = []

    for course in courses:
        cid = course.get("id")
        code = short_code(course)
        name = (course.get("name") or code).strip()
        try:
            groups = client.assignment_groups(cid)
            assignments = client.assignments(cid)
        except AuthError:
            raise
        except CanvasError:
            skipped.append(code)
            continue

        weights, weight_total = compute_weights(course, groups, assignments)
        course_notes.append({
            "code": code,
            "name": name,
            "weight_total": round(weight_total, 1),
            "weighted": bool(course.get("apply_assignment_group_weights")),
        })

        for a in assignments:
            if not is_real_assessment(a, cfg.get("include_ungraded", False)):
                continue
            due = parse_time(a.get("due_at"))
            if due is None or due > horizon or due < floor:
                continue
            submitted = is_submitted(a)
            if submitted and (cfg.get("hide_submitted") or due < now):
                continue
            weight = weights.get(a.get("id"))
            items.append(DueItem(
                title=trim_title(a.get("name"), code),
                course=code,
                course_name=name,
                due_iso=due.isoformat(),
                url=a.get("html_url") or "",
                kind=kind_of(a),
                submitted=submitted,
                weight=round(weight, 2) if weight else None,
                points=a.get("points_possible"),
            ))

    # Exams and in-class assessment are often calendar events, not
    # assignments — pick up the ones that read like assessment.
    by_context = {"course_%s" % c.get("id"): c for c in courses}
    try:
        events = client.calendar_events(
            list(by_context), now.isoformat(), horizon.isoformat())
    except CanvasError:
        events = []

    seen = {(i.course, i.title.lower(), i.due_iso[:10]) for i in items}
    for event in events:
        if event.get("hidden") or event.get("workflow_state") == "deleted":
            continue
        title = (event.get("title") or "").strip()
        if not cfg.get("all_calendar_events") and not EXAM_WORDS.search(title):
            continue
        start = parse_time(event.get("start_at"))
        if start is None or start > horizon or start < now:
            continue
        course = by_context.get(event.get("context_code")) or {}
        code = short_code(course) if course else "Calendar"
        key = (code, title.lower(), start.isoformat()[:10])
        if key in seen:
            continue
        seen.add(key)
        items.append(DueItem(
            title=trim_title(title, code) or "Scheduled event",
            course=code,
            course_name=(course.get("name") or code).strip(),
            due_iso=start.isoformat(),
            url=event.get("html_url") or "",
            kind="exam" if EXAM_WORDS.search(title) else "event",
            submitted=False,
        ))

    items.sort(key=lambda i: i.due_iso)

    return {
        "fetched_at": now.isoformat(),
        "items": [i.to_dict() for i in items],
        "courses": course_notes,
        "skipped": skipped,
    }


# -- calendar feed source -------------------------------------------------

def fetch_feed(cfg: dict, feed_url: str) -> dict:
    """Build the same payload from Canvas's private .ics feed.

    The feed has no marks and no submission status, so weights come from
    weights.json and 'done' comes from dismissed.json.
    """
    events = icsfeed.parse_events(icsfeed.download(feed_url))

    now = dt.datetime.now().astimezone()
    horizon = now + dt.timedelta(days=int(cfg["days_ahead"]))
    floor = now - dt.timedelta(days=int(cfg["overdue_grace_days"]))

    weights = store.load_weights()
    dismissed = store.prune(store.load_dismissed())
    if dismissed != store.load_dismissed():
        store.save_dismissed(dismissed)

    items: list[DueItem] = []
    seen: set[tuple] = set()
    names: dict[str, str] = {}

    for event in events:
        summary = (event.get("SUMMARY") or {}).get("value", "")
        title, unit = icsfeed.split_summary(summary)
        due = icsfeed.parse_dt(event.get("DTSTART"))
        if due is None or due > horizon or due < floor:
            continue

        code = short_code({"course_code": unit, "name": ""}) if unit else "Canvas"
        names.setdefault(code, unit or code)
        title = trim_title(title, code)
        if not title:
            continue

        kind = icsfeed.kind_of(event)
        if (kind == "event" and not cfg.get("all_calendar_events")
                and not EXAM_WORDS.search(title)):
            continue
        if store.is_dismissed(dismissed, code, title, due.isoformat()):
            continue

        key = (code, store.normalise(title), due.isoformat()[:10])
        if key in seen:
            continue
        seen.add(key)

        items.append(DueItem(
            title=title,
            course=code,
            course_name=names[code],
            due_iso=due.isoformat(),
            # Some calendar entries carry no link; send those to the
            # Canvas calendar rather than leaving a dead row.
            url=((event.get("URL") or {}).get("value", "").strip()
                 or cfg["base_url"].rstrip("/") + "/calendar"),
            kind=kind,
            submitted=False,
            weight=store.lookup_weight(weights, code, title),
        ))

    # Due dates you added yourself, for assessment Canvas hasn't published.
    for row in store.load_manual():
        due = parse_time(row.get("due_iso"))
        code = (row.get("course") or "Added").strip()
        title = (row.get("title") or "").strip()
        if due is None or not title or due > horizon or due < floor:
            continue
        if store.is_dismissed(dismissed, code, title, due.isoformat()):
            continue
        key = (code, store.normalise(title), due.isoformat()[:10])
        if key in seen:
            continue
        seen.add(key)
        names.setdefault(code, code)
        items.append(DueItem(
            title=title,
            course=code,
            course_name=names[code],
            due_iso=due.isoformat(),
            url="",
            kind="assignment",
            submitted=False,
            weight=row.get("weight"),
            manual=True,
        ))

    items.sort(key=lambda i: i.due_iso)

    totals = store.unit_totals(weights)
    skipped_units = set(cfg.get("skip_weights") or [])
    course_notes = [{
        "code": code,
        "name": names[code],
        "weight_total": totals.get(code, 0),
        "weighted": code not in skipped_units,
        "entered": code in totals or code in skipped_units,
    } for code in sorted(names)]

    return {
        "fetched_at": now.isoformat(),
        "items": [i.to_dict() for i in items],
        "courses": course_notes,
        "skipped": [],
        "source": "feed",
    }


def collect(cfg: dict) -> dict:
    """Fetch from whichever source is configured."""
    if cfg.get("source") == "token":
        token = config.get_token()
        if not token:
            raise NeedsSetup("No Canvas token saved yet.")
        return fetch(cfg, token)

    feed_url = config.get_feed()
    if not feed_url:
        raise NeedsSetup("No Canvas calendar feed saved yet.")
    return fetch_feed(cfg, feed_url)
