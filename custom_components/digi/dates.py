"""Parsing for the date formats Digi's pages use.

Digi writes dates as ``DD-MM-YYYY`` but is inconsistent about the separator, so
``30.06.2026`` and ``30/06/2026`` both occur. This was previously implemented
three times — in the API client, the coordinator and the sensor platform — each
with its own copy of the separator normalisation, which meant three places to
fix any parsing bug.

Deliberately free of Home Assistant imports: the API client is exercised by the
standalone dev probe, which runs without Home Assistant installed. Attaching a
timezone is therefore left to the caller that has ``dt_util`` available.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

__all__ = ["parse_date", "sort_key"]


def parse_date(value: Any) -> date | None:
    """Parse a Digi date, returning None for anything unrecognised.

    Accepts ``-``, ``.`` or ``/`` as the separator and ignores surrounding
    whitespace. Returns None rather than raising: these values come from scraped
    HTML, so an unexpected shape is normal input, not an error.
    """
    if not value:
        return None
    text = str(value).strip().replace(".", "-").replace("/", "-")
    parts = text.split("-")
    if len(parts) != 3:
        return None
    try:
        day, month, year = (int(part) for part in parts)
        return date(year, month, day)
    except ValueError:
        return None


def sort_key(value: Any) -> datetime:
    """Parse for ordering, placing unparseable values first.

    Sorting must never raise on scraped input, so an unreadable date sorts as
    the earliest possible moment instead of interrupting the sort.
    """
    parsed = parse_date(value)
    return datetime(parsed.year, parsed.month, parsed.day) if parsed else datetime.min
