"""Sensor platform for the Digi (RCS & RDS) integration.

Layout mirrors the "account → devices" model: the config entry is the Digi
account (titled by e-mail), and each address is its own device named by the
address, with a handful of sensors.

The device/entity ids use an md5 hash of the address (never the address text),
so the address is not exposed in entity_ids; it is the device name and a sensor
attribute instead.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfInformation
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTRIBUTION,
    CONF_CLIENT_CODE,
    CURRENCY_RON,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import DigiConfigEntry, DigiCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class DigiAddressDescription(SensorEntityDescription):
    """Describes a sensor on an address device."""

    value_fn: Callable[[dict[str, Any]], Any]
    with_attributes: bool = False


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
        value_fn=lambda a: a.get("due_date"),
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
            # Connection (uptime / traffic) sensors appear once the FiberLink
            # logs have been read for an internet address.
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
                entities.extend(
                    DigiTrafficSensor(
                        coordinator, config_entry, address_unique, description
                    )
                    for description in TRAFFIC_SENSORS
                )
        if entities:
            async_add_entities(entities)

    _add_new_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class DigiAddressSensor(CoordinatorEntity[DigiCoordinator], SensorEntity):
    """A sensor on an address device."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: DigiAddressDescription

    def __init__(
        self,
        coordinator: DigiCoordinator,
        config_entry: DigiConfigEntry,
        address_unique: str,
        description: DigiAddressDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._config_entry = config_entry
        self._address_unique = address_unique
        self._device_id = f"{config_entry.entry_id}_{address_unique}"
        self._attr_unique_id = f"{self._device_id}_{description.key}"
        # Entity id: prefix with the Digi client code ("Cod client") when known,
        # otherwise the entry id; then the md5 address hash (never the address
        # text). e.g. sensor.digi_123456_abcdef123456_amount_due
        prefix = config_entry.data.get(CONF_CLIENT_CODE) or config_entry.entry_id[:8]
        self.entity_id = (
            f"sensor.{DOMAIN}_{prefix}_{address_unique}_{description.key}"
        )

    @property
    def _address(self) -> dict[str, Any] | None:
        for address in (self.coordinator.data or {}).get("addresses", []):
            if address.get("address_unique") == self._address_unique:
                return address
        return None

    @property
    def available(self) -> bool:
        return super().available and self._address is not None

    @property
    def device_info(self) -> DeviceInfo:
        address = self._address or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=address.get("address") or "Adresă Digi",
            manufacturer=MANUFACTURER,
            model=MODEL,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> Any:
        address = self._address
        if address is None:
            return None
        return self.entity_description.value_fn(address)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.entity_description.with_attributes:
            return None
        address = self._address
        if address is None:
            return None
        return _invoice_attributes(address)


class DigiInternetSensor(CoordinatorEntity[DigiCoordinator], SensorEntity):
    """Public IP of the internet service at an address (plan/IPv6 in attrs).

    Disabled by default: the public IP is mildly sensitive, so users opt in.
    """

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_translation_key = "public_ip"
    _attr_icon = "mdi:ip-network"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: DigiCoordinator,
        config_entry: DigiConfigEntry,
        address_unique: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._address_unique = address_unique
        self._device_id = f"{config_entry.entry_id}_{address_unique}"
        self._attr_unique_id = f"{self._device_id}_public_ip"
        prefix = config_entry.data.get(CONF_CLIENT_CODE) or config_entry.entry_id[:8]
        self.entity_id = f"sensor.{DOMAIN}_{prefix}_{address_unique}_public_ip"

    @property
    def _internet(self) -> dict[str, Any] | None:
        for address in (self.coordinator.data or {}).get("addresses", []):
            if address.get("address_unique") == self._address_unique:
                return address.get("internet")
        return None

    @property
    def available(self) -> bool:
        return super().available and self._internet is not None

    @property
    def device_info(self) -> DeviceInfo:
        address = next(
            (
                a
                for a in (self.coordinator.data or {}).get("addresses", [])
                if a.get("address_unique") == self._address_unique
            ),
            {},
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=address.get("address") or "Adresă Digi",
            manufacturer=MANUFACTURER,
            model=MODEL,
            entry_type=DeviceEntryType.SERVICE,
        )

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


@dataclass(frozen=True, kw_only=True)
class DigiTrafficDescription(SensorEntityDescription):
    """Describes a monthly-traffic sensor (download or upload)."""

    # Key into the coordinator's connection dict for the current month total.
    current_key: str
    # Key into each monthly bucket ({"download_bytes"/"upload_bytes"}).
    monthly_key: str


TRAFFIC_SENSORS: tuple[DigiTrafficDescription, ...] = (
    DigiTrafficDescription(
        key="data_downloaded",
        translation_key="data_downloaded",
        icon="mdi:download-network-outline",
        current_key="download_bytes_month",
        monthly_key="download_bytes",
    ),
    DigiTrafficDescription(
        key="data_uploaded",
        translation_key="data_uploaded",
        icon="mdi:upload-network-outline",
        current_key="upload_bytes_month",
        monthly_key="upload_bytes",
    ),
)


def _to_gib(num_bytes: Any) -> float:
    return round((num_bytes or 0) / 1024**3, 2)


def _monthly_gib(monthly: dict[str, Any] | None, key: str) -> dict[str, float]:
    return {month: _to_gib(v.get(key)) for month, v in (monthly or {}).items()}


class _DigiConnectionEntity(CoordinatorEntity[DigiCoordinator], SensorEntity):
    """Base for the opt-in FiberLink connection sensors on an address device.

    Disabled by default: connection logs expose the line's IP/MAC and traffic,
    so users opt in.
    """

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: DigiCoordinator,
        config_entry: DigiConfigEntry,
        address_unique: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._address_unique = address_unique
        self._device_id = f"{config_entry.entry_id}_{address_unique}"
        self._prefix = (
            config_entry.data.get(CONF_CLIENT_CODE) or config_entry.entry_id[:8]
        )

    def _address(self) -> dict[str, Any] | None:
        for address in (self.coordinator.data or {}).get("addresses", []):
            if address.get("address_unique") == self._address_unique:
                return address
        return None

    @property
    def _connection(self) -> dict[str, Any] | None:
        address = self._address()
        return address.get("connection") if address else None

    @property
    def available(self) -> bool:
        return super().available and self._connection is not None

    @property
    def device_info(self) -> DeviceInfo:
        address = self._address() or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=address.get("address") or "Adresă Digi",
            manufacturer=MANUFACTURER,
            model=MODEL,
            entry_type=DeviceEntryType.SERVICE,
        )


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
        self.entity_id = (
            f"sensor.{DOMAIN}_{self._prefix}_{address_unique}_connection_status"
        )

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
            "monthly_download_gib": _monthly_gib(
                connection.get("monthly"), "download_bytes"
            ),
            "monthly_upload_gib": _monthly_gib(
                connection.get("monthly"), "upload_bytes"
            ),
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
        self.entity_id = (
            f"sensor.{DOMAIN}_{self._prefix}_{address_unique}_connection_uptime"
        )

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


class DigiTrafficSensor(_DigiConnectionEntity):
    """Current-month download/upload (GiB), with the monthly history in attrs."""

    entity_description: DigiTrafficDescription
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfInformation.GIBIBYTES
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: DigiCoordinator,
        config_entry: DigiConfigEntry,
        address_unique: str,
        description: DigiTrafficDescription,
    ) -> None:
        super().__init__(coordinator, config_entry, address_unique)
        self.entity_description = description
        self._attr_translation_key = description.translation_key
        self._attr_icon = description.icon
        self._attr_unique_id = f"{self._device_id}_{description.key}"
        self.entity_id = (
            f"sensor.{DOMAIN}_{self._prefix}_{address_unique}_{description.key}"
        )

    @property
    def native_value(self) -> float | None:
        connection = self._connection
        if connection is None:
            return None
        return _to_gib(connection.get(self.entity_description.current_key))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        connection = self._connection
        if connection is None:
            return None
        return {
            "month": connection.get("month_key"),
            "monthly_gib": _monthly_gib(
                connection.get("monthly"), self.entity_description.monthly_key
            ),
        }
