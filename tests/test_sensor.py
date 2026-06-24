"""Tests for the per-address device layout and entity ids."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from custom_components.digi.const import DOMAIN  # noqa: E402
from custom_components.digi.sensor import (  # noqa: E402
    ADDRESS_SENSORS,
    DigiAddressSensor,
)

HASH = "ab12cd34ef56"


def _entry(entry_id: str) -> SimpleNamespace:
    return SimpleNamespace(entry_id=entry_id, data={"username": "user@example.com"})


def _coordinator() -> SimpleNamespace:
    return SimpleNamespace(
        data={
            "addresses": [
                {
                    "address_unique": HASH,
                    "address": "Strada A",
                    "service_label": "Internet",
                    "rest": 12.0,
                    "amount": 30.0,
                    "due_date": "30-06-2026",
                    "has_arrears": True,
                    "services_count": 2,
                    "latest": {},
                    "history": [],
                }
            ]
        },
        last_update_success=True,
    )


def _sensor(entry_id: str, key: str) -> DigiAddressSensor:
    description = next(d for d in ADDRESS_SENSORS if d.key == key)
    return DigiAddressSensor(_coordinator(), _entry(entry_id), HASH, description)


def test_address_is_one_device_with_all_sensors():
    sensors = [_sensor("entry_one", d.key) for d in ADDRESS_SENSORS]
    # Every sensor for an address shares one device, named by the address.
    device_ids = {next(iter(s.device_info["identifiers"])) for s in sensors}
    assert device_ids == {(DOMAIN, "entry_one_" + HASH)}
    assert sensors[0].device_info["name"] == "Strada A"
    assert len(ADDRESS_SENSORS) == 5


def test_entity_id_uses_hash_not_address():
    sensor = _sensor("entry_one", "amount_due")
    assert "strada" not in sensor.entity_id.lower()
    assert sensor.entity_id == f"sensor.digi_entry_on_{HASH}_amount_due"
    assert sensor.unique_id == f"entry_one_{HASH}_amount_due"


def test_entity_id_prefers_client_code():
    description = next(d for d in ADDRESS_SENSORS if d.key == "amount_due")
    entry = SimpleNamespace(
        entry_id="entry_one",
        data={"username": "user@example.com", "client_code": "123456"},
    )
    sensor = DigiAddressSensor(_coordinator(), entry, HASH, description)
    assert sensor.entity_id == f"sensor.digi_123456_{HASH}_amount_due"


def test_ids_are_scoped_per_entry():
    a1 = _sensor("entry_one", "amount_due")
    a2 = _sensor("entry_two", "amount_due")
    assert a1.unique_id != a2.unique_id
    d1 = next(iter(a1.device_info["identifiers"]))
    d2 = next(iter(a2.device_info["identifiers"]))
    assert d1 != d2


def test_sensor_values_and_attributes():
    assert _sensor("entry_one", "amount_due").native_value == 12.0
    assert _sensor("entry_one", "last_invoice").native_value == 30.0
    assert _sensor("entry_one", "due_date").native_value == "30-06-2026"
    assert _sensor("entry_one", "overdue").native_value == "yes"
    assert _sensor("entry_one", "number_of_services").native_value == 2

    # Only the amount-due sensor carries the rich attributes.
    attrs = _sensor("entry_one", "amount_due").extra_state_attributes
    assert attrs["address"] == "Strada A"
    assert _sensor("entry_one", "due_date").extra_state_attributes is None
