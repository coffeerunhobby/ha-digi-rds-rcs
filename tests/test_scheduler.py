"""Tests for the round-robin Digi scheduler."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")

from custom_components.digi.scheduler import DigiScheduler  # noqa: E402


def _coordinator(interval_seconds: int = 3600) -> SimpleNamespace:
    return SimpleNamespace(
        interval_seconds=interval_seconds,
        async_request_refresh=AsyncMock(),
    )


async def test_round_robin_cycles_accounts():
    scheduler = DigiScheduler.__new__(DigiScheduler)  # skip __init__ (no hass)
    c1, c2 = _coordinator(), _coordinator()
    scheduler._coordinators = [c1, c2]
    scheduler._index = 0

    await scheduler._tick(None)  # c1
    await scheduler._tick(None)  # c2
    await scheduler._tick(None)  # c1 again

    assert c1.async_request_refresh.await_count == 2
    assert c2.async_request_refresh.await_count == 1


async def test_tick_with_no_accounts_is_noop():
    scheduler = DigiScheduler.__new__(DigiScheduler)
    scheduler._coordinators = []
    scheduler._index = 0
    await scheduler._tick(None)  # must not raise


def test_gap_uses_smallest_interval():
    scheduler = DigiScheduler.__new__(DigiScheduler)
    scheduler._coordinators = [_coordinator(7200), _coordinator(3600)]
    assert scheduler._gap().total_seconds() == 3600


def test_gap_has_a_floor():
    scheduler = DigiScheduler.__new__(DigiScheduler)
    scheduler._coordinators = [_coordinator(1)]
    assert scheduler._gap().total_seconds() == 60
