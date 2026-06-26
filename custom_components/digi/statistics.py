"""Feed daily-spread TX/RX into Home Assistant long-term statistics.

Home Assistant keeps long-term statistics indefinitely, so rather than maintain
our own monthly aggregates we hand it daily download/upload totals (spread across
each session's days) as *external statistics*. The built-in Statistics Graph card
then renders hourly/daily/weekly/monthly TX/RX bars natively — no custom card,
and history spanning the cached ~6 months is backfilled with correct timestamps.

External statistics are keyed by the address-unique id (never the IP, which churns
on PPPoE), so each address is one stable endpoint.
"""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .store import Session, daily_traffic

_LOGGER = logging.getLogger(__name__)

_GIB = 1024**3

# (direction, key into a daily bucket) for the two statistics per address.
_DIRECTIONS: tuple[tuple[str, str], ...] = (
    ("download", "download_bytes"),
    ("upload", "upload_bytes"),
)


def statistic_id(address_unique: str, direction: str) -> str:
    """External statistic id, e.g. ``digi:connection_download_<unique>``."""
    return f"{DOMAIN}:connection_{direction}_{address_unique}"


def _statistic_metadata(stat_id: str, name: str) -> StatisticMetaData:
    """Build sum-only statistic metadata, robust across HA versions.

    Newer Home Assistant replaced ``has_mean`` with a ``mean_type`` enum; older
    releases (down to the 2024.12 we support) only know ``has_mean``. Provide
    whichever the running version expects so the same code works on both.
    """
    metadata: StatisticMetaData = {
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": stat_id,
        "unit_of_measurement": UnitOfInformation.GIBIBYTES,
    }
    try:
        from homeassistant.components.recorder.models import StatisticMeanType

        metadata["mean_type"] = StatisticMeanType.NONE
    except ImportError:
        metadata["has_mean"] = False
    return metadata


def async_update_traffic_statistics(
    hass: HomeAssistant,
    address_unique: str,
    address_name: str,
    sessions: list[Session],
) -> None:
    """Re-import the cached sessions as daily download/upload statistics.

    The full retained window is re-sent each time; ``async_add_external_statistics``
    updates overlapping points, so late-arriving days (a long session that only
    completes weeks later) backfill cleanly. The cumulative ``sum`` is rebuilt
    from the oldest cached day on every call, which keeps the per-period deltas
    the card shows correct.

    Caveat: once a day ages out of the ~6-month cache the baseline shifts, so the
    single period straddling the cache's leading edge can read off. That only
    affects data older than the retention window, which is beyond what the graph
    is meant to show.
    """
    daily = daily_traffic(sessions)
    if not daily:
        return

    days = sorted(daily)
    for direction, key in _DIRECTIONS:
        metadata = _statistic_metadata(
            statistic_id(address_unique, direction),
            f"Digi {address_name} {direction}",
        )
        cumulative = 0.0
        points: list[StatisticData] = []
        for day in days:
            cumulative += daily[day][key] / _GIB
            start = dt_util.start_of_local_day(datetime.fromisoformat(day))
            points.append(StatisticData(start=start, state=cumulative, sum=cumulative))

        try:
            async_add_external_statistics(hass, metadata, points)
        except Exception:  # noqa: BLE001 — recorder may be disabled/unavailable
            _LOGGER.debug(
                "Could not import Digi traffic statistics for %s", direction,
                exc_info=True,
            )
            return
