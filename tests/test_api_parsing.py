"""Unit tests for the Digi HTML parser (no Home Assistant required)."""

from __future__ import annotations

from pathlib import Path

import aiohttp
import pytest

from ._loader import load_api

api = load_api()
DigiApiClient = api.DigiApiClient

FIX = Path(__file__).parent / "fixtures"


def _client():
    # Bypass __init__ (which needs an aiohttp session) — the parsing methods
    # only rely on static helpers and module-level regexes.
    return DigiApiClient.__new__(DigiApiClient)


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ── Money parsing ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("77,80 LEI", 77.8),
        ("1.234,56 LEI", 1234.56),
        ("1,234.56", 1234.56),
        ("0,00", 0.0),
        ("7780", 77.8),  # integer-only is treated as minor units (cents)
        ("", None),
        (None, None),
        ("LEI", None),
    ],
)
def test_parse_money(text, expected):
    assert DigiApiClient._parse_money(text) == expected


def test_clean_text_collapses_whitespace_and_entities():
    assert DigiApiClient._clean_text("  Internet &amp;   TV \n ") == "Internet & TV"


def test_client_code_regex():
    html = (
        '<p><strong>Nume: </strong>ION POPESCU</p>'
        '<p><strong>Cod client: </strong>123456</p>'
    )
    match = api.RE_CLIENT_CODE.search(html)
    assert match is not None
    assert match.group(1) == "123456"


async def test_session_pins_browser_user_agent():
    # Digi serves an empty 204 page to non-browser User-Agents. The browser UA
    # must be set at the session level so it is sent regardless of the aiohttp
    # version's per-request header behaviour (regression: empty page in HA).
    async with aiohttp.ClientSession() as base:
        client = DigiApiClient(base)
        try:
            assert client._session.headers.get("User-Agent") == api.USER_AGENT
        finally:
            await client.close()


# ── Invoice listing page ────────────────────────────────────────────────────
def test_parse_invoice_page_rows_and_ids():
    parsed = _client()._parse_invoice_page(_read("invoices_page.html"))
    rows = parsed["rows"]
    assert len(rows) == 3

    by_id = {row.invoice_id: row for row in rows}
    assert set(by_id) == {"500010", "500009", "500008"}

    current = by_id["500010"]
    assert current.address == "Strada Exemplu 10, Bucuresti"
    assert current.issue_date == "05-06-2026"
    assert current.due_date == "30-06-2026"
    assert current.amount == 77.8
    assert "Internet" in current.description

    # Archive invoice ids are matched positionally from client-invoices-cfg.
    assert by_id["500009"].amount == 77.72
    assert by_id["500008"].amount == 77.48

    # The current invoice is tagged; the archive ones are not.
    assert by_id["500010"].is_current is True
    assert by_id["500009"].is_current is False
    assert by_id["500008"].is_current is False


def _row(invoice_id, address_key, issue_date, *, is_current):
    return api.InvoiceSummary(
        invoice_id=invoice_id,
        address_key=address_key,
        address="A",
        issue_date=issue_date,
        due_date="30-07-2026",
        description="x",
        amount=10.0,
        is_current=is_current,
    )


def test_select_detail_ids_fetches_current_and_latest_only():
    client = _client()
    rows = [
        _row("100", "a", "05-07-2026", is_current=True),   # current → fetch
        _row("99", "a", "05-06-2026", is_current=False),   # old paid → skip
        _row("98", "a", "05-05-2026", is_current=False),   # old paid → skip
    ]
    assert client._select_detail_ids(rows, set()) == {"100"}


def test_select_detail_ids_latest_paid_then_cached():
    client = _client()
    rows = [
        _row("99", "a", "05-06-2026", is_current=False),   # latest paid → fetch once
        _row("98", "a", "05-05-2026", is_current=False),   # older paid → skip
    ]
    assert client._select_detail_ids(rows, set()) == {"99"}
    # Once the latest paid invoice is cached, nothing needs fetching.
    assert client._select_detail_ids(rows, {"99"}) == set()


# ── Invoice detail (current markup with hierarchical services) ──────────────
def test_parse_invoice_detail_extracts_leaf_services():
    detail = _client()._parse_invoice_detail(
        _read("invoice_detail_modern.html"), "500010"
    )

    assert detail.invoice_number == "FAKE-INV-123"
    assert detail.issue_date == "05-06-2026"
    assert detail.total == 77.8
    assert detail.rest == 0.0
    assert detail.status == "Achitată"
    assert detail.pdf_url is not None
    assert "pdf-download" in detail.pdf_url

    # The umbrella row ("1 ABONAMENTE …") is dropped in favour of the leaf
    # services ("1.1", "1.2", "1.3"), with the numeric index stripped.
    names = [s["name"] for s in detail.services]
    amounts = [s["amount"] for s in detail.services]
    assert names == [
        "Ab. Cablu TV, mentenanta, servicii accesorii",
        "Ab. Internet, mentenanta, servicii accesorii",
        "Ab. Telefonie Mobila",
    ]
    assert amounts == [26.44, 40.67, 10.69]
    assert round(sum(amounts), 2) == detail.total


def test_parse_invoice_detail_unpaid_without_services():
    detail = _client()._parse_invoice_detail(
        _read("invoice_detail_unpaid.html"), "500099"
    )
    assert detail.total == 120.0
    assert detail.rest == 120.0
    assert detail.status == "Neachitată"
    assert detail.services == []


# ── 2FA context ─────────────────────────────────────────────────────────────
def test_parse_2fa_context_detects_sms():
    methods = _client()._parse_2fa_context(_read("twofa_sms.html"))
    assert "sms" in methods
    assert methods["sms"]["default_target"] == "0123456789abcdef0123456789abcdef"
    assert methods["sms"]["send_payload"]["action"] == "myAccount2FASend"


# ── Address options ─────────────────────────────────────────────────────────
def test_extract_radio_address_options():
    options = _client()._extract_radio_options(_read("address_select.html"))
    assert [(o.value, o.label) for o in options] == [
        ("address-1", "Strada Exemplu 10, Bucuresti"),
        ("address-2", "Bulevardul Test 5, Cluj"),
    ]
