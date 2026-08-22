"""Diagnostics must never disclose credentials or personal data.

A diagnostics download is the artefact users attach to issue reports, so this is
the one output where a redaction gap is actively harmful. These tests assert on
the *rendered* result rather than on the redaction constants, so removing a name
from the set fails here even if the code still compiles — which is exactly how a
regression slipped through once before.

All values below are SYNTHETIC.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME  # noqa: E402

from custom_components.digi.diagnostics import (  # noqa: E402
    TO_REDACT_DATA,
    async_get_config_entry_diagnostics,
)

# Distinctive markers: if any of these reach the output, redaction failed.
SECRETS = {
    CONF_USERNAME: "canary-user@example.com",
    CONF_PASSWORD: "canary-password-value",
    "cookies": [{"key": "session", "value": "canary-cookie-value"}],
    "client_code": "canary-client-code",
    "address_map": {"1234567890": "canary-address-label"},
}


def _entry() -> SimpleNamespace:
    return SimpleNamespace(
        title="Digi — canary-user@example.com",
        entry_id="entry_one",
        data={**SECRETS, "update_interval": 6, "history_limit": 6},
        runtime_data=SimpleNamespace(
            last_update_success=True,
            data={
                "addresses": [
                    {
                        "address_unique": "ab12cd34ef56",
                        "address": "canary-street-address",
                        "address_id": "canary-address-id",
                        "invoice_number": "canary-invoice-number",
                        "pdf_url": "https://example.com/canary-pdf",
                        "rest": 12.0,
                        "internet": {"ipv4": "203.0.113.7", "ipv6": ["2001:db8::/64"]},
                        "connection": {
                            "current_ip": "203.0.113.7",
                            "current_mac": "AA:BB:CC:DD:EE:FF",
                            "sessions": [
                                {"ip": "203.0.113.7", "mac": "AA:BB:CC:DD:EE:FF"}
                            ],
                        },
                    }
                ]
            },
        ),
    )


async def _render(hass=None) -> str:
    result = await async_get_config_entry_diagnostics(hass, _entry())
    return json.dumps(result, default=str)


async def test_credentials_never_appear_in_diagnostics():
    rendered = await _render()
    assert "canary-user@example.com" not in rendered
    assert "canary-password-value" not in rendered


async def test_session_and_account_identifiers_are_redacted():
    rendered = await _render()
    for marker in (
        "canary-cookie-value",
        "canary-client-code",
        "canary-address-label",
        "canary-address-id",
        "canary-street-address",
        "canary-invoice-number",
        "canary-pdf",
    ):
        assert marker not in rendered, f"{marker} leaked into diagnostics"


async def test_network_identifiers_are_redacted():
    # IP and MAC identify the line and are mildly sensitive.
    rendered = await _render()
    assert "203.0.113.7" not in rendered
    assert "AA:BB:CC:DD:EE:FF" not in rendered


async def test_diagnostics_still_carry_useful_non_sensitive_context():
    # Redaction must not hollow the report out — it has to stay useful.
    result = await async_get_config_entry_diagnostics(None, _entry())
    assert result["coordinator"]["last_update_success"] is True
    assert "update_interval" in result["entry"]["data"]


def test_credentials_are_listed_in_the_redaction_set():
    # Guards the specific regression: a refactor once stripped these two names
    # from the set while leaving the module importable.
    assert CONF_USERNAME in TO_REDACT_DATA
    assert CONF_PASSWORD in TO_REDACT_DATA
