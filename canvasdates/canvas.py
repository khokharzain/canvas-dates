"""A small read-only Canvas LMS API client (standard library only)."""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "canvas-dates/1.0 (personal due-date menu bar app)"
MAX_PAGES = 30


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


_NEXT_LINK = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class CanvasError(RuntimeError):
    """Any failure talking to Canvas."""


class AuthError(CanvasError):
    """The token was rejected — it is wrong, expired, or revoked."""


def _next_link(header: str) -> str | None:
    m = _NEXT_LINK.search(header or "")
    return m.group(1) if m else None


class CanvasClient:
    def __init__(self, base_url: str, token: str, timeout: int = 25):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # -- plumbing ---------------------------------------------------------

    def _url(self, path: str, params: list[tuple[str, str]] | None) -> str:
        if path.startswith("http"):
            return path
        url = f"{self.base}/api/v1/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    def get(self, path: str, params: list[tuple[str, str]] | None = None):
        """GET a Canvas endpoint, following pagination for list responses."""
        url = self._url(path, params)
        results: list = []
        pages = 0

        while url and pages < MAX_PAGES:
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            })
            try:
                with urllib.request.urlopen(
                        req, timeout=self.timeout,
                        context=ssl_context()) as resp:
                    body = resp.read().decode("utf-8", "replace")
                    link = resp.headers.get("Link", "")
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise AuthError(
                        "Canvas rejected your API token (HTTP %d). "
                        "It may have expired — re-run setup." % exc.code
                    ) from exc
                if exc.code == 404:
                    return []
                raise CanvasError(
                    "Canvas returned HTTP %d for %s" % (exc.code, path)
                ) from exc
            except urllib.error.URLError as exc:
                if "CERTIFICATE_VERIFY" in str(exc.reason):
                    raise CanvasError(
                        "This Python has no certificate authorities "
                        "installed. Re-run ./install.sh — it installs them."
                    ) from exc
                raise CanvasError(
                    "Could not reach Canvas: %s" % exc.reason) from exc
            except TimeoutError as exc:
                raise CanvasError("Canvas timed out.") from exc

            try:
                data = json.loads(body) if body.strip() else []
            except ValueError as exc:
                raise CanvasError(
                    "Canvas sent a response that was not JSON — is the "
                    "Canvas address correct?"
                ) from exc

            # A single object (e.g. /users/self) is never paginated.
            if isinstance(data, dict):
                return data

            results.extend(data)
            url = _next_link(link)
            pages += 1

        return results

    # -- endpoints --------------------------------------------------------

    def whoami(self) -> dict:
        return self.get("users/self")

    def active_courses(self) -> list[dict]:
        courses = self.get("courses", [
            ("enrollment_state", "active"),
            ("state[]", "available"),
            ("include[]", "term"),
            ("per_page", "100"),
        ])
        # Canvas can return stub entries for courses you cannot read.
        return [c for c in courses if isinstance(c, dict) and c.get("id")
                and not c.get("access_restricted_by_date")]

    def assignments(self, course_id: int) -> list[dict]:
        return self.get(f"courses/{course_id}/assignments", [
            ("order_by", "due_at"),
            ("include[]", "submission"),
            ("per_page", "100"),
        ])

    def assignment_groups(self, course_id: int) -> list[dict]:
        """Groups carry the weighting (e.g. 'Assessment 2 = 30%')."""
        return self.get(f"courses/{course_id}/assignment_groups",
                        [("per_page", "100")])

    def calendar_events(self, context_codes: list[str],
                        start_iso: str, end_iso: str) -> list[dict]:
        """Scheduled events (exams, prac tests, in-class assessment).

        Canvas caps context_codes at 10 per request, so chunk them.
        """
        events: list[dict] = []
        for i in range(0, len(context_codes), 10):
            chunk = context_codes[i:i + 10]
            params = [
                ("type", "event"),
                ("start_date", start_iso),
                ("end_date", end_iso),
                ("per_page", "100"),
            ] + [("context_codes[]", cc) for cc in chunk]
            events.extend(self.get("calendar_events", params))
        return events
