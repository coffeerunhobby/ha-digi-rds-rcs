"""Data models for the Digi (RCS & RDS) integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


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
class DigiData:
    """Raw payload returned by the API client after a fetch."""

    account_label: str | None
    account_id: str | None
    invoices_by_address: dict[str, AddressInvoices]
    last_update: datetime
    needs_reauth: bool = False
