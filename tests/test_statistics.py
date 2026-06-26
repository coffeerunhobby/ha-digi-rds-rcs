"""Verify FiberLink traffic lands in Home Assistant long-term statistics.

Requires the Home Assistant test harness (recorder); skipped where unavailable.
"""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from homeassistant.components.recorder import get_instance  # noqa: E402
from homeassistant.components.recorder.statistics import (  # noqa: E402
    statistics_during_period,
)
from homeassistant.util import dt as dt_util  # noqa: E402

from custom_components.digi.statistics import (  # noqa: E402
    async_update_traffic_statistics,
    statistic_id,
)


async def _wait_recorder(hass):
    """Flush the recorder queue (portable across HA versions / phacc)."""
    await hass.async_block_till_done()
    await get_instance(hass).async_block_till_done()
    await hass.async_block_till_done()

GIB = 1024**3


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations():
    """Override the conftest autouse: this module drives ``hass`` through
    ``recorder_mock`` (which must initialise before ``hass``), so it must not
    pull ``enable_custom_integrations`` and set ``hass`` up first."""
    yield


def _session(connect, disconnect, dl=0, ul=0):
    return {
        "connect": connect,
        "disconnect": disconnect,
        "duration": None,
        "ip": "203.0.113.7",
        "mac": "AA:BB:CC:DD:EE:FF",
        "download_bytes": dl,
        "upload_bytes": ul,
    }


async def test_traffic_statistics_imported(recorder_mock, hass):
    # 6 GB over 2026-06-01 → 2026-06-03 (3 days, 2 GB/day, daily-spread).
    sessions = [_session("2026-06-01T00:00:00", "2026-06-03T00:00:00", dl=6 * GIB)]

    async_update_traffic_statistics(hass, "abc123", "Test Address", sessions)
    await _wait_recorder(hass)

    download_id = statistic_id("abc123", "download")
    upload_id = statistic_id("abc123", "upload")

    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.utc_from_timestamp(0),
        None,
        {download_id, upload_id},
        "day",
        None,
        {"sum", "state"},
    )

    assert download_id in stats
    assert upload_id in stats
    # Final cumulative sum is the whole 6 GB (download), 0 (upload).
    assert stats[download_id][-1]["sum"] == pytest.approx(6.0, abs=0.01)
    assert stats[upload_id][-1]["sum"] == pytest.approx(0.0, abs=0.01)
    # Daily-spread produced three daily points.
    assert len(stats[download_id]) == 3
