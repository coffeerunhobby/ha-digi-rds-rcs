"""Binary sensor platform for the Digi (RCS & RDS) integration.

Exposes the overdue state as a ``problem`` binary sensor. Home Assistant renders
device-class ``problem`` entities in red when they are ``on`` (with
``state_color: true`` on a card), which is the native way to surface this —
frontend styling is not something an integration can control directly.

The equivalent ``sensor.*_overdue`` (``yes``/``no``) is kept for existing
automations and templates.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DigiConfigEntry, DigiCoordinator
from .entity import DigiAddressEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: DigiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one overdue binary sensor per address."""
    coordinator = config_entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        entities: list[BinarySensorEntity] = []
        for address in (coordinator.data or {}).get("addresses", []):
            address_unique = address.get("address_unique")
            if not address_unique or address_unique in known:
                continue
            known.add(address_unique)
            entities.append(
                DigiOverdueBinarySensor(coordinator, config_entry, address_unique)
            )
        if entities:
            async_add_entities(entities)

    _add_new()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_new))


class DigiOverdueBinarySensor(DigiAddressEntity, BinarySensorEntity):
    """True while the address has an unpaid balance."""

    _attr_translation_key = "overdue"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: DigiCoordinator,
        config_entry: DigiConfigEntry,
        address_unique: str,
    ) -> None:
        super().__init__(coordinator, config_entry, address_unique)
        self._attr_unique_id = f"{self._device_id}_overdue_problem"
        self.entity_id = self._build_entity_id("binary_sensor", "overdue")

    @property
    def is_on(self) -> bool | None:
        address = self._address
        if address is None:
            return None
        return bool(address.get("has_arrears"))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        address = self._address
        if address is None:
            return None
        return {
            "amount_due": address.get("rest"),
            "due_date": address.get("due_date"),
            "unpaid_invoices": address.get("unpaid_count"),
        }
