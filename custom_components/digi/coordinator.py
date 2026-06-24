"""Data update coordinator for the Digi (RCS & RDS) integration."""

from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    DigiApiClient,
    DigiAuthError,
    DigiError,
    DigiReauthRequired,
)
from .const import (
    CONF_COOKIES,
    CONF_HISTORY_LIMIT,
    CONF_SELECTED_ACCOUNT_ID,
    CONF_SELECTED_ACCOUNT_LABEL,
    CONF_UPDATE_INTERVAL,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

type DigiConfigEntry = ConfigEntry["DigiCoordinator"]


# ── Text helpers ────────────────────────────────────────────────────────────
def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip().replace(".", "-").replace("/", "-")
    parts = text.split("-")
    if len(parts) != 3:
        return None
    try:
        day, month, year = [int(p) for p in parts]
        return date(year, month, day)
    except ValueError:
        return None


def _slugify(text: str) -> str:
    value = (text or "").lower()
    replacements = {
        "ă": "a",
        "â": "a",
        "î": "i",
        "ș": "s",
        "ş": "s",
        "ț": "t",
        "ţ": "t",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    slug = "".join(ch if ch.isalnum() else "_" for ch in value)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_") or "cont"


def _service_label(value: str | None) -> str:
    text = str(value or "").strip()
    return text or "Digi services"


def _address_hash(address: str) -> str:
    """A short, address-free identifier for an address (md5 of the text).

    Used for entity/device ids so the readable address is not embedded in
    entity_ids; the full address is kept as a sensor attribute. md5 (not crc32)
    to keep collisions negligible.
    """
    return hashlib.md5((address or "").encode("utf-8")).hexdigest()[:12]


def _services_count(latest: dict[str, Any]) -> int:
    services = latest.get("services") or []
    if isinstance(services, list) and services:
        return len(services)
    for key in ("numar_servicii", "services_count", "services_total"):
        val = latest.get(key)
        try:
            if val is not None:
                return int(val)
        except (TypeError, ValueError):
            pass
    return 0


class DigiCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches the Digi invoices page and transforms it for the sensors."""

    config_entry: DigiConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        interval_hours = int(
            config_entry.data.get(
                CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_HOURS
            )
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=interval_hours),
            config_entry=config_entry,
        )
        self._history_limit = int(
            config_entry.data.get(CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT)
        )
        # Snapshot of the user-tunable settings; used to decide whether an
        # entry update should trigger a reload (cookie writes must not).
        self._settings_signature = (interval_hours, self._history_limit)
        self.api: DigiApiClient | None = None

    def settings_changed(self) -> bool:
        """Return True if the tunable settings differ from the running config."""
        current = (
            int(
                self.config_entry.data.get(
                    CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_HOURS
                )
            ),
            int(self.config_entry.data.get(CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT)),
        )
        return current != self._settings_signature

    def _ensure_api(self) -> DigiApiClient:
        if self.api is None:
            self.api = DigiApiClient(async_get_clientsession(self.hass))
            cookies = self.config_entry.data.get(CONF_COOKIES) or []
            self.api.import_cookies(cookies)
        return self.api

    def _persist_cookies(self) -> None:
        """Save rotated session cookies back to the entry (no reload)."""
        if self.api is None:
            return
        cookies = self.api.export_cookies()
        if cookies and cookies != (self.config_entry.data.get(CONF_COOKIES) or []):
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_COOKIES: cookies},
            )

    async def async_shutdown(self) -> None:
        """Close the API client on teardown."""
        if self.api is not None:
            await self.api.close()
            self.api = None
        await super().async_shutdown()

    async def _async_update_data(self) -> dict[str, Any]:
        api = self._ensure_api()

        cookies = self.config_entry.data.get(CONF_COOKIES) or []
        if not cookies:
            raise ConfigEntryAuthFailed("Digi session is missing — re-authenticate.")

        try:
            digi_data = await api.async_fetch_data(history_limit=self._history_limit)
        except DigiReauthRequired as err:
            raise ConfigEntryAuthFailed(
                "Digi session expired — re-authentication required."
            ) from err
        except DigiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except DigiError as err:
            raise UpdateFailed(str(err)) from err

        self._persist_cookies()
        return self._build_snapshot(digi_data)

    def _build_snapshot(self, digi_data: Any) -> dict[str, Any]:
        account_id = (
            self.config_entry.data.get(CONF_SELECTED_ACCOUNT_ID)
            or digi_data.account_id
            or "digi"
        )
        account_label = (
            self.config_entry.data.get(CONF_SELECTED_ACCOUNT_LABEL)
            or digi_data.account_label
            or "Digi account"
        )

        address_rows: list[dict[str, Any]] = []
        total_sold = 0.0
        total_ultima_factura = 0.0
        total_services = 0
        has_arrears = False
        arrears_due_dates: list[date] = []
        latest_global: dict[str, Any] | None = None
        latest_global_date: date | None = None
        address_keys: set[str] = set()

        # One row per address — invoices are aggregated across the services
        # billed at that address (Digi already groups invoices by address).
        for address_key, entry in digi_data.invoices_by_address.items():
            address = entry.address or address_key
            address_id = _address_hash(address)
            address_keys.add(address_key or address_id)

            items = [item for item in (entry.history or []) if item]
            if not items and entry.latest:
                items = [entry.latest]
            if not items:
                continue

            items.sort(
                key=lambda x: _parse_date(x.get("issue_date")) or date.min,
                reverse=True,
            )
            latest = items[0]
            address_unique = address_id

            unpaid = [
                item
                for item in items
                if float(item.get("rest") or 0.0) > 0
                or "neach" in str(item.get("status") or "").lower()
            ]
            rest = round(
                sum(
                    max(float(item.get("rest") or item.get("amount") or 0.0), 0.0)
                    for item in unpaid
                ),
                2,
            )
            amount = round(float(latest.get("amount") or 0.0), 2)
            issue_date = _parse_date(latest.get("issue_date"))

            latest_services = latest.get("services") or []
            if isinstance(latest_services, list) and latest_services:
                services_count = len(latest_services)
            else:
                services_count = _services_count(latest)

            # Distinct service descriptions seen at this address (for display).
            service_labels: list[str] = []
            for item in items:
                label = _service_label(item.get("description"))
                if label not in service_labels:
                    service_labels.append(label)
            service_label = ", ".join(service_labels[:3]) if service_labels else "Digi"

            total_sold += max(rest, 0.0)
            total_ultima_factura += amount
            total_services += services_count
            has_arrears = has_arrears or rest > 0

            for item in unpaid:
                due = _parse_date(item.get("due_date"))
                if due:
                    arrears_due_dates.append(due)

            if issue_date and (
                latest_global_date is None or issue_date > latest_global_date
            ):
                latest_global_date = issue_date
                latest_global = latest
            elif latest_global is None:
                latest_global = latest

            address_rows.append(
                {
                    "address_unique": address_unique,
                    "address_key": address_key,
                    "address": address,
                    "service_label": service_label,
                    "rest": rest,
                    "amount": amount,
                    "issue_date": latest.get("issue_date"),
                    "due_date": latest.get("due_date"),
                    "invoice_number": latest.get("invoice_number")
                    or latest.get("invoice_id"),
                    "status": latest.get("status"),
                    "pdf_url": latest.get("pdf_url"),
                    "unpaid_count": len(unpaid),
                    "has_arrears": rest > 0,
                    "services_count": services_count,
                    "services": latest_services,
                    "latest": latest,
                    "history": items,
                }
            )

        next_due = min(arrears_due_dates).isoformat() if arrears_due_dates else None
        latest_invoice_id = None
        latest_invoice_value = round(total_ultima_factura, 2)
        if latest_global:
            latest_invoice_id = latest_global.get("invoice_number") or latest_global.get(
                "invoice_id"
            )
            latest_invoice_value = round(float(latest_global.get("amount") or 0.0), 2)

        return {
            "account_id": str(account_id),
            "account_label": account_label,
            "addresses": address_rows,
            "totals": {
                "sold": round(total_sold, 2),
                "ultima_factura": latest_invoice_value,
                "id_ultima_factura": latest_invoice_id,
                "scadenta": next_due,
                "has_arrears": has_arrears,
                "numar_servicii": total_services,
                "addresses_count": len(address_keys),
            },
            "needs_reauth": digi_data.needs_reauth,
            "last_update": digi_data.last_update.isoformat()
            if digi_data.last_update
            else None,
        }
