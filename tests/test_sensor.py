"""Tests for the per-address device layout and entity ids."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from custom_components.digi.const import DOMAIN  # noqa: E402
from custom_components.digi.sensor import (  # noqa: E402
    ADDRESS_SENSORS,
    TRAFFIC_SENSORS,
    DigiAddressSensor,
    DigiConnectionStatusSensor,
    DigiConnectionUptimeSensor,
    DigiInternetSensor,
    DigiTrafficSensor,
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


def test_internet_sensor():
    coordinator = SimpleNamespace(
        data={
            "addresses": [
                {
                    "address_unique": HASH,
                    "address": "Strada A",
                    "internet": {
                        "ipv4": "203.0.113.7",
                        "ipv6": ["2001:db8::/64"],
                        "plan": "Plan X",
                    },
                }
            ]
        },
        last_update_success=True,
    )
    sensor = DigiInternetSensor(coordinator, _entry("entry_one"), HASH)
    assert sensor.native_value == "203.0.113.7"
    assert sensor.entity_id == f"sensor.digi_entry_on_{HASH}_public_ip"
    assert sensor.entity_registry_enabled_default is False
    attrs = sensor.extra_state_attributes
    assert attrs["plan"] == "Plan X"
    assert attrs["ipv6"] == ["2001:db8::/64"]
    assert "account" not in attrs


def _connection_coordinator() -> SimpleNamespace:
    return SimpleNamespace(
        data={
            "addresses": [
                {
                    "address_unique": HASH,
                    "address": "Strada A",
                    "connection": {
                        "status": "online",
                        "connected_since": "2026-06-22T20:27:37",
                        "last_connect": "2026-06-13T16:29:12",
                        "last_disconnect": "2026-06-22T20:27:37",
                        "last_duration": "219h:58m:25s",
                        "reconnects": 5,
                        "uptime_seconds": 315000,
                        "current_ip": "203.0.113.7",
                        "current_mac": "AA:BB:CC:DD:EE:FF",
                        "month_key": "2026-06",
                        "download_bytes_month": 30 * 1024**3,
                        "upload_bytes_month": 12 * 1024**3,
                        "monthly": {
                            "2026-05": {
                                "download_bytes": 10 * 1024**3,
                                "upload_bytes": 4 * 1024**3,
                            },
                            "2026-06": {
                                "download_bytes": 30 * 1024**3,
                                "upload_bytes": 12 * 1024**3,
                            },
                        },
                        "sessions": [],
                    },
                }
            ]
        },
        last_update_success=True,
    )


def test_connection_status_sensor():
    coordinator = _connection_coordinator()
    sensor = DigiConnectionStatusSensor(coordinator, _entry("entry_one"), HASH)
    assert sensor.native_value == "online"
    assert sensor.entity_id == f"sensor.digi_entry_on_{HASH}_connection_status"
    assert sensor.entity_registry_enabled_default is False
    attrs = sensor.extra_state_attributes
    assert attrs["reconnects_30d"] == 5
    assert attrs["current_ip"] == "203.0.113.7"
    assert attrs["monthly_download_gib"] == {"2026-05": 10.0, "2026-06": 30.0}


def test_connection_uptime_sensor_is_timezone_aware():
    coordinator = _connection_coordinator()
    sensor = DigiConnectionUptimeSensor(coordinator, _entry("entry_one"), HASH)
    value = sensor.native_value
    assert value is not None
    assert value.tzinfo is not None  # HA timestamp sensors must be tz-aware
    assert value.year == 2026 and value.month == 6 and value.day == 22


def test_traffic_sensors_report_gib():
    coordinator = _connection_coordinator()
    download = next(d for d in TRAFFIC_SENSORS if d.key == "data_downloaded")
    sensor = DigiTrafficSensor(coordinator, _entry("entry_one"), HASH, download)
    assert sensor.native_value == 30.0  # 30 GiB this month
    assert sensor.entity_id == f"sensor.digi_entry_on_{HASH}_data_downloaded"
    assert sensor.extra_state_attributes["monthly_gib"] == {
        "2026-05": 10.0,
        "2026-06": 30.0,
    }
