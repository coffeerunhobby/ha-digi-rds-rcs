"""Tests for the Digi config and options flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

from custom_components.digi.api import (  # noqa: E402
    DigiAuthError,
    TwoFactorContext,
)
from custom_components.digi.const import (  # noqa: E402
    CONF_HISTORY_LIMIT,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DOMAIN,
)

_COOKIES = [
    {
        "key": "session",
        "value": "abc",
        "domain": "www.digi.ro",
        "path": "/",
        "secure": True,
        "expires": "",
    }
]

_USER_INPUT = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_UPDATE_INTERVAL: 6,
    CONF_HISTORY_LIMIT: 6,
}


def _mock_api(**overrides) -> MagicMock:
    api = MagicMock()
    api.login = AsyncMock(return_value=("https://www.digi.ro/", "<html></html>"))
    api.get_2fa_context = AsyncMock()
    api.send_2fa_code = AsyncMock()
    api.validate_2fa_code = AsyncMock()
    api.get_address_options = AsyncMock(return_value=[])
    api.confirm_address = AsyncMock()
    api.export_cookies = MagicMock(return_value=_COOKIES)
    api.close = AsyncMock()
    for key, value in overrides.items():
        setattr(api, key, value)
    return api


async def test_user_flow_success_without_2fa(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.digi.config_flow.DigiApiClient",
        return_value=_mock_api(),
    ), patch("custom_components.digi.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_USERNAME] == "user@example.com"
    assert result["data"]["cookies"] == _COOKIES
    assert result["result"].unique_id == "user@example.com"


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    api = _mock_api(login=AsyncMock(side_effect=DigiAuthError("bad")))
    with patch(
        "custom_components.digi.config_flow.DigiApiClient", return_value=api
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_with_2fa(hass: HomeAssistant) -> None:
    two_factor = TwoFactorContext(
        methods={
            "sms": {
                "send_url": "https://www.digi.ro/api-post-2fa-send-code",
                "send_payload": {"action": "myAccount2FASend"},
                "validate_payload": {"action": "myAccount2FAVerify"},
            }
        },
        html="",
    )
    api = _mock_api(
        login=AsyncMock(
            return_value=("https://www.digi.ro/auth/2fa?redirectTo=/", "<html></html>")
        ),
        get_2fa_context=AsyncMock(return_value=two_factor),
        validate_2fa_code=AsyncMock(return_value=("https://www.digi.ro/", "")),
    )

    with patch(
        "custom_components.digi.config_flow.DigiApiClient", return_value=api
    ), patch("custom_components.digi.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "twofa_method"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"twofa_method": "sms"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "twofa_code"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"twofa_code": "123456"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    api.send_2fa_code.assert_awaited_once()
    api.validate_2fa_code.assert_awaited_once()


async def test_second_distinct_account_creates_separate_entry(
    hass: HomeAssistant,
) -> None:
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={**_USER_INPUT, "cookies": _COOKIES},
        unique_id="user@example.com",
    )
    existing.add_to_hass(hass)

    with patch(
        "custom_components.digi.config_flow.DigiApiClient",
        return_value=_mock_api(),
    ), patch("custom_components.digi.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**_USER_INPUT, CONF_USERNAME: "other@example.com"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "other@example.com"
    # Each account keeps its own cookie jar in its own entry.
    assert result["result"].data["cookies"] == _COOKIES
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_duplicate_account_is_rejected(hass: HomeAssistant) -> None:
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={**_USER_INPUT, "cookies": _COOKIES},
        unique_id="user@example.com",
    )
    existing.add_to_hass(hass)

    with patch(
        "custom_components.digi.config_flow.DigiApiClient",
        return_value=_mock_api(),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _USER_INPUT
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_updates_settings(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**_USER_INPUT, "cookies": _COOKIES},
        unique_id="user@example.com",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_UPDATE_INTERVAL: 12, CONF_HISTORY_LIMIT: 10},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_UPDATE_INTERVAL] == 12
    assert entry.data[CONF_HISTORY_LIMIT] == 10
