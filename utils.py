"""Small shared helpers: console formatting and time utilities."""
from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (e.g. 2026-08-30T12:34:56Z)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_int(value) -> str:
    """Format an integer with thousands separators; None becomes '-'."""
    if value is None:
        return "-"
    return f"{int(value):,}"


def fmt_float(value, digits: int = 2) -> str:
    """Format a float for display; None becomes '-'."""
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def truncate(text: str, width: int = 40) -> str:
    """Shorten a string for tidy console columns."""
    text = text or ""
    return text if len(text) <= width else text[: width - 1] + "…"


def section(title: str, width: int = 40) -> str:
    """A '=' framed section header, e.g. for the final scan summary."""
    line = "=" * width
    return f"\n{line}\n{title}\n{line}\n"
