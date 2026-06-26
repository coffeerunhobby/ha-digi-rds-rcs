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
    CONF_ADDRESS_MAP,
    CONF_COOKIES,
    CONF_HISTORY_LIMIT,
    CONF_SELECTED_ACCOUNT_ID,
    CONF_UPDATE_INTERVAL,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
)
from .scheduler import DATA_SCHEDULER

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


def _normalize_address(text: str) -> str:
    """Lowercase, drop diacritics and keep only alphanumerics — for matching the
    invoices-page address text against the login address labels."""
    value = (text or "").lower()
    for src, dst in {
        "ă": "a",
        "â": "a",
        "î": "i",
        "ș": "s",
        "ş": "s",
        "ț": "t",
        "ţ": "t",
    }.items():
        value = value.replace(src, dst)
    return "".join(ch for ch in value if ch.isalnum())


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
        # update_interval is None: refreshes are driven by the domain-level
        # round-robin scheduler (see scheduler.py), not by self-polling, so the
        # accounts are updated serially and spaced out.
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
            config_entry=config_entry,
        )
        self.interval_seconds = interval_hours * 3600
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
        # Serialize fetches across all accounts via the shared scheduler lock,
        # so two Digi accounts never hit the site at the same moment.
        scheduler = (self.hass.data.get(DOMAIN) or {}).get(DATA_SCHEDULER)
        if scheduler is not None:
            async with scheduler.lock:
                return await self._fetch_and_build()
        return await self._fetch_and_build()

    async def _fetch_and_build(self) -> dict[str, Any]:
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

    def _resolve_address_id(self, address: str) -> str | None:
        """Match an invoices-page address to its Digi numeric address-id.

        The invoices page only carries the address text, while the login page
        gives a {numeric-id: label} map. The invoice text (e.g. "… Ap. 14") is a
        normalized substring of the matching label, so a unique containment match
        yields the real Digi id. Returns None if absent or ambiguous.
        """
        address_map = self.config_entry.data.get(CONF_ADDRESS_MAP) or {}
        norm = _normalize_address(address)
        if not address_map or not norm:
            return None
        matches = [
            address_id
            for address_id, label in address_map.items()
            if norm in _normalize_address(label)
        ]
        return matches[0] if len(matches) == 1 else None

    def _build_snapshot(self, digi_data: Any) -> dict[str, Any]:
        account_id = (
            self.config_entry.data.get(CONF_SELECTED_ACCOUNT_ID)
            or digi_data.account_id
            or "digi"
        )

        address_rows: list[dict[str, Any]] = []

        # When there is exactly one address and one known address-id, map them
        # directly (single-address accounts), regardless of label formatting.
        address_map = self.config_entry.data.get(CONF_ADDRESS_MAP) or {}
        direct_id = (
            next(iter(address_map))
            if len(address_map) == 1 and len(digi_data.invoices_by_address) == 1
            else None
        )

        # One row per address — invoices are aggregated across the services
        # billed at that address (Digi already groups invoices by address).
        for address_key, entry in digi_data.invoices_by_address.items():
            address = entry.address or address_key
            # Prefer the real Digi numeric address-id (matched by label, or the
            # 1:1 direct map); fall back to a hash of the address text.
            address_unique = (
                self._resolve_address_id(address)
                or direct_id
                or _address_hash(address)
            )

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

        return {
            "account_id": str(account_id),
            "addresses": address_rows,
            "needs_reauth": digi_data.needs_reauth,
            "last_update": digi_data.last_update.isoformat()
            if digi_data.last_update
            else None,
        }
