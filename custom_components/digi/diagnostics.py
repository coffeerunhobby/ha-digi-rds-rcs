"""Diagnostics support for the Digi (RCS & RDS) integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ADDRESS_MAP,
    CONF_CLIENT_CODE,
    CONF_COOKIES,
    CONF_PASSWORD,
    CONF_SELECTED_ACCOUNT_ID,
    CONF_SELECTED_ACCOUNT_LABEL,
    CONF_USERNAME,
)
from .coordinator import DigiConfigEntry

TO_REDACT_DATA = {
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_COOKIES,
    CONF_CLIENT_CODE,
    CONF_ADDRESS_MAP,
    CONF_SELECTED_ACCOUNT_ID,
    CONF_SELECTED_ACCOUNT_LABEL,
}

TO_REDACT_RESULT = {
    "address",
    "address_key",
    "address_id",
    "account",
    "account_id",
    "account_label",
    "invoice_number",
    "id_ultima_factura",
    "last_invoice_id",
    "pdf_url",
    "ipv4",
    "ipv6",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DigiConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with personal data redacted."""
    coordinator = entry.runtime_data

    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT_DATA),
        },
        "coordinator": {
            "last_update_success": getattr(
                coordinator, "last_update_success", None
            ),
            "data": async_redact_data(coordinator.data or {}, TO_REDACT_RESULT),
        },
    }
