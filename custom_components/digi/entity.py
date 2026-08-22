"""Shared identity and lookup for entities bound to one Digi address.

Every entity in this integration belongs to exactly one address, and each one
previously repeated the same four things: the device identifiers, the address
lookup through the coordinator snapshot, the availability rule, and the
entity_id prefix. That was four copies of logic that must agree — and if they
drift, entities silently split across two devices.

Subclasses supply the platform (``SensorEntity``, ``BinarySensorEntity``, …)
and their own ``key``; everything about *which address this is* lives here.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_CLIENT_CODE, DOMAIN, MANUFACTURER, MODEL
from .coordinator import DigiConfigEntry, DigiCoordinator
from .models import AddressSnapshot


class DigiAddressEntity(CoordinatorEntity[DigiCoordinator]):
    """Base for an entity that represents one address on a Digi account."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

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
        # Entity ids read better with the Digi client code ("Cod client") when
        # it is known; the entry id is a stable fallback. The address text is
        # never used, so it stays out of entity_ids.
        self._prefix = (
            config_entry.data.get(CONF_CLIENT_CODE) or config_entry.entry_id[:8]
        )

    def _build_entity_id(self, platform: str, key: str) -> str:
        """e.g. ``sensor.digi_123456_11112222_amount_due``."""
        return f"{platform}.{DOMAIN}_{self._prefix}_{self._address_unique}_{key}"

    @property
    def _address(self) -> AddressSnapshot | None:
        """This entity's row in the coordinator snapshot, if still present."""
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
