"""HTTP client for the Digi (RCS & RDS) customer portal (www.digi.ro).

Digi exposes no public API, so this client drives the same web endpoints the
browser uses: a form login, an optional 2FA step (SMS / e-mail), an optional
address selection, and finally the ``my-account/invoices`` page which is
scraped for invoice rows and per-invoice details.

The client keeps its own cookie jar (separate from Home Assistant's shared
session) so the authenticated session can be exported/imported and persisted
across restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from html import unescape
from typing import Any

import aiohttp
from yarl import URL

from .const import (
    ACCOUNT_DETAILS_URL,
    ADDRESS_CONFIRM_URL,
    ADDRESS_SELECT_URL,
    BASE_URL,
    CONNECTION_LOGS_URL,
    FIBERLINK_URL,
    INVOICES_URL,
    LOGIN_URL,
    MY_SERVICES_URL,
    TWO_FA_URL,
    TWO_FA_VALIDATE_URL,
    USER_AGENT,
)
from .dates import sort_key

# Re-exported for callers that import them from this module.
from .exceptions import (  # noqa: F401
    DigiAccountSelectionRequired,
    DigiAuthError,
    DigiError,
    DigiReauthRequired,
    DigiTwoFactorError,
    DigiTwoFactorRequired,
)
from .models import (
    AddressInvoices,
    AddressOption,
    ConnectionSession,
    DigiData,
    InvoiceDetail,
    InvoiceSummary,
    TwoFactorContext,
)
from .parser import (
    RE_ADDRESS_OPTION,
    RE_CLIENT_CODE,
    _clean_text,
    _extract_radio_options,
    _extract_select_options,
    _parse_2fa_context,
    _parse_connection_logs,
    _parse_internet,
    _parse_invoice_detail,
    _parse_invoice_page,
)

_LOGGER = logging.getLogger(__name__)








class DigiApiClient:
    """Drives the Digi customer portal and returns structured invoice data."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        connector = session.connector
        if connector is None:
            raise DigiError("HTTP session connector is unavailable")

        self._default_headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": BASE_URL,
            "Origin": BASE_URL,
        }
        # Reuse Home Assistant's connector but keep an isolated cookie jar so
        # the Digi session can be exported and persisted independently.
        #
        # IMPORTANT: the browser User-Agent must be set at the session level,
        # not only per-request. Digi serves an empty "204 No Content" page to
        # non-browser User-Agents, and depending on the aiohttp version the
        # session's built-in "Python/aiohttp" default can otherwise win over a
        # per-request header — which produces an empty invoice page in Home
        # Assistant even though the session is authenticated.
        self._session = aiohttp.ClientSession(
            connector=connector,
            connector_owner=False,
            cookie_jar=aiohttp.CookieJar(),
            timeout=session.timeout,
            headers=self._default_headers,
        )

    async def close(self) -> None:
        if not self._session.closed:
            await self._session.close()

    async def _request(
        self, method: str, url: str, **kwargs: Any
    ) -> aiohttp.ClientResponse:
        headers = dict(self._default_headers)
        headers.update(kwargs.pop("headers", {}))
        return await self._session.request(method, url, headers=headers, **kwargs)

    async def _read_text(self, response: aiohttp.ClientResponse) -> str:
        return await response.text(errors="ignore")

    # ── Cookie persistence ──────────────────────────────────────────────────
    def export_cookies(self) -> list[dict[str, Any]]:
        cookies: list[dict[str, Any]] = []
        for cookie in self._session.cookie_jar:
            cookies.append(
                {
                    "key": cookie.key,
                    "value": cookie.value,
                    "domain": cookie["domain"],
                    "path": cookie["path"],
                    "secure": bool(cookie["secure"]),
                    "expires": cookie["expires"],
                }
            )
        return cookies

    def import_cookies(self, cookies: list[dict[str, Any]]) -> None:
        jar = self._session.cookie_jar
        jar.clear()

        if not cookies:
            return

        for item in cookies:
            domain = str(item.get("domain", "")).strip()
            key = str(item.get("key", "")).strip()
            value = str(item.get("value", ""))

            if not domain or not key:
                continue

            jar.update_cookies(
                {key: value},
                response_url=URL(f"https://{domain.lstrip('.')}"),
            )

    # ── Login ───────────────────────────────────────────────────────────────
    async def begin_login(self, email: str, password: str) -> tuple[str, str]:
        self._session.cookie_jar.clear()

        payload = {
            "signin-input-app": "0",
            "signin-input-email": email,
            "signin-input-password": password,
            "signin-submit-button": "",
        }
        resp = await self._request(
            "POST",
            LOGIN_URL,
            data=payload,
            allow_redirects=True,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        text = await self._read_text(resp)
        final_url = str(resp.url)

        if "auth/login" in final_url and "2fa" not in final_url:
            raise DigiAuthError("Invalid credentials")

        return final_url, text

    async def login(self, email: str, password: str) -> tuple[str, str]:
        return await self.begin_login(email, password)

    # ── Two-factor authentication ───────────────────────────────────────────
    async def get_2fa_context(self, html: str | None = None) -> TwoFactorContext:
        if html is None:
            resp = await self._request("GET", TWO_FA_URL, allow_redirects=True)
            html = await self._read_text(resp)

        methods = _parse_2fa_context(html)
        if not methods:
            _LOGGER.debug("Digi 2FA HTML first 1500 chars: %s", html[:1500])
            raise DigiTwoFactorRequired("Could not parse 2FA page")

        return TwoFactorContext(methods=methods, html=html)






    async def send_2fa_code(
        self,
        context: TwoFactorContext,
        method: str,
        target_value: str | None = None,
    ) -> None:
        selected = context.methods.get(method)
        if not selected:
            raise DigiTwoFactorError(f"2FA method '{method}' is not available")

        payload = dict(selected["send_payload"])

        target_key: str | None = None
        target_options = selected.get("target_options") or []
        default_target = selected.get("default_target")

        if method == "sms" and (target_options or default_target):
            target_key = "phone"

        if target_key:
            resolved_target = (target_value or default_target or "").strip()
            if target_options and not resolved_target:
                if len(target_options) == 1:
                    resolved_target = str(target_options[0].get("value") or "").strip()
                else:
                    raise DigiTwoFactorError("2FA target selection is required")

            if target_options:
                allowed_values = {
                    str(option.get("value") or "").strip() for option in target_options
                }
                if resolved_target not in allowed_values:
                    raise DigiTwoFactorError("Invalid 2FA target selected")

            if not resolved_target:
                raise DigiTwoFactorError("2FA target could not be determined")

            payload[target_key] = resolved_target
            context.selections[method] = resolved_target
        elif target_value:
            raise DigiTwoFactorError(
                "Selected 2FA target is not supported for this method"
            )

        resp = await self._request(
            "POST",
            selected["send_url"],
            data=payload,
            allow_redirects=True,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        text = await self._read_text(resp)

        if resp.status >= 400:
            raise DigiTwoFactorError(f"Failed to send code: HTTP {resp.status}")

        if text and "error" in text.lower():
            _LOGGER.debug("Digi send 2FA response: %s", text[:400])

    async def validate_2fa_code(
        self, context: TwoFactorContext, method: str, code: str
    ) -> tuple[str, str]:
        selected = context.methods.get(method)
        if not selected:
            raise DigiTwoFactorError(f"2FA method '{method}' is not available")

        payload = dict(selected["validate_payload"])
        chosen_target = context.selections.get(method) or selected.get("default_target")
        if method == "sms" and chosen_target:
            payload["phone"] = chosen_target
        payload["code"] = code.strip()

        resp = await self._request(
            "POST",
            TWO_FA_VALIDATE_URL,
            data=payload,
            allow_redirects=True,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        raw = await self._read_text(resp)

        data: dict[str, Any] = {}
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            pass

        if resp.status >= 400:
            raise DigiTwoFactorError(f"Failed to validate code: HTTP {resp.status}")

        if data and not data.get("success", True):
            raise DigiTwoFactorError(data.get("message") or "Invalid verification code")

        follow = await self._request("GET", ADDRESS_SELECT_URL, allow_redirects=True)
        html = await self._read_text(follow)
        return str(follow.url), html

    # ── Address / account selection ─────────────────────────────────────────
    async def get_address_options(self, html: str | None = None) -> list[AddressOption]:
        if html is None:
            resp = await self._request("GET", ADDRESS_SELECT_URL, allow_redirects=True)
            html = await self._read_text(resp)

        options: list[AddressOption] = _extract_radio_options(html)

        if not options:
            for _, label in RE_ADDRESS_OPTION.findall(html):
                clean = _clean_text(label)
                if clean and clean.lower() != "toate adresele":
                    options.append(AddressOption(value="", label=clean))

        return options

    async def confirm_address(self, address_id: str) -> None:
        payload = {"address": address_id, "order-btn-id": ""}
        resp = await self._request(
            "POST",
            ADDRESS_CONFIRM_URL,
            data=payload,
            allow_redirects=True,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        text = await self._read_text(resp)

        if resp.status >= 400:
            raise DigiAccountSelectionRequired(
                f"Address confirmation failed: HTTP {resp.status}"
            )

        if text:
            try:
                data = json.loads(text)
                if not data.get("success", True):
                    raise DigiAccountSelectionRequired(
                        data.get("message") or "Address confirmation failed"
                    )
            except json.JSONDecodeError:
                pass

    async def async_fetch_address_map(self) -> dict[str, str]:
        """Read the {address-id: label} map from the my-services dropdown.

        Multi-address accounts get this from the login address-select page, but
        single-address accounts never see it — so for them we read the one
        address-id from the `my-services-address-select` dropdown on the
        my-services page. Returns {} if the dropdown is absent, in which case a
        hash of the address text is used instead.
        """
        try:
            resp = await self._request("GET", MY_SERVICES_URL, allow_redirects=True)
            html = await self._read_text(resp)
        except aiohttp.ClientError:
            return {}
        if resp.status != 200:
            return {}
        options = _extract_select_options(
            unescape(html), "my-services-address-select"
        )
        return {option.value: option.label for option in options if option.value}

    async def async_fetch_internet(self, address_id: str) -> dict[str, Any] | None:
        """Read internet-service details (IP, plan) for an address-id.

        Returns None for addresses without an internet (FiberLink) service.
        """
        try:
            resp = await self._request(
                "POST",
                FIBERLINK_URL,
                data={"address-id": address_id},
                allow_redirects=True,
                headers={
                    "Referer": MY_SERVICES_URL,
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
            )
            html = await self._read_text(resp)
        except aiohttp.ClientError:
            return None
        if resp.status != 200:
            return None
        return _parse_internet(html)


    async def async_fetch_connection_logs(
        self,
        address_id: str,
        username: str,
        date_from: str,
        date_to: str,
    ) -> list[ConnectionSession]:
        """Fetch FiberLink connection (PPPoE) sessions for a date range.

        ``date_from``/``date_to`` are ``YYYY-MM-DD`` (the format the datepicker
        actually submits, despite its ``dd/mm/yyyy`` placeholder). Returns the
        sessions newest-first, or an empty list on error / no data.
        """
        try:
            resp = await self._request(
                "POST",
                CONNECTION_LOGS_URL,
                data={
                    "address-id": address_id,
                    "username": username,
                    "date-from": date_from,
                    "date-to": date_to,
                },
                allow_redirects=True,
                headers={
                    "Referer": FIBERLINK_URL,
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
            )
            html = await self._read_text(resp)
        except aiohttp.ClientError:
            return []
        if resp.status != 200 or "popup-content-error" in html:
            return []
        return _parse_connection_logs(html)




    async def async_fetch_client_code(self) -> str | None:
        """Read the Digi client code ("Cod client") from the account page."""
        try:
            resp = await self._request(
                "GET", ACCOUNT_DETAILS_URL, allow_redirects=True
            )
            html = await self._read_text(resp)
        except aiohttp.ClientError:
            return None
        if resp.status != 200:
            return None
        match = RE_CLIENT_CODE.search(unescape(html))
        return match.group(1) if match else None

    # ── Data fetch ──────────────────────────────────────────────────────────
    async def _load_invoice_page(self, attempts: int = 3) -> str:
        """Load the invoices page, retrying transient empty responses.

        Digi occasionally answers with an empty ``204 No Content`` page (its
        bot protection) before serving the real page. Retrying a couple of
        times keeps a transient hiccup from failing setup outright.
        """
        last_status = 0
        for attempt in range(attempts):
            resp = await self._request("GET", INVOICES_URL, allow_redirects=True)
            html = await self._read_text(resp)
            final_url = str(resp.url)

            if (
                "/auth/login" in final_url
                or "/auth/2fa" in final_url
                or "/auth/address-select" in final_url
            ):
                raise DigiReauthRequired("Session expired")

            if resp.status != 204 and html.strip():
                return html

            last_status = resp.status
            _LOGGER.debug(
                "Digi invoices page empty (HTTP %s), attempt %s/%s",
                resp.status,
                attempt + 1,
                attempts,
            )
            if attempt < attempts - 1:
                await asyncio.sleep(1.5)

        raise DigiError(
            f"Digi returned an empty page (HTTP {last_status}); "
            "the request may have been blocked. It will retry automatically."
        )

    def _select_detail_ids(
        self, recent_rows: list[InvoiceSummary], cached_ids: set[str]
    ) -> set[str]:
        """Decide which invoice details to fetch this poll.

        - Current/unpaid invoices: always (their remaining balance changes).
        - The latest invoice per address: needed for the service breakdown,
          unless it is a paid invoice already in the cache.
        - Older paid invoices: not fetched at all — their page-row data is used.
        """
        by_address: dict[str, list[InvoiceSummary]] = {}
        for row in recent_rows:
            by_address.setdefault(row.address_key, []).append(row)

        needed: set[str] = set()
        for bucket in by_address.values():
            latest = max(
                bucket, key=lambda r: sort_key(r.issue_date)
            )
            for row in bucket:
                if row.is_current or row is latest and row.invoice_id not in cached_ids:
                    needed.add(row.invoice_id)
        return needed

    async def async_fetch_data(
        self,
        history_limit: int = 6,
        detail_cache: dict[str, InvoiceDetail] | None = None,
    ) -> DigiData:
        html = await self._load_invoice_page()

        parsed = _parse_invoice_page(html)
        if not parsed["rows"]:
            raise DigiError("No invoices found in Digi page")

        rows: list[InvoiceSummary] = parsed["rows"]
        cache = detail_cache if detail_cache is not None else {}

        # Keep the most-recent `history_limit` rows per address (current first).
        recent_rows: list[InvoiceSummary] = []
        per_address: dict[str, int] = {}
        for row in rows:
            count = per_address.get(row.address_key, 0)
            if count < history_limit:
                recent_rows.append(row)
                per_address[row.address_key] = count + 1

        # Fetch only what's needed (current + uncached latest); reuse the rest.
        to_fetch = self._select_detail_ids(recent_rows, set(cache))
        fetched: dict[str, InvoiceDetail] = {}
        for invoice_id in to_fetch:
            fetched[invoice_id] = await self._fetch_invoice_details(invoice_id)
            await asyncio.sleep(0.15)

        def _detail_for(row: InvoiceSummary) -> InvoiceDetail:
            if row.invoice_id in fetched:
                detail = fetched[row.invoice_id]
                if not row.is_current:
                    cache[row.invoice_id] = detail  # paid details are immutable
                return detail
            if row.invoice_id in cache:
                return cache[row.invoice_id]
            # An older paid invoice we deliberately skipped: use the page row.
            return InvoiceDetail(
                invoice_id=row.invoice_id,
                invoice_number=row.invoice_id,
                issue_date=row.issue_date,
                due_date=row.due_date,
                total=row.amount,
                rest=0.0,
                status="Achitată",
                pdf_url=None,
                services=[],
            )

        invoices_by_address: dict[str, AddressInvoices] = {}
        grouped: dict[str, list[dict[str, Any]]] = {}

        for row in recent_rows:
            detail = _detail_for(row)
            item = {
                "invoice_id": row.invoice_id,
                "address": row.address,
                "issue_date": detail.issue_date or row.issue_date,
                "due_date": detail.due_date or row.due_date,
                "description": row.description,
                "amount": detail.total if detail.total is not None else row.amount,
                "rest": detail.rest if detail.rest is not None else 0.0,
                "status": detail.status,
                "invoice_number": detail.invoice_number,
                "pdf_url": detail.pdf_url,
                "services": detail.services,
            }
            grouped.setdefault(row.address_key, []).append(item)

        # Keep the cache bounded to invoices still shown on the page.
        if detail_cache is not None:
            visible = {row.invoice_id for row in recent_rows}
            for invoice_id in list(cache):
                if invoice_id not in visible:
                    del cache[invoice_id]

        for address_key, items in grouped.items():
            items.sort(
                key=lambda x: sort_key(x.get("issue_date")),
                reverse=True,
            )
            latest = items[0]
            unpaid_count = sum(
                1
                for item in items
                if (item.get("rest") or 0) > 0
                or "neach" in (item.get("status") or "").lower()
            )

            invoices_by_address[address_key] = AddressInvoices(
                address_key=address_key,
                address=latest["address"],
                latest=latest,
                history=items,
                unpaid_count=unpaid_count,
            )

        return DigiData(
            account_label=None,
            account_id=None,
            invoices_by_address=invoices_by_address,
            last_update=datetime.now(UTC),
            needs_reauth=False,
        )

    # ── Page parsing ────────────────────────────────────────────────────────

    async def _fetch_invoice_details(self, invoice_id: str) -> InvoiceDetail:
        payload = {
            "url": f"/my-account/invoices/details?invoice_id={invoice_id}",
            "id": invoice_id,
        }
        resp = await self._request(
            "POST",
            f"{BASE_URL}/my-account/invoices/details?invoice_id={invoice_id}",
            data=payload,
            allow_redirects=True,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        html = await self._read_text(resp)

        if resp.status >= 400:
            raise DigiError(
                f"Failed to fetch invoice details for {invoice_id}: HTTP {resp.status}"
            )

        return _parse_invoice_detail(html, invoice_id)


    # ── Helpers ─────────────────────────────────────────────────────────────



