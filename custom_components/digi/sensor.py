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

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CURRENCY_RON, DOMAIN, MANUFACTURER, MODEL
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

    @callback
    def _add_new_entities() -> None:
        data = coordinator.data or {}
        entities: list[SensorEntity] = []
        for address in data.get("addresses", []):
            address_unique = address.get("address_unique")
            if not address_unique or address_unique in known:
                continue
            known.add(address_unique)
            entities.extend(
                DigiAddressSensor(coordinator, config_entry, address_unique, description)
                for description in ADDRESS_SENSORS
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
        # Entity id uses the md5 address hash, never the address text.
        self.entity_id = (
            f"sensor.{DOMAIN}_{config_entry.entry_id[:8]}_{address_unique}_{description.key}"
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
