"""Read Canvas's private calendar feed (.ics) — no API token needed.

QUT blocks student-generated access tokens, but every student can still
export a personal calendar feed. It carries titles, due times, unit codes
and links, but no marks — which is why assessment weights are entered by
hand in weights.py.
"""

from __future__ import annotations

import datetime as dt
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from .canvas import CanvasError

USER_AGENT = "canvas-dates/1.0 (personal due-date menu bar app)"

# Canvas puts the unit in the summary: "Assignment 1 [CAB444_26se2]"
TRAILING_UNIT = re.compile(r"\s*\[([^\]]+)\]\s*$")


def ssl_context() -> ssl.SSLContext:
    """A TLS context that works on macOS python.org builds.

    Those builds don't read the macOS Keychain for root certificates, so a
    default context has an empty trust store and every HTTPS request fails
    with CERTIFICATE_VERIFY_FAILED. certifi carries the roots instead.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_ESCAPES = {"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}


def unfold(text: str) -> list[str]:
    """iCalendar wraps long lines; continuations start with space or tab."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            out.append(_ESCAPES.get(value[i + 1], value[i + 1]))
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def parse_events(text: str) -> list[dict]:
    """Pull VEVENT blocks out into {NAME: {'value':…, 'params':{…}}} dicts."""
    events: list[dict] = []
    current: dict | None = None

    for line in unfold(text):
        stripped = line.strip()
        if stripped.upper() == "BEGIN:VEVENT":
            current = {}
            continue
        if stripped.upper() == "END:VEVENT":
            if current:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue

        head, _, value = line.partition(":")
        pieces = head.split(";")
        params = {}
        for piece in pieces[1:]:
            key, _, val = piece.partition("=")
            params[key.upper()] = val.strip('"')
        current[pieces[0].upper()] = {"value": unescape(value),
                                      "params": params}
    return events


def parse_dt(field: dict | None) -> dt.datetime | None:
    """iCalendar timestamps: UTC, zoned, floating, or whole-day."""
    if not field:
        return None
    value = field["value"].strip()
    params = field.get("params", {})

    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        try:
            day = dt.datetime.strptime(value, "%Y%m%d")
        except ValueError:
            return None
        # A whole-day due date means "by the end of that day".
        return day.replace(hour=23, minute=59).astimezone()

    naive = value[:-1] if value.endswith("Z") else value
    try:
        stamp = dt.datetime.strptime(naive, "%Y%m%dT%H%M%S")
    except ValueError:
        return None

    if value.endswith("Z"):
        return stamp.replace(tzinfo=dt.timezone.utc).astimezone()

    tzid = params.get("TZID")
    if tzid:
        try:
            from zoneinfo import ZoneInfo
            return stamp.replace(tzinfo=ZoneInfo(tzid)).astimezone()
        except Exception:
            pass
    # Floating time — read it as local, which is what Canvas means.
    return stamp.astimezone()


def split_summary(summary: str) -> tuple[str, str | None]:
    """'Assignment 1 [CAB444_26se2]' -> ('Assignment 1', 'CAB444_26se2')."""
    match = TRAILING_UNIT.search(summary or "")
    if not match:
        return (summary or "").strip(), None
    return TRAILING_UNIT.sub("", summary).strip(), match.group(1).strip()


def kind_of(event: dict) -> str:
    uid = (event.get("UID", {}).get("value") or "").lower()
    url = (event.get("URL", {}).get("value") or "").lower()
    if "assignment" in uid or "/assignments/" in url:
        return "assignment"
    if "/quizzes/" in url:
        return "quiz"
    return "event"


def normalise_feed_url(url: str) -> str:
    """Accept webcal:// and the copy-paste variants Canvas hands out."""
    url = (url or "").strip().strip("<>").strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    elif url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url


def download(feed_url: str, timeout: int = 25) -> str:
    request = urllib.request.Request(
        normalise_feed_url(feed_url),
        headers={"User-Agent": USER_AGENT, "Accept": "text/calendar"})
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=ssl_context()) as response:
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise CanvasError(
                "Canvas would not serve that calendar feed (HTTP %d). The "
                "link may have been reset — copy it again from Calendar." % exc.code
            ) from exc
        raise CanvasError("Calendar feed returned HTTP %d." % exc.code) from exc
    except urllib.error.URLError as exc:
        if "CERTIFICATE_VERIFY" in str(exc.reason):
            raise CanvasError(
                "This Python has no certificate authorities installed, so it "
                "cannot verify Canvas's HTTPS certificate. Re-run ./install.sh "
                "— it installs them.") from exc
        raise CanvasError("Could not reach Canvas: %s" % exc.reason) from exc
    except TimeoutError as exc:
        raise CanvasError("The calendar feed timed out.") from exc

    if "BEGIN:VCALENDAR" not in text.upper():
        raise CanvasError(
            "That link did not return a calendar. Make sure you copied the "
            "'Calendar Feed' link from Canvas, not the Canvas web address.")
    return text
