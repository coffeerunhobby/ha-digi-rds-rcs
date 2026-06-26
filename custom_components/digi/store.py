"""Persistent FiberLink connection-session cache and traffic aggregation.

Digi's connection-logs endpoint only returns the date range we ask for, so each
poll fetches just the last ``LOGS_WINDOW_DAYS`` days. Those sessions are merged
into a small per-entry on-disk cache (keyed by the address' unique id) that is
kept for ``LOGS_RETENTION_DAYS`` (~6 months). Monthly/daily traffic graphs are
computed from the cached sessions, so the longer history is available without
re-querying the site.

The aggregation/merge/derive helpers are deliberately pure (no Home Assistant
imports at module load) so they can be unit-tested without a running HA; the
``Store`` is imported lazily inside :class:`DigiSessionStore`.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .const import (
    LOGS_RETENTION_DAYS,
    LOGS_WINDOW_DAYS,
    STORAGE_KEY_SESSIONS,
    STORAGE_VERSION,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# A session is stored as a plain JSON-friendly dict with the keys produced by
# dataclasses.asdict(ConnectionSession): connect, disconnect, duration, ip,
# mac, download_bytes, upload_bytes.
Session = dict[str, Any]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def merge_sessions(
    existing: list[Session],
    new: list[Session],
    *,
    today: date,
    retention_days: int = LOGS_RETENTION_DAYS,
) -> list[Session]:
    """Merge freshly fetched sessions into the cache.

    Sessions are de-duplicated by their ``connect`` timestamp (a stable unique
    key — a completed session is immutable once logged); on conflict the freshly
    fetched copy wins. Sessions whose most recent boundary is older than the
    retention cutoff are pruned. The result is sorted newest-first.
    """
    by_connect: dict[str, Session] = {}
    for session in (*existing, *new):
        key = session.get("connect")
        if key:
            by_connect[key] = session

    cutoff = today - timedelta(days=retention_days)
    kept: list[Session] = []
    for session in by_connect.values():
        boundary = _parse_iso(session.get("disconnect") or session.get("connect"))
        if boundary is None or boundary.date() >= cutoff:
            kept.append(session)

    kept.sort(key=lambda s: s.get("connect") or "", reverse=True)
    return kept


def _daily_traffic(
    sessions: list[Session],
) -> tuple[dict[date, float], dict[date, float]]:
    """Spread each session's bytes evenly across the days it spans (option 2).

    A session that spans month/day boundaries (e.g. a 77-day session carrying
    1.7 TB) is divided by its day count so each day — and therefore each month —
    gets a proportional share, instead of dumping the whole total on the
    disconnect day.
    """
    download: dict[date, float] = defaultdict(float)
    upload: dict[date, float] = defaultdict(float)
    for session in sessions:
        start = _parse_iso(session.get("connect"))
        end = _parse_iso(session.get("disconnect"))
        if start is None or end is None:
            continue
        first, last = start.date(), end.date()
        days = (last - first).days + 1
        if days <= 0:
            continue
        per_day_dl = (session.get("download_bytes") or 0) / days
        per_day_ul = (session.get("upload_bytes") or 0) / days
        current = first
        while current <= last:
            download[current] += per_day_dl
            upload[current] += per_day_ul
            current += timedelta(days=1)
    return download, upload


def daily_traffic(sessions: list[Session]) -> dict[str, dict[str, int]]:
    """Per-day {YYYY-MM-DD: {download_bytes, upload_bytes}} (daily-spread)."""
    download, upload = _daily_traffic(sessions)
    days = sorted(set(download) | set(upload))
    return {
        day.isoformat(): {
            "download_bytes": int(round(download.get(day, 0.0))),
            "upload_bytes": int(round(upload.get(day, 0.0))),
        }
        for day in days
    }


def monthly_traffic(sessions: list[Session]) -> dict[str, dict[str, int]]:
    """Per-month {YYYY-MM: {download_bytes, upload_bytes}} (daily-spread)."""
    download, upload = _daily_traffic(sessions)
    months_dl: dict[str, float] = defaultdict(float)
    months_ul: dict[str, float] = defaultdict(float)
    for day, value in download.items():
        months_dl[day.strftime("%Y-%m")] += value
    for day, value in upload.items():
        months_ul[day.strftime("%Y-%m")] += value
    months = sorted(set(months_dl) | set(months_ul))
    return {
        month: {
            "download_bytes": int(round(months_dl.get(month, 0.0))),
            "upload_bytes": int(round(months_ul.get(month, 0.0))),
        }
        for month in months
    }


def derive_status(
    sessions: list[Session],
    *,
    now: datetime,
    window_days: int = LOGS_WINDOW_DAYS,
) -> dict[str, Any]:
    """Best-effort connection status / uptime from the session history.

    The log lists only *completed* sessions, so the currently-open session is
    never shown. FiberLink reconnects automatically, so we treat the most recent
    disconnect as the start of the ongoing session: ``connected_since`` is that
    timestamp and ``uptime_seconds`` is measured from it. ``status`` is a
    best-effort "online" whenever any history exists, "unknown" otherwise.
    """
    if not sessions:
        return {
            "status": "unknown",
            "connected_since": None,
            "last_connect": None,
            "last_disconnect": None,
            "last_duration": None,
            "reconnects": 0,
            "uptime_seconds": None,
            "current_ip": None,
            "current_mac": None,
        }

    latest = sessions[0]  # newest connect (sessions are sorted newest-first)
    connected_since = latest.get("disconnect") or latest.get("connect")
    since_dt = _parse_iso(connected_since)
    uptime = int((now - since_dt).total_seconds()) if since_dt else None
    if uptime is not None and uptime < 0:
        uptime = 0

    cutoff = now - timedelta(days=window_days)
    reconnects = sum(
        1
        for session in sessions
        if (started := _parse_iso(session.get("connect"))) and started >= cutoff
    )

    return {
        "status": "online",
        "connected_since": connected_since,
        "last_connect": latest.get("connect"),
        "last_disconnect": latest.get("disconnect"),
        "last_duration": latest.get("duration"),
        "reconnects": reconnects,
        "uptime_seconds": uptime,
        "current_ip": latest.get("ip"),
        "current_mac": latest.get("mac"),
    }


class DigiSessionStore:
    """On-disk cache of connection sessions, one bucket per address-unique id."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        # Lazy import keeps this module importable without Home Assistant for
        # the pure-logic unit tests above.
        from homeassistant.helpers.storage import Store

        self._store: Store[dict[str, list[Session]]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_SESSIONS}_{entry_id}"
        )
        self._data: dict[str, list[Session]] = {}
        self._loaded = False

    async def async_load(self) -> None:
        if not self._loaded:
            self._data = await self._store.async_load() or {}
            self._loaded = True

    def sessions(self, address_unique: str) -> list[Session]:
        return self._data.get(address_unique, [])

    def update(
        self, address_unique: str, new_sessions: list[Session], *, today: date
    ) -> list[Session]:
        """Merge new sessions for an address and return the full kept history."""
        merged = merge_sessions(
            self._data.get(address_unique, []), new_sessions, today=today
        )
        self._data[address_unique] = merged
        return merged

    async def async_save(self) -> None:
        await self._store.async_save(self._data)

    async def async_remove(self) -> None:
        await self._store.async_remove()
