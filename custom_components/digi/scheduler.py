"""Domain-level round-robin scheduler for Digi accounts.

All Digi coordinators are driven by a single timer instead of each polling on
its own interval. On every tick exactly one account is refreshed, cycling
through them, so accounts are updated serially and spaced out:

    user1 → gap → user2 → gap → user1 → …

The gap is the smallest configured update interval across the accounts, and a
shared lock guarantees two fetches never run at the same time (e.g. during the
initial setup of several entries).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import DEFAULT_UPDATE_INTERVAL_HOURS, DOMAIN

if TYPE_CHECKING:
    from .coordinator import DigiCoordinator

_LOGGER = logging.getLogger(__name__)

DATA_SCHEDULER = "scheduler"


class DigiScheduler:
    """Refreshes registered Digi coordinators one at a time, round-robin."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        # Shared across all accounts so only one fetch runs at any moment.
        self.lock = asyncio.Lock()
        self._coordinators: list[DigiCoordinator] = []
        self._index = 0
        self._cancel: CALLBACK_TYPE | None = None

    @property
    def empty(self) -> bool:
        return not self._coordinators

    @callback
    def register(self, coordinator: DigiCoordinator) -> None:
        if coordinator not in self._coordinators:
            self._coordinators.append(coordinator)
        self._reschedule()

    @callback
    def unregister(self, coordinator: DigiCoordinator) -> None:
        if coordinator in self._coordinators:
            self._coordinators.remove(coordinator)
        if self._index >= len(self._coordinators):
            self._index = 0
        self._reschedule()

    @callback
    def shutdown(self) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
        self._coordinators.clear()

    def _gap(self) -> timedelta:
        seconds = min(
            (c.interval_seconds for c in self._coordinators),
            default=DEFAULT_UPDATE_INTERVAL_HOURS * 3600,
        )
        return timedelta(seconds=max(seconds, 60))

    @callback
    def _reschedule(self) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
        if not self._coordinators:
            return
        gap = self._gap()
        self._cancel = async_track_time_interval(self.hass, self._tick, gap)
        _LOGGER.debug(
            "Digi scheduler: %d account(s), one refresh every %s",
            len(self._coordinators),
            gap,
        )

    async def _tick(self, _now) -> None:
        if not self._coordinators:
            return
        self._index %= len(self._coordinators)
        coordinator = self._coordinators[self._index]
        self._index += 1
        _LOGGER.debug(
            "Digi scheduler: refreshing account %d/%d",
            self._index,
            len(self._coordinators),
        )
        await coordinator.async_request_refresh()


@callback
def async_get_scheduler(hass: HomeAssistant) -> DigiScheduler:
    """Return the shared scheduler, creating it on first use."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    scheduler = domain_data.get(DATA_SCHEDULER)
    if scheduler is None:
        scheduler = DigiScheduler(hass)
        domain_data[DATA_SCHEDULER] = scheduler
    return scheduler
