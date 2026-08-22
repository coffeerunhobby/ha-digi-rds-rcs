"""Parsing for the HTML Digi serves on its customer portal.

Split out of the API client so the two concerns stay separable: everything here
is pure — HTML in, structured data out — with no network access and no state.
That makes the regexes directly testable, and keeps the client focused on
transport, authentication and orchestration.

Digi's markup changes without notice, so parsing is deliberately forgiving:
unrecognised input yields None or an empty result rather than raising, and the
caller decides whether that is a problem.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin

from .const import BASE_URL, TWO_FA_SEND_URL
from .models import (
    AddressOption,
    ConnectionSession,
    InvoiceDetail,
    InvoiceSummary,
    TwoFactorOption,
)

_LOGGER = logging.getLogger(__name__)


# ── HTML parsing patterns ───────────────────────────────────────────────────
RE_INPUT_TAG = re.compile(r"<input[^>]*>", re.I | re.S)
RE_LABEL_FOR = re.compile(
    r'<label[^>]+for=["\']([^"\']+)["\'][^>]*>(.*?)</label>',
    re.I | re.S,
)
RE_ADDRESS_OPTION = re.compile(
    r'<option[^>]+id=["\'](address-[^"\']+)["\'][^>]*>(.*?)</option>',
    re.I | re.S,
)
RE_SCRIPT_CFG = re.compile(
    r'<script[^>]+id=["\']client-invoices-cfg["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)

RE_ROW = re.compile(
    r'<div class=["\']my-account-tbl-row["\'][^>]*data-invoice-address=["\']([^"\']+)["\'][^>]*>\s*'
    r'<div class=["\']my-account-tbl-col date["\']>\s*(.*?)\s*</div>\s*'
    r'<div class=["\']my-account-tbl-col description["\']>\s*(.*?)\s*<span>\s*(.*?)\s*</span>\s*</div>\s*'
    r'<div class=["\']my-account-tbl-col amount["\']>\s*(.*?)\s*</div>',
    re.I | re.S,
)

RE_CURRENT_ROW = re.compile(
    r'<div class=["\']my-account-tbl-row["\'][^>]*data-invoice-address=["\']([^"\']+)["\'][^>]*>\s*'
    r'<div class=["\']my-account-tbl-col select check["\']>\s*'
    r'<button[^>]*data-invoices-id=["\'](\d+)["\'][^>]*>.*?</button>\s*</div>\s*'
    r'<div class=["\']my-account-tbl-col date["\']>\s*(.*?)\s*</div>\s*'
    r'<div class=["\']my-account-tbl-col description["\']>\s*(.*?)\s*<span>\s*(.*?)\s*</span>\s*</div>\s*'
    r'<div class=["\']my-account-tbl-col amount["\']>\s*(.*?)\s*</div>',
    re.I | re.S,
)

RE_DETAILS_TITLE = re.compile(
    r"Factura\s+([^<]+?)\s+din data de\s+([0-9.\-/]+)",
    re.I | re.S,
)
RE_PDF = re.compile(
    r'href=["\']([^"\']*?/my-account/invoices/pdf-download[^"\']+)["\']',
    re.I,
)
# Invoice service rows. Digi renders each billed line as an <h5> header whose
# text is the service name (prefixed with a hierarchical index such as "1.1"),
# followed by a <span> holding the price, e.g.:
#   <h5 class="popup-content-item-header">1.2 Ab. Internet ...
#       <span class="popup-content-item-price">40.67 LEI</span></h5>
RE_SERVICE_ROW = re.compile(
    r'<h5[^>]*class=["\']popup-content-item-header["\'][^>]*>\s*(.*?)'
    r'<span[^>]*class=["\']popup-content-item-price["\'][^>]*>\s*(.*?)\s*</span>',
    re.I | re.S,
)
# Leading hierarchical index, e.g. "1 ", "1.2 ", "1.10.3 ".
RE_SERVICE_INDEX = re.compile(r"^\d+(?:\.\d+)*\s+")
# A sub-item index ("1.1", not the umbrella "1") marks an actual billed service.
RE_SERVICE_LEAF = re.compile(r"^\d+\.\d+")
RE_HEX32 = re.compile(r"\b[a-f0-9]{32}\b", re.I)
RE_PHONE_PARAM = re.compile(
    r"(?:phone|form-phone-number-confirm|phone-number-confirm)[^a-f0-9]{0,40}([a-f0-9]{32})",
    re.I | re.S,
)
RE_SELECT_BLOCK = re.compile(
    r'<select[^>]*(?:id|name)=["\']([^"\']+)["\'][^>]*>(.*?)</select>',
    re.I | re.S,
)
RE_OPTION_TAG = re.compile(
    r'<option[^>]*value=["\']([^"\']*)["\'][^>]*>(.*?)</option>',
    re.I | re.S,
)
RE_LABEL_VALUE_MONEY = re.compile(
    r">\s*(Total|Rest)\s*<.*?>\s*([0-9]+(?:(?:[.,]|&period;)[0-9]{2})?)\s*LEI",
    re.I | re.S,
)
RE_LABEL_VALUE_TEXT = re.compile(
    r">\s*Status\s*<.*?>\s*([^<]+)",
    re.I | re.S,
)
# Account-details page: "<strong>Cod client: </strong>123456".
RE_CLIENT_CODE = re.compile(r"Cod\s*client[^0-9]{0,40}(\d{3,})", re.I | re.S)


def _re_fiberlink_field(label: str) -> re.Pattern[str]:
    """Match a fiberlink label/value pair: <strong>LABEL</strong>…<div…><p>VALUE</p>."""
    return re.compile(
        r"<strong>\s*" + label + r"\s*</strong>\s*</p>\s*</div>\s*"
        r"<div[^>]*>\s*<p>\s*(.*?)\s*</p>",
        re.I | re.S,
    )


RE_FIBERLINK_IPV4 = _re_fiberlink_field(r"Adresa\s*IPV4")
RE_FIBERLINK_IPV6 = _re_fiberlink_field(r"Adresa\s*IPV6")
# Plan name, e.g. "DIGI Net Business Acces internet 1000 (24 luni)".
RE_FIBERLINK_PLAN = re.compile(r'mb-20["\']>\s*([^<]+?)\s*</div>', re.I | re.S)
# The FiberLink username (e.g. "ABCDV000000001") needed to query connection
# logs; it sits on the "Vizualizare loguri conectare" link as data-user.
RE_FIBERLINK_USER = re.compile(
    r'data-user=["\']([^"\']+)["\'][^>]*data-action=["\']netFiberlinkLogs["\']',
    re.I | re.S,
)

# Connection-logs table: each session is a row of cells tagged by data-thead.
RE_LOG_CELL = re.compile(r'data-thead=["\']([^"\']+)["\']>\s*(.*?)\s*</div>', re.I | re.S)
# Byte units as rendered by Digi ("196.36 GB", "0.00 B", "1.72 TB").
_LOG_BYTE_UNITS: dict[str, int] = {
    "B": 1,
    "KB": 1024,
    "MB": 1024**2,
    "GB": 1024**3,
    "TB": 1024**4,
}


def _parse_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    pattern = r'(\w+(?:-\w+)*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))'
    for key, value1, value2, value3 in re.findall(pattern, tag, re.I):
        attrs[key.lower()] = value1 or value2 or value3 or ""
    return attrs


def _extract_hidden_inputs(html: str) -> dict[str, str]:
    hidden: dict[str, str] = {}
    for tag in RE_INPUT_TAG.findall(html):
        attrs = _parse_attrs(tag)
        if attrs.get("type", "").lower() != "hidden":
            continue
        name = attrs.get("name")
        if name:
            hidden[name] = attrs.get("value", "")
    return hidden


def _extract_select_options(
    html: str, *candidate_names: str
) -> list[TwoFactorOption]:
    candidates = {name.lower() for name in candidate_names if name}
    options: list[TwoFactorOption] = []

    for select_name, select_body in RE_SELECT_BLOCK.findall(html):
        name_l = select_name.lower()
        if candidates and name_l not in candidates:
            continue

        for value, label_html in RE_OPTION_TAG.findall(select_body):
            clean_value = (value or "").strip()
            clean_label = _clean_text(label_html)
            if not clean_value or not clean_label:
                continue
            options.append(TwoFactorOption(value=clean_value, label=clean_label))

    deduped: list[TwoFactorOption] = []
    seen: set[tuple[str, str]] = set()
    for option in options:
        key = (option.value, option.label)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)

    return deduped


def _extract_radio_options(html: str) -> list[AddressOption]:
    labels = {key: _clean_text(val) for key, val in RE_LABEL_FOR.findall(html)}
    options: list[AddressOption] = []

    for tag in RE_INPUT_TAG.findall(html):
        attrs = _parse_attrs(tag)
        if attrs.get("type", "").lower() != "radio":
            continue
        input_id = attrs.get("id", "")
        value = attrs.get("value", "")
        label = labels.get(input_id, "")
        if value and label:
            options.append(AddressOption(value=value, label=label))

    return options


def _parse_2fa_context(html: str) -> dict[str, dict[str, Any]]:
    methods: dict[str, dict[str, Any]] = {}
    hidden = _extract_hidden_inputs(html)
    html_lower = html.lower()

    phone_value: str | None = None

    for key in (
        "form-phone-number-confirm",
        "phone",
        "phone-number-confirm",
        "form_phone_number_confirm",
    ):
        value = hidden.get(key)
        if value and RE_HEX32.fullmatch(value):
            phone_value = value
            break

    if not phone_value:
        for key, value in hidden.items():
            key_l = key.lower()
            if ("phone" in key_l or "telefon" in key_l) and value and RE_HEX32.fullmatch(value):
                phone_value = value
                break

    if not phone_value:
        match = RE_PHONE_PARAM.search(html)
        if match:
            phone_value = match.group(1)

    sms_candidates = _extract_select_options(
        html,
        "form-my-account-2fa-send-phone",
        "phone",
        "phone-number-confirm",
        "form-phone-number-confirm",
    )

    sms_markers = (
        "trimite sms" in html_lower
        or "codul primit prin sms" in html_lower
        or "cod de siguranță prin sms" in html_lower
        or "cod de siguranta prin sms" in html_lower
    )

    if not phone_value and sms_markers:
        tokens = list(dict.fromkeys(RE_HEX32.findall(html)))
        if len(tokens) == 1:
            phone_value = tokens[0]

    if phone_value or sms_candidates:
        sms_method: dict[str, Any] = {
            "send_url": TWO_FA_SEND_URL,
            "send_payload": {"action": "myAccount2FASend"},
            "validate_payload": {"action": "myAccount2FAVerify"},
        }
        if phone_value:
            sms_method["default_target"] = phone_value
        if sms_candidates:
            sms_method["target_options"] = [
                {"value": option.value, "label": option.label}
                for option in sms_candidates
            ]
        methods["sms"] = sms_method
    elif sms_markers:
        _LOGGER.debug(
            "Digi 2FA page looks like SMS flow but phone target was not found. "
            "Hidden keys: %s",
            list(hidden.keys()),
        )

    email_candidates = {
        key: value
        for key, value in hidden.items()
        if ("mail" in key.lower() or "email" in key.lower()) and value
    }
    if email_candidates:
        key, value = next(iter(email_candidates.items()))
        methods["email"] = {
            "send_url": TWO_FA_SEND_URL,
            "send_payload": {"action": "myAccount2FASend", key: value},
            "validate_payload": {"action": "myAccount2FAVerify", key: value},
        }

    _LOGGER.debug(
        "Digi 2FA parse: methods=%s hidden_keys=%s",
        list(methods.keys()),
        list(hidden.keys()),
    )

    return methods


def _parse_internet(html: str) -> dict[str, Any] | None:
    # NB: match the FiberLink username on the *raw* html (the data-user
    # attribute is not HTML-escaped) before unescaping the rest.
    user_match = RE_FIBERLINK_USER.search(html)
    html = unescape(html)
    ipv4_match = RE_FIBERLINK_IPV4.search(html)
    if ipv4_match is None:
        return None  # no internet service at this address

    # The account code ("Cont:") is intentionally not collected — it is a
    # customer identifier we never use. The FiberLink username *is* kept,
    # but only because it is required to query the connection logs; it is
    # redacted from diagnostics and never surfaced as a sensor attribute.
    plan_match = RE_FIBERLINK_PLAN.search(html)
    return {
        "ipv4": _clean_text(ipv4_match.group(1)),
        "ipv6": [_clean_text(v) for v in RE_FIBERLINK_IPV6.findall(html)],
        "plan": _clean_text(plan_match.group(1)) if plan_match else None,
        "username": _clean_text(user_match.group(1)) if user_match else None,
    }


def _parse_connection_logs(html: str) -> list[ConnectionSession]:
    sessions: list[ConnectionSession] = []
    # Each session is a ``tbl-row`` block (the header row has no data-thead
    # cells, so it is naturally skipped).
    for block in re.split(r'<div class=["\']tbl-row', html):
        if "data-thead" not in block:
            continue
        cells = {
            _clean_text(label).lower(): value
            for label, value in RE_LOG_CELL.findall(block)
        }
        if not cells:
            continue
        duration = cells.get("durată") or cells.get("durata")
        sessions.append(
            ConnectionSession(
                connect=_parse_log_datetime(cells.get("conectare")),
                disconnect=_parse_log_datetime(cells.get("deconectare")),
                duration=_clean_text(duration) if duration else None,
                ip=_clean_text(cells.get("ip", "")) or None,
                mac=_clean_text(cells.get("mac", "")) or None,
                download_bytes=_parse_bytes(cells.get("download")),
                upload_bytes=_parse_bytes(cells.get("upload")),
            )
        )
    return sessions


def _parse_log_datetime(text: str | None) -> str | None:
    """Normalise a log timestamp to a naive ISO string, or None."""
    if not text:
        return None
    clean = re.sub(r"\s+", " ", unescape(text)).strip()
    try:
        return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S").isoformat()
    except ValueError:
        return None


def _parse_bytes(text: str | None) -> int | None:
    """Parse a Digi byte amount like '196.36 GB' / '0.00 B' into bytes."""
    if not text:
        return None
    clean = re.sub(r"\s+", " ", unescape(text)).strip()
    match = re.match(r"([0-9]+(?:[.,][0-9]+)?)\s*([KMGT]?B)\b", clean, re.I)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return int(round(value * _LOG_BYTE_UNITS[match.group(2).upper()]))


def _parse_invoice_page(html: str) -> dict[str, Any]:
    addresses: dict[str, str] = {
        key: _clean_text(label)
        for key, label in RE_ADDRESS_OPTION.findall(html)
    }

    rows: list[InvoiceSummary] = []

    current_html = _extract_section(html, "Facturi curente", "Facturi achitate")
    current_invoice_ids: list[str] = []
    if current_html:
        for (
            address_key,
            invoice_id,
            issue_date,
            description,
            due_date,
            amount_text,
        ) in RE_CURRENT_ROW.findall(current_html):
            current_invoice_ids.append(str(invoice_id))
            rows.append(
                InvoiceSummary(
                    invoice_id=str(invoice_id),
                    address_key=address_key,
                    address=addresses.get(
                        address_key,
                        address_key.replace("address-", "").replace("_", " "),
                    ),
                    issue_date=_clean_text(issue_date),
                    due_date=_clean_text(due_date),
                    description=_clean_text(description),
                    amount=_parse_money(amount_text),
                    is_current=True,
                )
            )

    archive_html = _extract_section(html, "Facturi achitate", None)

    cfg_match = RE_SCRIPT_CFG.search(html)
    archive_ids: list[str] = []
    if cfg_match:
        try:
            cfg = json.loads(unescape(cfg_match.group(1).strip()))
            all_ids = [str(item["id"]) for item in cfg if item.get("id")]

            # The client-invoices-cfg payload lists both current and paid
            # invoices. Remove the current invoice ids first so the first
            # paid invoice does not wrongly inherit a current invoice id.
            current_ids_remaining = list(current_invoice_ids)
            for invoice_id in all_ids:
                if invoice_id in current_ids_remaining:
                    current_ids_remaining.remove(invoice_id)
                    continue
                archive_ids.append(invoice_id)
        except json.JSONDecodeError as err:
            raise DigiError("Invalid invoice config JSON") from err

    archive_matches = list(RE_ROW.findall(archive_html if archive_html else html))

    for idx, match in enumerate(archive_matches):
        if idx >= len(archive_ids):
            break

        address_key, issue_date, description, due_date, amount_text = match
        rows.append(
            InvoiceSummary(
                invoice_id=archive_ids[idx],
                address_key=address_key,
                address=addresses.get(
                    address_key,
                    address_key.replace("address-", "").replace("_", " "),
                ),
                issue_date=_clean_text(issue_date),
                due_date=_clean_text(due_date),
                description=_clean_text(description),
                amount=_parse_money(amount_text),
            )
        )

    if not rows:
        _LOGGER.debug("Digi invoices page parsed but no rows found")

    return {"rows": rows, "addresses": addresses}


def _parse_invoice_detail(html: str, invoice_id: str) -> InvoiceDetail:
    html_unescaped = unescape(html)
    title_match = RE_DETAILS_TITLE.search(html_unescaped)
    pdf_match = RE_PDF.search(html_unescaped)

    money_map = {
        _clean_text(label).lower(): _parse_money(value)
        for label, value in RE_LABEL_VALUE_MONEY.findall(html)
    }

    status_match = RE_LABEL_VALUE_TEXT.search(html_unescaped)

    # Collect every line, then prefer the leaf services (indexed "1.1",
    # "1.2", …) over the umbrella group total (indexed "1"). If the invoice
    # has no hierarchy, fall back to all rows.
    raw_rows: list[tuple[str, str]] = []
    for raw_name, raw_price in RE_SERVICE_ROW.findall(html_unescaped):
        name = _clean_text(raw_name)
        price_text = _clean_text(raw_price)
        if name:
            raw_rows.append((name, price_text))

    leaf_rows = [row for row in raw_rows if RE_SERVICE_LEAF.match(row[0])]
    chosen_rows = leaf_rows or raw_rows

    services = [
        {
            "name": RE_SERVICE_INDEX.sub("", name).strip() or name,
            "amount": _parse_money(price_text),
            "raw_amount": price_text,
        }
        for name, price_text in chosen_rows
    ]

    invoice_number = None
    issue_date = None
    if title_match:
        invoice_number = _clean_text(title_match.group(1))
        issue_date = _clean_text(title_match.group(2))

    if not invoice_number:
        invoice_number = invoice_id

    return InvoiceDetail(
        invoice_id=invoice_id,
        invoice_number=invoice_number,
        issue_date=issue_date,
        due_date=None,
        total=money_map.get("total"),
        rest=money_map.get("rest"),
        status=_clean_text(status_match.group(1)) if status_match else None,
        pdf_url=urljoin(BASE_URL, unescape(pdf_match.group(1))) if pdf_match else None,
        services=services,
    )


def _parse_money(text: str | None) -> float | None:
    if text is None:
        return None

    clean = unescape(text).strip()
    clean = re.sub(r"[^0-9,.\-]", "", clean)

    if not clean:
        return None

    if "," in clean and "." in clean:
        if clean.rfind(",") > clean.rfind("."):
            clean = clean.replace(".", "").replace(",", ".")
        else:
            clean = clean.replace(",", "")
    elif "," in clean:
        clean = clean.replace(",", ".")
    elif "." in clean:
        pass
    else:
        try:
            return int(clean) / 100
        except ValueError:
            return None

    try:
        return float(clean)
    except ValueError:
        return None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _extract_section(html: str, start_marker: str, end_marker: str | None) -> str:
    start_idx = html.find(start_marker)
    if start_idx == -1:
        return ""

    sliced = html[start_idx:]
    if end_marker:
        end_idx = sliced.find(end_marker)
        if end_idx != -1:
            return sliced[:end_idx]
    return sliced
