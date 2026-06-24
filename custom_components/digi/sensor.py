"""Sensor platform for the Digi (RCS & RDS) integration.

Each config entry (Digi account) is a single Home Assistant device. Under it:
  - account-wide totals (amount due, next due date, overdue, number of services)
  - one row per address (state = amount due; details in attributes)
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

from .const import (
    ATTRIBUTION,
    CONF_USERNAME,
    CURRENCY_RON,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import DigiConfigEntry, DigiCoordinator

_LOGGER = logging.getLogger(__name__)


# ── Account-wide totals sensors ─────────────────────────────────────────────
@dataclass(frozen=True, kw_only=True)
class DigiTotalsDescription(SensorEntityDescription):
    """Describes an account-wide totals sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


TOTALS_SENSORS: tuple[DigiTotalsDescription, ...] = (
    DigiTotalsDescription(
        key="total_de_plata",
        translation_key="total_de_plata",
        icon="mdi:cash-multiple",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_RON,
        value_fn=lambda t: t.get("sold"),
    ),
    DigiTotalsDescription(
        key="total_scadenta",
        translation_key="total_scadenta",
        icon="mdi:calendar-clock",
        value_fn=lambda t: t.get("scadenta"),
    ),
    DigiTotalsDescription(
        key="total_restanta",
        translation_key="total_restanta",
        icon="mdi:alert-circle-outline",
        value_fn=lambda t: "yes" if t.get("has_arrears") else "no",
    ),
    DigiTotalsDescription(
        key="numar_servicii",
        translation_key="numar_servicii",
        icon="mdi:counter",
        value_fn=lambda t: t.get("numar_servicii"),
    ),
)


def _account_device(config_entry: DigiConfigEntry) -> DeviceInfo:
    """The single device that represents the Digi account (named by e-mail)."""
    email = config_entry.data.get(CONF_USERNAME) or "Digi account"
    return DeviceInfo(
        identifiers={(DOMAIN, config_entry.entry_id)},
        name=f"Digi · {email}",
        manufacturer=MANUFACTURER,
        model=MODEL,
        entry_type=DeviceEntryType.SERVICE,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: DigiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Digi sensors: account totals + one row per address."""
    coordinator = config_entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        data = coordinator.data or {}
        entities: list[SensorEntity] = []

        if "totals" not in known:
            known.add("totals")
            entities.extend(
                DigiTotalsSensor(coordinator, config_entry, description)
                for description in TOTALS_SENSORS
            )

        for address in data.get("addresses", []):
            address_unique = address.get("address_unique")
            if not address_unique or address_unique in known:
                continue
            known.add(address_unique)
            entities.append(
                DigiAddressSensor(coordinator, config_entry, address_unique)
            )

        if entities:
            async_add_entities(entities)

    _add_new_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class DigiTotalsSensor(CoordinatorEntity[DigiCoordinator], SensorEntity):
    """An account-wide totals sensor on the account device."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: DigiTotalsDescription

    def __init__(
        self,
        coordinator: DigiCoordinator,
        config_entry: DigiConfigEntry,
        description: DigiTotalsDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_{description.key}"
        # Entity id is derived from the entry, not the e-mail, so it stays
        # clean and stable regardless of the account label.
        self.entity_id = (
            f"sensor.{DOMAIN}_{config_entry.entry_id[:8]}_{description.key}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        return _account_device(self._config_entry)

    @property
    def native_value(self) -> Any:
        totals = (self.coordinator.data or {}).get("totals")
        if not totals:
            return None
        return self.entity_description.value_fn(totals)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "total_de_plata":
            return None
        data = self.coordinator.data or {}
        totals = data.get("totals") or {}
        return {
            "account": data.get("account_label"),
            "addresses_count": totals.get("addresses_count"),
            "services_count": totals.get("numar_servicii"),
            "last_invoice_id": totals.get("id_ultima_factura"),
            "last_invoice_amount": totals.get("ultima_factura"),
            "last_update": data.get("last_update"),
        }


class DigiAddressSensor(CoordinatorEntity[DigiCoordinator], SensorEntity):
    """One row per address; state is the amount due for that address."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:map-marker"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_RON

    def __init__(
        self,
        coordinator: DigiCoordinator,
        config_entry: DigiConfigEntry,
        address_unique: str,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._address_unique = address_unique
        self._attr_unique_id = f"{config_entry.entry_id}_{address_unique}"
        # The entity id uses the md5 address hash, never the address text, so
        # the address is not exposed in entity_ids / dashboards / screenshots.
        self.entity_id = (
            f"sensor.{DOMAIN}_{config_entry.entry_id[:8]}_{address_unique}"
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
    def name(self) -> str:
        address = self._address or {}
        return address.get("address") or "Adresă Digi"

    @property
    def device_info(self) -> DeviceInfo:
        return _account_device(self._config_entry)

    @property
    def native_value(self) -> Any:
        address = self._address
        return address.get("rest") if address else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        address = self._address
        if address is None:
            return None
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
