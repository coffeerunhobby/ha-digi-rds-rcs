"""Sensor platform for the Digi (RCS & RDS) integration."""

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
    CURRENCY_RON,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import DigiConfigEntry, DigiCoordinator

_LOGGER = logging.getLogger(__name__)


# ── Service (per address+service) sensor descriptions ───────────────────────
@dataclass(frozen=True, kw_only=True)
class DigiServiceDescription(SensorEntityDescription):
    """Describes a sensor bound to a single Digi service account."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _service_invoice_attrs(service: dict[str, Any]) -> dict[str, Any]:
    latest = service.get("latest") or {}
    history = service.get("history") or []
    return {
        "address": service.get("address"),
        "service": service.get("service_label"),
        "invoice_number": service.get("invoice_number"),
        "issue_date": service.get("issue_date"),
        "due_date": service.get("due_date"),
        "status": service.get("status"),
        "invoice_amount": service.get("amount"),
        "unpaid_invoices": service.get("unpaid_count"),
        "services_count": service.get("services_count"),
        "pdf_url": latest.get("pdf_url"),
        "services": latest.get("services") or [],
        "history": [
            {
                "invoice_number": item.get("invoice_number"),
                "issue_date": item.get("issue_date"),
                "due_date": item.get("due_date"),
                "amount": item.get("amount"),
                "remaining": item.get("rest"),
                "status": item.get("status"),
            }
            for item in history
        ],
    }


SERVICE_SENSORS: tuple[DigiServiceDescription, ...] = (
    DigiServiceDescription(
        key="de_plata",
        translation_key="de_plata",
        icon="mdi:cash-multiple",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_RON,
        value_fn=lambda s: s.get("rest"),
        attrs_fn=_service_invoice_attrs,
    ),
    DigiServiceDescription(
        key="ultima_factura",
        translation_key="ultima_factura",
        icon="mdi:file-document-outline",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_RON,
        value_fn=lambda s: s.get("amount"),
    ),
    DigiServiceDescription(
        key="scadenta",
        translation_key="scadenta",
        icon="mdi:calendar-clock",
        value_fn=lambda s: s.get("due_date"),
    ),
    DigiServiceDescription(
        key="restanta",
        translation_key="restanta",
        icon="mdi:alert-circle-outline",
        value_fn=lambda s: "yes" if s.get("has_arrears") else "no",
    ),
)


# ── Totals (account-wide) sensor descriptions ───────────────────────────────
@dataclass(frozen=True, kw_only=True)
class DigiTotalsDescription(SensorEntityDescription):
    """Describes a sensor bound to the account-wide totals."""

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
        key="total_ultima_factura",
        translation_key="total_ultima_factura",
        icon="mdi:file-document-outline",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_RON,
        value_fn=lambda t: t.get("ultima_factura"),
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


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: DigiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Digi sensors from a config entry."""
    coordinator = config_entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        data = coordinator.data or {}
        entities: list[SensorEntity] = []

        # Account-wide totals device (added once).
        if "totals" not in known:
            known.add("totals")
            entities.extend(
                DigiTotalsSensor(coordinator, config_entry, description)
                for description in TOTALS_SENSORS
            )

        # One device per discovered service account.
        for service in data.get("services", []):
            account_unique = service.get("account_unique")
            if not account_unique or account_unique in known:
                continue
            known.add(account_unique)
            entities.extend(
                DigiServiceSensor(coordinator, config_entry, account_unique, description)
                for description in SERVICE_SENSORS
            )

        if entities:
            async_add_entities(entities)

    _add_new_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class DigiServiceSensor(CoordinatorEntity[DigiCoordinator], SensorEntity):
    """A sensor for a single Digi service account (address + service)."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: DigiServiceDescription

    def __init__(
        self,
        coordinator: DigiCoordinator,
        config_entry: DigiConfigEntry,
        account_unique: str,
        description: DigiServiceDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._account_unique = account_unique
        # Scope the device/entity ids to the config entry so two Digi accounts
        # that share an address or service name cannot collide in the registry.
        self._device_id = f"{config_entry.entry_id}_{account_unique}"
        self._attr_unique_id = f"{self._device_id}_{description.key}"

    @property
    def _service(self) -> dict[str, Any] | None:
        for service in (self.coordinator.data or {}).get("services", []):
            if service.get("account_unique") == self._account_unique:
                return service
        return None

    @property
    def available(self) -> bool:
        return super().available and self._service is not None

    @property
    def device_info(self) -> DeviceInfo:
        service = self._service or {}
        address = service.get("address") or "Digi"
        label = service.get("service_label") or "Services"
        name = f"Digi · {address} · {label}".strip(" ·")
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> Any:
        service = self._service
        if service is None:
            return None
        return self.entity_description.value_fn(service)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        service = self._service
        if service is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(service)


class DigiTotalsSensor(CoordinatorEntity[DigiCoordinator], SensorEntity):
    """A sensor for the account-wide Digi totals."""

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
        self._attr_unique_id = f"{config_entry.entry_id}_{description.key}"
        self._device_id = f"{config_entry.entry_id}_totals"

    @property
    def device_info(self) -> DeviceInfo:
        label = (self.coordinator.data or {}).get("account_label") or "Digi services"
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=f"Digi · {label}",
            manufacturer=MANUFACTURER,
            model=MODEL,
            entry_type=DeviceEntryType.SERVICE,
        )

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
            "last_update": data.get("last_update"),
        }
