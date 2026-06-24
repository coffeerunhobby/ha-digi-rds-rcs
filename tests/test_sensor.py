"""Tests for entity/device uniqueness across multiple Digi accounts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from custom_components.digi.const import DOMAIN  # noqa: E402
from custom_components.digi.sensor import (  # noqa: E402
    SERVICE_SENSORS,
    TOTALS_SENSORS,
    DigiServiceSensor,
    DigiTotalsSensor,
)


def _coordinator(services: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(data={"services": services}, last_update_success=True)


def _service_payload() -> list[dict]:
    # Two different accounts can legitimately have the same address + service
    # text; the ids must still not collide.
    return [
        {
            "account_unique": "digi_strada_a_internet",
            "address": "Strada A",
            "service_label": "Internet",
        }
    ]


def test_service_sensor_ids_are_scoped_per_entry():
    desc = SERVICE_SENSORS[0]
    s1 = DigiServiceSensor(
        _coordinator(_service_payload()),
        SimpleNamespace(entry_id="entry_one"),
        "digi_strada_a_internet",
        desc,
    )
    s2 = DigiServiceSensor(
        _coordinator(_service_payload()),
        SimpleNamespace(entry_id="entry_two"),
        "digi_strada_a_internet",
        desc,
    )

    # Same logical account_unique, different config entries → distinct ids.
    assert s1.unique_id == "entry_one_digi_strada_a_internet_de_plata"
    assert s2.unique_id == "entry_two_digi_strada_a_internet_de_plata"
    assert s1.unique_id != s2.unique_id

    id1 = next(iter(s1.device_info["identifiers"]))
    id2 = next(iter(s2.device_info["identifiers"]))
    assert id1 == (DOMAIN, "entry_one_digi_strada_a_internet")
    assert id1 != id2


def test_totals_sensor_ids_are_scoped_per_entry():
    desc = TOTALS_SENSORS[0]
    t1 = DigiTotalsSensor(
        _coordinator([]), SimpleNamespace(entry_id="entry_one"), desc
    )
    t2 = DigiTotalsSensor(
        _coordinator([]), SimpleNamespace(entry_id="entry_two"), desc
    )

    assert t1.unique_id != t2.unique_id
    id1 = next(iter(t1.device_info["identifiers"]))
    id2 = next(iter(t2.device_info["identifiers"]))
    assert id1 != id2
