"""Unit tests for the session cache + traffic aggregation (no Home Assistant)."""

from __future__ import annotations

from datetime import date, datetime

from ._loader import load_store

store = load_store()

GIB = 1024**3


def _session(connect, disconnect, dl=0, ul=0, ip="203.0.113.7", mac="AA:BB:CC:DD:EE:FF"):
    return {
        "connect": connect,
        "disconnect": disconnect,
        "duration": None,
        "ip": ip,
        "mac": mac,
        "download_bytes": dl,
        "upload_bytes": ul,
    }


# ── merge_sessions ───────────────────────────────────────────────────────────
def test_merge_dedupes_by_connect_and_sorts_newest_first():
    existing = [_session("2026-06-01T10:00:00", "2026-06-02T10:00:00", dl=1)]
    new = [
        # same connect as existing → fresh copy wins
        _session("2026-06-01T10:00:00", "2026-06-02T10:00:00", dl=999),
        _session("2026-06-03T10:00:00", "2026-06-04T10:00:00", dl=2),
    ]
    merged = store.merge_sessions(existing, new, today=date(2026, 6, 26))
    assert [s["connect"] for s in merged] == [
        "2026-06-03T10:00:00",
        "2026-06-01T10:00:00",
    ]
    assert merged[1]["download_bytes"] == 999  # fresh copy replaced the old one


def test_merge_prunes_beyond_retention():
    old = _session("2025-01-01T10:00:00", "2025-01-02T10:00:00")
    recent = _session("2026-06-01T10:00:00", "2026-06-02T10:00:00")
    merged = store.merge_sessions(
        [old, recent], [], today=date(2026, 6, 26), retention_days=190
    )
    assert [s["connect"] for s in merged] == ["2026-06-01T10:00:00"]


# ── daily-spread aggregation ─────────────────────────────────────────────────
def test_daily_spread_splits_a_session_across_its_days():
    # 4 GB over a session spanning 4 calendar days → 1 GB per day.
    sessions = [_session("2026-06-01T00:00:00", "2026-06-04T00:00:00", dl=4 * GIB)]
    daily = store.daily_traffic(sessions)
    assert set(daily) == {
        "2026-06-01",
        "2026-06-02",
        "2026-06-03",
        "2026-06-04",
    }
    assert all(v["download_bytes"] == GIB for v in daily.values())


def test_daily_spread_crosses_month_boundary():
    # 6 GB over May 29 → Jun 1 (4 days, 1.5 GB/day): 3 days in May, 1 in Jun.
    sessions = [_session("2026-05-29T00:00:00", "2026-06-01T00:00:00", dl=6 * GIB)]
    daily = store.daily_traffic(sessions)
    may = {d: v for d, v in daily.items() if d.startswith("2026-05")}
    jun = {d: v for d, v in daily.items() if d.startswith("2026-06")}
    assert len(may) == 3 and len(jun) == 1
    total = sum(v["download_bytes"] for v in daily.values())
    assert abs(total - 6 * GIB) <= 2  # whole amount preserved across the months


# ── derive_status ────────────────────────────────────────────────────────────
def test_derive_status_uses_last_disconnect_as_connected_since():
    sessions = [
        _session("2026-06-13T16:29:12", "2026-06-22T20:27:37"),
        _session("2026-06-09T13:30:58", "2026-06-13T16:29:12"),
    ]
    status = store.derive_status(sessions, now=datetime(2026, 6, 26, 20, 27, 37))
    assert status["status"] == "online"
    # The ongoing (unlogged) session is assumed to start at the last disconnect.
    assert status["connected_since"] == "2026-06-22T20:27:37"
    assert status["uptime_seconds"] == 4 * 24 * 3600  # exactly 4 days
    assert status["reconnects"] == 2
    assert status["current_ip"] == "203.0.113.7"


def test_derive_status_empty_is_unknown():
    status = store.derive_status([], now=datetime(2026, 6, 26, 12, 0, 0))
    assert status["status"] == "unknown"
    assert status["connected_since"] is None
    assert status["uptime_seconds"] is None
    assert status["reconnects"] == 0
