"""Things the calendar feed can't tell us, so you tell us once.

The feed carries titles and due dates but no marks and no submission
status, so two small files fill the gap:

  weights.json    what each assessment is worth, entered from your unit
                  outlines. Fixed for the semester — a one-time job.
  dismissed.json  items you've handed in and want off the list.
  manual.json     due dates you added yourself, for assessment Canvas
                  hasn't published (or that lives outside Canvas).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re

from . import config

WEIGHTS_PATH = config.CONFIG_DIR / "weights.json"
DISMISSED_PATH = config.CONFIG_DIR / "dismissed.json"
MANUAL_PATH = config.CONFIG_DIR / "manual.json"

_NOISE = re.compile(r"[^a-z0-9]+")


def normalise(title: str) -> str:
    """Loose key so punctuation and spacing changes don't lose a weight.

    "Assessment 1.2: Week 7" and "Assessment 1.2  Week 7" both become
    "assessment 1 2 week 7".
    """
    return _NOISE.sub(" ", (title or "").lower()).strip()


def key_for(course: str, title: str, due_iso: str) -> str:
    return "%s|%s|%s" % (course, normalise(title), (due_iso or "")[:10])


def _read(path, fallback):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return fallback


def _write(path, payload):
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


# -- weights --------------------------------------------------------------

def load_weights() -> dict:
    data = _read(WEIGHTS_PATH, {})
    return data if isinstance(data, dict) else {}


def save_weights(data: dict) -> None:
    _write(WEIGHTS_PATH, data)


def lookup_weight(data: dict, course: str, title: str) -> float | None:
    unit = data.get(course) or {}
    wanted = normalise(title)
    if wanted in unit:
        return unit[wanted]
    # Canvas sometimes appends to a title after you entered the weight
    # ("Final Exam" -> "Final Exam (invigilated)"). Accept that, but only
    # on a whole-word prefix, so "Assignment 1" never matches
    # "Assignment 10".
    for stored, weight in unit.items():
        if not stored:
            continue
        if wanted.startswith(stored + " ") or stored.startswith(wanted + " "):
            return weight
    return None


def set_weight(data: dict, course: str, title: str,
               weight: float | None) -> dict:
    unit = data.setdefault(course, {})
    if weight is None:
        unit.pop(normalise(title), None)
    else:
        unit[normalise(title)] = weight
    return data


def unit_totals(data: dict) -> dict[str, float]:
    """What each unit's entered weights add up to — should be 100."""
    return {course: round(sum(items.values()), 2)
            for course, items in data.items() if items}


# -- dismissed ------------------------------------------------------------

def load_dismissed() -> dict:
    data = _read(DISMISSED_PATH, {})
    return data if isinstance(data, dict) else {}


def save_dismissed(data: dict) -> None:
    _write(DISMISSED_PATH, data)


def dismiss(data: dict, course: str, title: str, due_iso: str) -> dict:
    data[key_for(course, title, due_iso)] = due_iso
    return data


def is_dismissed(data: dict, course: str, title: str, due_iso: str) -> bool:
    return key_for(course, title, due_iso) in data


def prune(data: dict, days: int = 30) -> dict:
    """Forget dismissals whose due date is well past, so the file stays small."""
    cutoff = (dt.datetime.now().astimezone() - dt.timedelta(days=days))
    kept = {}
    for key, due_iso in data.items():
        try:
            if dt.datetime.fromisoformat(due_iso) >= cutoff:
                kept[key] = due_iso
        except (TypeError, ValueError):
            continue
    return kept


# -- manually added due dates --------------------------------------------

def load_manual() -> list:
    data = _read(MANUAL_PATH, [])
    return data if isinstance(data, list) else []


def save_manual(rows: list) -> None:
    _write(MANUAL_PATH, rows)


def add_manual(course: str, title: str, due_iso: str,
               weight: float | None = None) -> list:
    rows = [r for r in load_manual()
            if key_for(r.get("course", ""), r.get("title", ""),
                       r.get("due_iso", "")) != key_for(course, title, due_iso)]
    rows.append({"course": course, "title": title, "due_iso": due_iso,
                 "weight": weight})
    rows.sort(key=lambda r: r.get("due_iso") or "")
    save_manual(rows)
    return rows


def remove_manual(course: str, title: str, due_iso: str) -> list:
    wanted = key_for(course, title, due_iso)
    rows = [r for r in load_manual()
            if key_for(r.get("course", ""), r.get("title", ""),
                       r.get("due_iso", "")) != wanted]
    save_manual(rows)
    return rows
