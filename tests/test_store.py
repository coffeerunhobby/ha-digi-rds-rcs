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


def test_monthly_spread_splits_across_month_boundary():
    # 10 GB over May 28 → Jun 2 (6 days, ~1.667 GB/day): 4 days in May, 2 in Jun.
    sessions = [_session("2026-05-28T00:00:00", "2026-06-02T00:00:00", dl=10 * GIB)]
    monthly = store.monthly_traffic(sessions)
    assert set(monthly) == {"2026-05", "2026-06"}
    total = monthly["2026-05"]["download_bytes"] + monthly["2026-06"]["download_bytes"]
    # Rounding aside, the whole 10 GB is preserved across the two months.
    assert abs(total - 10 * GIB) <= 2
    assert monthly["2026-05"]["download_bytes"] > monthly["2026-06"]["download_bytes"]


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
