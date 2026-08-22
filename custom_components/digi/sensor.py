"""Sensor platform for the Digi (RCS & RDS) integration.

Layout mirrors the "account → devices" model: the config entry is the Digi
account (titled by e-mail), and each address is its own device named by the
address, with a handful of sensors.

The device/entity ids use a short hash of the address (never the address text),
so the address is not exposed in entity_ids; it is the device name and a sensor
attribute instead.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    CURRENCY_RON,
)
from .coordinator import DigiConfigEntry, DigiCoordinator
from .dates import parse_date
from .entity import DigiAddressEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class DigiAddressDescription(SensorEntityDescription):
    """Describes a sensor on an address device."""

    value_fn: Callable[[dict[str, Any]], Any]
    with_attributes: bool = False
    # Optional per-state icon, e.g. to flag an overdue due date.
    icon_fn: Callable[[dict[str, Any]], str | None] | None = None


def _parse_due_datetime(value: Any) -> datetime | None:
    """Digi's due date as a timezone-aware datetime, or None.

    Reported as a ``timestamp`` sensor so Home Assistant renders it as
    "in 3 days" / "5 days ago", which makes an overdue invoice obvious without
    any frontend styling. Parsing is shared (see dates.py); only attaching the
    local timezone is specific to the entity layer.
    """
    parsed = parse_date(value)
    if parsed is None:
        return None
    return datetime(
        parsed.year, parsed.month, parsed.day, tzinfo=dt_util.DEFAULT_TIME_ZONE
    )


def _invoice_attributes(address: dict[str, Any]) -> dict[str, Any]:
    latest = address.get("latest") or {}
    return {
        "address": address.get("address"),
        "services": address.get("service_label"),
        "invoice_number": address.get("invoice_number"),
        "issue_date": address.get("issue_date"),
        "due_date": address.get("due_date"),
        "status": address.get("status"),
        "invoice_amount": address.get("amount"),
        "overdue": "yes" if address.get("has_arrears") else "no",
        "unpaid_invoices": address.get("unpaid_count"),
        "services_count": address.get("services_count"),
        "pdf_url": latest.get("pdf_url"),
        "services_breakdown": address.get("services") or [],
        "history": [
            {
                "invoice_number": item.get("invoice_number"),
                "issue_date": item.get("issue_date"),
                "due_date": item.get("due_date"),
                "amount": item.get("amount"),
                "remaining": item.get("rest"),
                "status": item.get("status"),
            }
            for item in (address.get("history") or [])
        ],
    }


ADDRESS_SENSORS: tuple[DigiAddressDescription, ...] = (
    DigiAddressDescription(
        key="amount_due",
        translation_key="amount_due",
        icon="mdi:cash-multiple",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_RON,
        value_fn=lambda a: a.get("rest"),
        with_attributes=True,
    ),
    DigiAddressDescription(
        key="last_invoice",
        translation_key="last_invoice",
        icon="mdi:file-document-outline",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_RON,
        value_fn=lambda a: a.get("amount"),
    ),
    DigiAddressDescription(
        key="due_date",
        translation_key="due_date",
        icon="mdi:calendar-clock",
        # Digi's own DD-MM-YYYY text, for the next payment deadline (the
        # earliest unpaid invoice); empty once nothing is owed. See
        # `due_date_timestamp` for the machine-readable form.
        value_fn=lambda a: a.get("next_due_date"),
        icon_fn=lambda a: (
            "mdi:calendar-alert" if a.get("has_arrears") else "mdi:calendar-clock"
        ),
    ),
    DigiAddressDescription(
        key="due_date_timestamp",
        translation_key="due_date_timestamp",
        icon="mdi:calendar-clock",
        # A real timestamp, so Home Assistant renders it as "in 3 days" /
        # "5 days ago" — an overdue invoice becomes obvious without any
        # frontend styling, which an integration cannot control.
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda a: _parse_due_datetime(a.get("next_due_date")),
        icon_fn=lambda a: (
            "mdi:calendar-alert" if a.get("has_arrears") else "mdi:calendar-clock"
        ),
    ),
    DigiAddressDescription(
        key="overdue",
        translation_key="overdue",
        icon="mdi:alert-circle-outline",
        value_fn=lambda a: "yes" if a.get("has_arrears") else "no",
    ),
    DigiAddressDescription(
        key="number_of_services",
        translation_key="number_of_services",
        icon="mdi:counter",
        value_fn=lambda a: a.get("services_count"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: DigiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one device per address, each with its sensors."""
    coordinator = config_entry.runtime_data
    known: set[str] = set()
    known_internet: set[str] = set()
    known_connection: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        data = coordinator.data or {}
        entities: list[SensorEntity] = []
        for address in data.get("addresses", []):
            address_unique = address.get("address_unique")
            if not address_unique:
                continue
            if address_unique not in known:
                known.add(address_unique)
                entities.extend(
                    DigiAddressSensor(
                        coordinator, config_entry, address_unique, description
                    )
                    for description in ADDRESS_SENSORS
                )
            # The Public IP sensor only exists for addresses with internet, and
            # may appear on a later poll than the invoice sensors.
            if address.get("internet") and address_unique not in known_internet:
                known_internet.add(address_unique)
                entities.append(
                    DigiInternetSensor(coordinator, config_entry, address_unique)
                )
            # Connection status / uptime sensors appear once the FiberLink logs
            # have been read for an internet address. Traffic is exposed as
            # native long-term statistics (see statistics.py), not as entities.
            if address.get("connection") and address_unique not in known_connection:
                known_connection.add(address_unique)
                entities.append(
                    DigiConnectionStatusSensor(
                        coordinator, config_entry, address_unique
                    )
                )
                entities.append(
                    DigiConnectionUptimeSensor(
                        coordinator, config_entry, address_unique
                    )
                )
        if entities:
            async_add_entities(entities)

    _add_new_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class DigiAddressSensor(DigiAddressEntity, SensorEntity):
    """A sensor on an address device."""

    entity_description: DigiAddressDescription

    def __init__(
        self,
        coordinator: DigiCoordinator,
        config_entry: DigiConfigEntry,
        address_unique: str,
        description: DigiAddressDescription,
    ) -> None:
        super().__init__(coordinator, config_entry, address_unique)
        self.entity_description = description
        self._attr_unique_id = f"{self._device_id}_{description.key}"
        self.entity_id = self._build_entity_id("sensor", description.key)

    @property
    def native_value(self) -> Any:
        address = self._address
        if address is None:
            return None
        return self.entity_description.value_fn(address)

    @property
    def icon(self) -> str | None:
        """Allow the icon to reflect state (e.g. an overdue due date)."""
        address = self._address
        icon_fn = self.entity_description.icon_fn
        if address is not None and icon_fn is not None:
            return icon_fn(address)
        return super().icon

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.entity_description.with_attributes:
            return None
        address = self._address
        if address is None:
            return None
        return _invoice_attributes(address)


class DigiInternetSensor(DigiAddressEntity, SensorEntity):
    """Public IP of the internet service at an address (plan/IPv6 in attrs).

    Disabled by default: the public IP is mildly sensitive, so users opt in.
    """

    _attr_translation_key = "public_ip"
    _attr_icon = "mdi:ip-network"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: DigiCoordinator,
        config_entry: DigiConfigEntry,
        address_unique: str,
    ) -> None:
        super().__init__(coordinator, config_entry, address_unique)
        self._attr_unique_id = f"{self._device_id}_public_ip"
        self.entity_id = self._build_entity_id("sensor", "public_ip")

    @property
    def _internet(self) -> dict[str, Any] | None:
        address = self._address
        return address.get("internet") if address else None

    @property
    def available(self) -> bool:
        # Stricter than the base: this entity only exists while the address
        # actually reports an internet service.
        return super().available and self._internet is not None

    @property
    def native_value(self) -> Any:
        internet = self._internet
        return internet.get("ipv4") if internet else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        internet = self._internet
        if internet is None:
            return None
        return {
            "ipv6": internet.get("ipv6") or [],
            "plan": internet.get("plan"),
        }


class _DigiConnectionEntity(DigiAddressEntity, SensorEntity):
    """Base for the opt-in FiberLink connection sensors on an address device.

    Disabled by default: connection logs expose the line's IP/MAC and traffic,
    so users opt in.
    """

    _attr_entity_registry_enabled_default = False

    @property
    def _connection(self) -> dict[str, Any] | None:
        address = self._address
        return address.get("connection") if address else None

    @property
    def available(self) -> bool:
        # Stricter than the base: these appear only once the FiberLink logs
        # have been read for the address.
        return super().available and self._connection is not None


class DigiConnectionStatusSensor(_DigiConnectionEntity):
    """Best-effort connection status with uptime / stability attributes."""

    _attr_translation_key = "connection_status"
    _attr_icon = "mdi:lan-connect"

    def __init__(
        self,
        coordinator: DigiCoordinator,
        config_entry: DigiConfigEntry,
        address_unique: str,
    ) -> None:
        super().__init__(coordinator, config_entry, address_unique)
        self._attr_unique_id = f"{self._device_id}_connection_status"
        self.entity_id = self._build_entity_id("sensor", "connection_status")

    @property
    def native_value(self) -> Any:
        connection = self._connection
        return connection.get("status") if connection else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        connection = self._connection
        if connection is None:
            return None
        return {
            "connected_since": connection.get("connected_since"),
            "last_connect": connection.get("last_connect"),
            "last_disconnect": connection.get("last_disconnect"),
            "last_duration": connection.get("last_duration"),
            "reconnects_30d": connection.get("reconnects"),
            "current_ip": connection.get("current_ip"),
            "current_mac": connection.get("current_mac"),
            "recent_sessions": connection.get("sessions"),
        }


class DigiConnectionUptimeSensor(_DigiConnectionEntity):
    """Timestamp the current session started (best-effort "connected since")."""

    _attr_translation_key = "connection_uptime"
    _attr_icon = "mdi:timer-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coordinator: DigiCoordinator,
        config_entry: DigiConfigEntry,
        address_unique: str,
    ) -> None:
        super().__init__(coordinator, config_entry, address_unique)
        self._attr_unique_id = f"{self._device_id}_connection_uptime"
        self.entity_id = self._build_entity_id("sensor", "connection_uptime")

    @property
    def native_value(self) -> datetime | None:
        connection = self._connection
        if connection is None:
            return None
        since = connection.get("connected_since")
        parsed = dt_util.parse_datetime(since) if since else None
        if parsed is None:
            return None
        # Digi timestamps are naive local time — attach the local zone.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        return parsed
