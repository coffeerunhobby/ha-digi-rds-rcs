"""End-to-end setup inside a real Home Assistant instance.

The other tests exercise pieces in isolation; this one boots the integration the
way Home Assistant does — config entry, coordinator, platform forwarding — and
asserts on the entities and states that actually reach the UI. That is the level
at which a refactor either preserved behaviour or did not.

The API client is stubbed so no network is touched; the data mirrors what the
live site returns. All values are SYNTHETIC.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

from custom_components.digi.const import (  # noqa: E402
    CONF_ADDRESS_MAP,
    CONF_CLIENT_CODE,
    CONF_COOKIES,
    CONF_HISTORY_LIMIT,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)
from custom_components.digi.models import AddressInvoices, DigiData  # noqa: E402

CLIENT_CODE = "123456"
ADDRESS_ID = "11112222"


def _digi_data() -> DigiData:
    unpaid = {
        "invoice_id": "500099",
        "address": "Strada Exemplu 10, Oras",
        "issue_date": "05-07-2026",
        "due_date": "30-07-2026",
        "description": "Internet & TV",
        "amount": 120.0,
        "rest": 120.0,
        "status": "Neachitată",
        "invoice_number": "INV-99",
        "pdf_url": "https://example.com/inv-99.pdf",
        "services": [
            {"name": "Internet", "amount": 40.0},
            {"name": "TV", "amount": 80.0},
        ],
    }
    return DigiData(
        account_label=None,
        account_id=None,
        invoices_by_address={
            "address-1": AddressInvoices(
                address_key="address-1",
                address="Strada Exemplu 10, Oras",
                latest=unpaid,
                history=[unpaid],
                unpaid_count=1,
            )
        },
        last_update=datetime(2026, 7, 5, 12, 0, 0),
        needs_reauth=False,
    )


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Digi — user@example.com",
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "synthetic-password",
            CONF_COOKIES: [{"key": "session", "value": "synthetic", "domain": "x"}],
            CONF_CLIENT_CODE: CLIENT_CODE,
            CONF_ADDRESS_MAP: {ADDRESS_ID: "Strada Exemplu 10, Oras"},
            CONF_UPDATE_INTERVAL: 6,
            CONF_HISTORY_LIMIT: 6,
        },
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.digi.coordinator.DigiApiClient.async_fetch_data",
            AsyncMock(return_value=_digi_data()),
        ),
        patch(
            "custom_components.digi.coordinator.DigiApiClient.async_fetch_internet",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.digi.coordinator.DigiApiClient.export_cookies",
            return_value=[],
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_the_integration_sets_up(hass: HomeAssistant, entry: MockConfigEntry):
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED


async def test_expected_entities_are_created(hass: HomeAssistant, entry):
    await _setup(hass, entry)
    prefix = f"{DOMAIN}_{CLIENT_CODE}_{ADDRESS_ID}"
    for entity_id in (
        f"sensor.{prefix}_amount_due",
        f"sensor.{prefix}_last_invoice",
        f"sensor.{prefix}_due_date",
        f"sensor.{prefix}_due_date_timestamp",
        f"sensor.{prefix}_overdue",
        f"sensor.{prefix}_number_of_services",
        f"binary_sensor.{prefix}_overdue",
    ):
        assert hass.states.get(entity_id) is not None, f"{entity_id} was not created"


async def test_states_reflect_the_fetched_invoice(hass: HomeAssistant, entry):
    await _setup(hass, entry)
    prefix = f"{DOMAIN}_{CLIENT_CODE}_{ADDRESS_ID}"

    assert hass.states.get(f"sensor.{prefix}_amount_due").state == "120.0"
    assert hass.states.get(f"sensor.{prefix}_last_invoice").state == "120.0"
    assert hass.states.get(f"sensor.{prefix}_overdue").state == "yes"
    assert hass.states.get(f"sensor.{prefix}_number_of_services").state == "2"
    # The next deadline is the unpaid invoice's due date.
    assert hass.states.get(f"sensor.{prefix}_due_date").state == "30-07-2026"


async def test_overdue_is_a_problem_binary_sensor(hass: HomeAssistant, entry):
    await _setup(hass, entry)
    state = hass.states.get(f"binary_sensor.{DOMAIN}_{CLIENT_CODE}_{ADDRESS_ID}_overdue")
    assert state.state == "on"
    # device_class problem is what Home Assistant renders in red.
    assert state.attributes["device_class"] == "problem"


async def test_all_entities_share_one_device(hass: HomeAssistant, entry):
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    await _setup(hass, entry)
    entities = er.async_get(hass)
    devices = dr.async_get(hass)

    device_ids = {
        e.device_id
        for e in er.async_entries_for_config_entry(entities, entry.entry_id)
        if e.device_id
    }
    assert len(device_ids) == 1, "entities scattered across multiple devices"
    device = devices.async_get(next(iter(device_ids)))
    assert device.name == "Strada Exemplu 10, Oras"


async def test_the_address_never_appears_in_an_entity_id(hass, entry):
    from homeassistant.helpers import entity_registry as er

    await _setup(hass, entry)
    for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        assert "strada" not in e.entity_id.lower()
        assert "exemplu" not in e.entity_id.lower()


async def test_unload_is_clean(hass: HomeAssistant, entry: MockConfigEntry):
    await _setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_a_pre_1_0_entry_is_migrated_on_setup(hass: HomeAssistant, entry):
    """An entry written by an older version is brought up to date in place.

    The fixture stores a password and a plaintext cookie jar, the way every
    release before 1.0.0 did. Setup must drop the password (nothing ever read
    it) and encrypt the jar so nothing reusable sits in core.config_entries —
    while the jar still round-trips exactly, because a lossy migration would
    silently log the user out.
    """
    from custom_components.digi.crypto import DigiCipher, is_encrypted

    original_cookies = entry.data[CONF_COOKIES]
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert CONF_PASSWORD not in entry.data
    assert "synthetic-password" not in str(entry.data)
    assert is_encrypted(entry.data[CONF_COOKIES])
    assert "synthetic" not in entry.data[CONF_COOKIES]

    cipher = await DigiCipher.async_load(hass)
    assert cipher.decrypt_json(entry.data[CONF_COOKIES]) == original_cookies
