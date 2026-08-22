"""Data models for the Digi (RCS & RDS) integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NotRequired, TypedDict


@dataclass(slots=True)
class InvoiceSummary:
    """A single invoice row scraped from the invoices listing page."""

    invoice_id: str
    address_key: str
    address: str
    issue_date: str
    due_date: str
    description: str
    amount: float
    # True for the "Facturi curente" section (current/unpaid), False for the
    # "Facturi achitate" archive — decides which invoice details to fetch.
    is_current: bool = False


@dataclass(slots=True)
class InvoiceDetail:
    """Detailed invoice data fetched from the invoice details endpoint."""

    invoice_id: str
    invoice_number: str | None
    issue_date: str | None
    due_date: str | None
    total: float | None
    rest: float | None
    status: str | None
    pdf_url: str | None
    services: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class AddressInvoices:
    """All invoices grouped under a single Digi address."""

    address_key: str
    address: str
    latest: dict[str, Any]
    history: list[dict[str, Any]]
    unpaid_count: int


@dataclass(slots=True)
class ConnectionSession:
    """A single FiberLink connection (PPPoE) session from the logs page.

    Timestamps are naive ISO strings in Digi's local time (Europe/Bucharest);
    ``connect`` is the stable de-duplication key. ``disconnect`` may be None for
    a session that is still open (not yet seen in practice — the log only lists
    completed sessions — but tolerated by the model).
    """

    connect: str | None
    disconnect: str | None
    duration: str | None
    ip: str | None
    mac: str | None
    download_bytes: int | None
    upload_bytes: int | None


@dataclass(slots=True)
class DigiData:
    """Raw payload returned by the API client after a fetch."""

    account_label: str | None
    account_id: str | None
    invoices_by_address: dict[str, AddressInvoices]
    last_update: datetime
    needs_reauth: bool = False


# ── Auth-flow value objects ─────────────────────────────
@dataclass(slots=True)
class TwoFactorOption:
    value: str
    label: str


@dataclass(slots=True)
class TwoFactorContext:
    methods: dict[str, dict[str, Any]]
    html: str
    selections: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class AddressOption:
    value: str
    label: str


# ── Coordinator snapshot ────────────────────────────────────────────────────
# TypedDict rather than a dataclass on purpose: this is what the coordinator
# hands to the entities, and every sensor reads it with ``.get(...)``. A
# TypedDict documents and type-checks the shape while remaining an ordinary
# dict at runtime, so nothing about the data flow changes.
class AddressSnapshot(TypedDict):
    """One address as the entities see it."""

    # Identity. ``address_unique`` is the real Digi address-id when known and a
    # hash of the address text otherwise; it is what entity_ids are keyed on.
    address_unique: str
    address_id: str | None
    address_key: str
    address: str
    service_label: str

    # Money. ``rest`` is the outstanding balance, ``amount`` the latest invoice.
    rest: float
    amount: float

    # The latest invoice, plus the next deadline that actually matters.
    issue_date: str | None
    due_date: str | None
    next_due_date: str | None
    invoice_number: str | None
    status: str | None
    pdf_url: str | None

    unpaid_count: int
    has_arrears: bool
    services_count: int
    services: list[dict[str, Any]]
    latest: dict[str, Any]
    history: list[dict[str, Any]]

    # Added later in the poll, and only for addresses that have the service.
    internet: NotRequired[dict[str, Any]]
    connection: NotRequired[dict[str, Any]]


class DigiSnapshot(TypedDict):
    """The full coordinator payload for one Digi account."""

    account_id: str
    addresses: list[AddressSnapshot]
    needs_reauth: bool
    last_update: str | None
