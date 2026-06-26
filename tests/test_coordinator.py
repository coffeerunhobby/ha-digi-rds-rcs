"""Tests for the Digi coordinator helpers and snapshot transformation."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from custom_components.digi.const import CONF_ADDRESS_MAP  # noqa: E402
from custom_components.digi.coordinator import (  # noqa: E402
    DigiCoordinator,
    _normalize_address,
    _parse_date,
    _services_count,
    _service_label,
    _slugify,
)
from custom_components.digi.models import AddressInvoices, DigiData  # noqa: E402


# Synthetic data only. The login labels are lowercase with a county suffix; the
# invoices-page text is title-cased and shorter — mirroring the real format
# difference the resolver must bridge.
_ADDRESS_MAP = {
    "11112222": "Strada Exemplu nr. 10, bl. A1, sc. B, ap. 14, Oras, județul Exemplu",
    "33334444": "Strada Exemplu nr. 10, bl. A1, sc. B, ap. 19, Oras, județul Exemplu",
}


def test_normalize_address():
    assert _normalize_address("Strada Exemplu, Nr. 10, Ap. 14") == "stradaexemplunr10ap14"
    assert _normalize_address("Strada Țării") == "stradatarii"


def test_resolve_address_id_matches_numeric_id():
    coord = DigiCoordinator.__new__(DigiCoordinator)
    coord.config_entry = SimpleNamespace(data={CONF_ADDRESS_MAP: _ADDRESS_MAP})
    # Invoice-page text (different formatting) → numeric address-id.
    assert (
        coord._resolve_address_id("Strada Exemplu, Nr. 10, Bl. A1, Sc. B, Ap. 14")
        == "11112222"
    )
    assert (
        coord._resolve_address_id("Strada Exemplu, Nr. 10, Bl. A1, Sc. B, Ap. 19")
        == "33334444"
    )


def test_resolve_address_id_without_map():
    coord = DigiCoordinator.__new__(DigiCoordinator)
    coord.config_entry = SimpleNamespace(data={})
    assert coord._resolve_address_id("Strada Exemplu, Nr. 10, Ap. 14") is None


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

    # One row per address (services are aggregated within the address).
    assert len(snapshot["addresses"]) == 1
    assert "totals" not in snapshot

    addr = snapshot["addresses"][0]
    assert addr["address"] == "Strada Exemplu 10"
    # Identity is the md5 hash of the address text, not the address itself.
    assert addr["address_unique"] == hashlib.md5(b"Strada Exemplu 10").hexdigest()[:12]
    assert addr["service_label"] == "Internet & TV"
    # Latest invoice (sorted by issue date desc) drives the headline values.
    assert addr["amount"] == 120.0
    assert addr["services_count"] == 2
    # Only the unpaid invoice contributes to the outstanding balance.
    assert addr["rest"] == 120.0
    assert addr["has_arrears"] is True
    assert addr["unpaid_count"] == 1


def test_build_snapshot_single_address_direct_maps_id():
    coord = DigiCoordinator.__new__(DigiCoordinator)
    # One invoice address + one known id: map directly even if the label does not
    # substring-match (single-address accounts).
    coord.config_entry = SimpleNamespace(
        data={CONF_ADDRESS_MAP: {"99990000": "A differently-formatted label"}}
    )
    snapshot = coord._build_snapshot(_make_digi_data())
    assert snapshot["addresses"][0]["address_unique"] == "99990000"


def test_build_snapshot_no_arrears_when_all_paid():
    coord = DigiCoordinator.__new__(DigiCoordinator)
    coord.config_entry = SimpleNamespace(data={})

    data = _make_digi_data()
    # Mark every invoice as paid.
    for item in data.invoices_by_address["address-1"].history:
        item["rest"] = 0.0
        item["status"] = "Achitată"

    snapshot = coord._build_snapshot(data)
    addr = snapshot["addresses"][0]
    assert addr["rest"] == 0.0
    assert addr["has_arrears"] is False
