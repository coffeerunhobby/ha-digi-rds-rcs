"""Tests for entity/device layout across multiple Digi accounts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from custom_components.digi.const import DOMAIN  # noqa: E402
from custom_components.digi.sensor import (  # noqa: E402
    TOTALS_SENSORS,
    DigiAddressSensor,
    DigiTotalsSensor,
)


def _entry(entry_id: str) -> SimpleNamespace:
    return SimpleNamespace(entry_id=entry_id, data={"username": "user@example.com"})


def _coordinator(addresses: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        data={"addresses": addresses, "account_label": "Digi account"},
        last_update_success=True,
    )


def _address_payload() -> list[dict]:
    # Two different accounts can legitimately have the same address text; the
    # ids must still not collide.
    return [
        {
            "address_unique": "ab12cd34ef56",
            "address": "Strada A",
            "service_label": "Internet",
            "rest": 12.0,
            "latest": {},
            "history": [],
        }
    ]


def test_account_is_a_single_device():
    # Totals and address sensors must all live on the same per-entry device.
    entry = _entry("entry_one")
    totals = DigiTotalsSensor(
        _coordinator(_address_payload()), entry, TOTALS_SENSORS[0]
    )
    address = DigiAddressSensor(
        _coordinator(_address_payload()), entry, "ab12cd34ef56"
    )
    totals_device = next(iter(totals.device_info["identifiers"]))
    address_device = next(iter(address.device_info["identifiers"]))
    assert totals_device == (DOMAIN, "entry_one")
    assert totals_device == address_device


def test_device_is_named_by_email():
    totals = DigiTotalsSensor(
        _coordinator(_address_payload()), _entry("entry_one"), TOTALS_SENSORS[0]
    )
    assert totals.device_info["name"] == "Digi · user@example.com"


def test_ids_are_scoped_per_entry_and_address_free():
    a1 = DigiAddressSensor(
        _coordinator(_address_payload()), _entry("entry_one"), "ab12cd34ef56"
    )
    a2 = DigiAddressSensor(
        _coordinator(_address_payload()), _entry("entry_two"), "ab12cd34ef56"
    )
    assert a1.unique_id == "entry_one_ab12cd34ef56"
    assert a2.unique_id == "entry_two_ab12cd34ef56"
    assert a1.unique_id != a2.unique_id

    # The entity_id must use the hash, never the address text.
    assert "strada" not in a1.entity_id.lower()
    assert a1.entity_id == "sensor.digi_entry_on_ab12cd34ef56"

    d1 = next(iter(a1.device_info["identifiers"]))
    d2 = next(iter(a2.device_info["identifiers"]))
    assert d1 == (DOMAIN, "entry_one")
    assert d1 != d2


def test_address_sensor_value_and_name():
    sensor = DigiAddressSensor(
        _coordinator(_address_payload()), _entry("entry_one"), "ab12cd34ef56"
    )
    # The readable address remains the display name and an attribute.
    assert sensor.name == "Strada A"
    assert sensor.native_value == 12.0
    assert sensor.extra_state_attributes["address"] == "Strada A"
