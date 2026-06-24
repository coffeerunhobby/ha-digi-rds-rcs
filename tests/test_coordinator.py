"""Tests for the Digi coordinator helpers and snapshot transformation."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from custom_components.digi.coordinator import (  # noqa: E402
    DigiCoordinator,
    _parse_date,
    _services_count,
    _service_label,
    _slugify,
)
from custom_components.digi.models import AddressInvoices, DigiData  # noqa: E402


def test_slugify():
    assert _slugify("Strada Exemplu 10, București") == "strada_exemplu_10_bucuresti"
    assert _slugify("Internet & TV") == "internet_tv"
    assert _slugify("") == "cont"


def test_parse_date():
    assert _parse_date("05-06-2026") == date(2026, 6, 5)
    assert _parse_date("05.06.2026") == date(2026, 6, 5)
    assert _parse_date("05/06/2026") == date(2026, 6, 5)
    assert _parse_date(None) is None
    assert _parse_date("not-a-date") is None


def test_service_label_fallback_is_english():
    assert _service_label(None) == "Digi services"
    assert _service_label("  ") == "Digi services"
    assert _service_label("Internet") == "Internet"


def test_services_count():
    assert _services_count({"services": [{"name": "a"}, {"name": "b"}]}) == 2
    assert _services_count({"services_count": 3}) == 3
    assert _services_count({}) == 0


def _make_digi_data() -> DigiData:
    paid = {
        "invoice_id": "500010",
        "address": "Strada Exemplu 10",
        "issue_date": "05-06-2026",
        "due_date": "30-06-2026",
        "description": "Internet & TV",
        "amount": 77.8,
        "rest": 0.0,
        "status": "Achitată",
        "invoice_number": "INV-10",
        "services": [{"name": "Internet", "amount": 40.0}],
    }
    unpaid = {
        "invoice_id": "500099",
        "address": "Strada Exemplu 10",
        "issue_date": "05-07-2026",
        "due_date": "30-07-2026",
        "description": "Internet & TV",
        "amount": 120.0,
        "rest": 120.0,
        "status": "Neachitată",
        "invoice_number": "INV-99",
        "services": [
            {"name": "Internet", "amount": 40.0},
            {"name": "TV", "amount": 80.0},
        ],
    }
    entry = AddressInvoices(
        address_key="address-1",
        address="Strada Exemplu 10",
        latest=unpaid,
        history=[paid, unpaid],
        unpaid_count=1,
    )
    return DigiData(
        account_label=None,
        account_id=None,
        invoices_by_address={"address-1": entry},
        last_update=datetime(2026, 7, 5, 12, 0, 0),
        needs_reauth=False,
    )


def test_build_snapshot():
    coord = DigiCoordinator.__new__(DigiCoordinator)
    coord.config_entry = SimpleNamespace(data={})

    snapshot = coord._build_snapshot(_make_digi_data())

    assert snapshot["account_label"] == "Digi account"
    assert len(snapshot["services"]) == 1

    svc = snapshot["services"][0]
    assert svc["address"] == "Strada Exemplu 10"
    assert svc["service_label"] == "Internet & TV"
    assert svc["account_unique"] == "digi_strada_exemplu_10_internet_tv"
    # Latest invoice (sorted by issue date desc) drives the headline values.
    assert svc["amount"] == 120.0
    assert svc["services_count"] == 2
    # Only the unpaid invoice contributes to the outstanding balance.
    assert svc["rest"] == 120.0
    assert svc["has_arrears"] is True
    assert svc["unpaid_count"] == 1

    totals = snapshot["totals"]
    assert totals["sold"] == 120.0
    assert totals["has_arrears"] is True
    assert totals["scadenta"] == "2026-07-30"
    assert totals["addresses_count"] == 1
    assert totals["numar_servicii"] == 2


def test_build_snapshot_no_arrears_when_all_paid():
    coord = DigiCoordinator.__new__(DigiCoordinator)
    coord.config_entry = SimpleNamespace(data={})

    data = _make_digi_data()
    # Mark every invoice as paid.
    for item in data.invoices_by_address["address-1"].history:
        item["rest"] = 0.0
        item["status"] = "Achitată"

    snapshot = coord._build_snapshot(data)
    svc = snapshot["services"][0]
    assert svc["rest"] == 0.0
    assert svc["has_arrears"] is False
    assert snapshot["totals"]["has_arrears"] is False
    assert snapshot["totals"]["scadenta"] is None
