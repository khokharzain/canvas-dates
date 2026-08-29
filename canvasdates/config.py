"""Settings + token storage for canvas-dates.

Settings live in ~/.config/canvas-dates/config.json (non-secret).
The Canvas API token lives in the macOS Keychain, with a chmod-600
file as a fallback if the Keychain is unavailable.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

CONFIG_DIR = Path(os.path.expanduser("~/.config/canvas-dates"))
CONFIG_PATH = CONFIG_DIR / "config.json"
CACHE_PATH = CONFIG_DIR / "cache.json"
KEYCHAIN_SERVICE = "canvas-dates"
ACCOUNT_TOKEN = "canvas-api-token"
ACCOUNT_FEED = "canvas-feed-url"

DEFAULTS = {
    # Where due dates come from. QUT blocks student API tokens, so the
    # private calendar feed is the working route; "token" stays supported
    # in case an administrator ever issues one.
    "source": "feed",
    # QUT's Canvas. Change if your institution differs.
    "base_url": "https://canvas.qut.edu.au",
    # How far ahead to look for due dates.
    "days_ahead": 90,
    # Hide assignments Canvas says you have already submitted.
    "hide_submitted": True,
    # Keep showing unsubmitted work this many days past its due date.
    "overdue_grace_days": 7,
    # Background refresh interval.
    "refresh_minutes": 30,
    # Cap the dropdown so it stays readable.
    "max_items": 25,
    # Menu bar: just the calendar icon, or the icon plus what's next.
    "bar_details": False,
    # Units you've deliberately left unweighted — due dates only, no
    # percentages and no nagging about missing ones.
    "skip_weights": [],
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_name("config.json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True))
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG_PATH)


def _secret_read(account: str, env_var: str) -> str | None:
    value = os.environ.get(env_var, "").strip()
    if value:
        return value
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        text = (CONFIG_DIR / account).read_text().strip()
        return text or None
    except OSError:
        return None


def _secret_write(account: str, value: str, label: str) -> str:
    """Store a secret. Returns 'keychain' or 'file'."""
    value = value.strip()
    if not value:
        raise ValueError("empty secret")
    try:
        subprocess.run(
            ["security", "add-generic-password", "-U",
             "-s", KEYCHAIN_SERVICE, "-a", account, "-l", label, "-w", value],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return "keychain"
    except (OSError, subprocess.SubprocessError):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path = CONFIG_DIR / account
        path.write_text(value)
        os.chmod(path, 0o600)
        return "file"


def _secret_clear(account: str) -> None:
    try:
        subprocess.run(
            ["security", "delete-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", account],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        (CONFIG_DIR / account).unlink()
    except OSError:
        pass


def get_token() -> str | None:
    return _secret_read(ACCOUNT_TOKEN, "CANVAS_TOKEN")


def set_token(token: str) -> str:
    return _secret_write(ACCOUNT_TOKEN, token, "Canvas Dates API token")


def clear_token() -> None:
    _secret_clear(ACCOUNT_TOKEN)


def get_feed() -> str | None:
    """The private calendar feed URL — a secret, so it lives in the Keychain."""
    return _secret_read(ACCOUNT_FEED, "CANVAS_FEED_URL")


def set_feed(url: str) -> str:
    return _secret_write(ACCOUNT_FEED, url, "Canvas Dates calendar feed")


def clear_feed() -> None:
    _secret_clear(ACCOUNT_FEED)


def load_cache() -> dict | None:
    try:
        return json.loads(CACHE_PATH.read_text())
    except (OSError, ValueError):
        return None


def save_cache(payload: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_name("cache.json.tmp")
    tmp.write_text(json.dumps(payload))
    os.chmod(tmp, 0o600)
    tmp.replace(CACHE_PATH)
