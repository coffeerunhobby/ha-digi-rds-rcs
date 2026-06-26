"""Config and options flow for the Digi (RCS & RDS) integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    AddressOption,
    DigiAccountSelectionRequired,
    DigiApiClient,
    DigiAuthError,
    DigiTwoFactorError,
    DigiTwoFactorRequired,
    TwoFactorContext,
)
from .const import (
    CONF_2FA_CODE,
    CONF_2FA_METHOD,
    CONF_2FA_TARGET,
    CONF_ADDRESS_MAP,
    CONF_CLIENT_CODE,
    CONF_COOKIES,
    CONF_HISTORY_LIMIT,
    CONF_PASSWORD,
    CONF_SELECTED_ACCOUNT_ID,
    CONF_SELECTED_ACCOUNT_LABEL,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
    MAX_HISTORY_LIMIT,
    MAX_UPDATE_INTERVAL_HOURS,
    MIN_HISTORY_LIMIT,
    MIN_UPDATE_INTERVAL_HOURS,
)

_LOGGER = logging.getLogger(__name__)


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=defaults.get(
                    CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_HOURS
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_UPDATE_INTERVAL_HOURS,
                    max=MAX_UPDATE_INTERVAL_HOURS,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="h",
                )
            ),
            vol.Required(
                CONF_HISTORY_LIMIT,
                default=defaults.get(CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_HISTORY_LIMIT,
                    max=MAX_HISTORY_LIMIT,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


class DigiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Digi config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._api: DigiApiClient | None = None
        self._pending: dict[str, Any] = {}
        self._two_factor: TwoFactorContext | None = None
        self._address_options: list[AddressOption] = []
        self._reauth_entry_data: dict[str, Any] | None = None

    # ── Entry point ─────────────────────────────────────────────────────────
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._pending = {
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                CONF_HISTORY_LIMIT: int(user_input[CONF_HISTORY_LIMIT]),
            }
            return await self._async_start_login(errors)

        return self.async_show_form(
            step_id="user", data_schema=_user_schema(), errors=errors
        )

    async def _async_start_login(self, errors: dict[str, str]) -> ConfigFlowResult:
        self._api = DigiApiClient(async_get_clientsession(self.hass))

        try:
            final_url, html = await self._api.login(
                self._pending[CONF_USERNAME], self._pending[CONF_PASSWORD]
            )
        except DigiAuthError:
            errors["base"] = "invalid_auth"
            return self.async_show_form(
                step_id="user",
                data_schema=_user_schema(self._pending),
                errors=errors,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during Digi login")
            errors["base"] = "unknown"
            return self.async_show_form(
                step_id="user",
                data_schema=_user_schema(self._pending),
                errors=errors,
            )

        return await self._async_handle_login_result(final_url, html)

    async def _async_handle_login_result(
        self, final_url: str, html: str
    ) -> ConfigFlowResult:
        assert self._api is not None

        if "/auth/2fa" in final_url:
            try:
                self._two_factor = await self._api.get_2fa_context(html)
            except DigiTwoFactorRequired:
                return self.async_show_form(
                    step_id="user",
                    data_schema=_user_schema(self._pending),
                    errors={"base": "twofa_unavailable"},
                )
            return await self.async_step_twofa_method()

        if "/auth/address-select" in final_url:
            # Every address on the account appears as a device regardless of the
            # choice, so just confirm the first one to get past Digi's mandatory
            # address-select page — no user prompt. Fall back to a manual choice
            # only if that fails.
            self._address_options = await self._api.get_address_options(html)
            target = next((o for o in self._address_options if o.value), None)
            if target is not None:
                try:
                    await self._api.confirm_address(target.value)
                except DigiAccountSelectionRequired:
                    return await self.async_step_select_account()

        return await self._async_finish()

    # ── 2FA: choose method / target ─────────────────────────────────────────
    def _target_options(self, method: str) -> list[dict[str, str]]:
        if self._two_factor is None:
            return []
        selected = self._two_factor.methods.get(method) or {}
        result: list[dict[str, str]] = []
        for option in selected.get("target_options") or []:
            value = str(option.get("value") or "").strip()
            label = str(option.get("label") or value).strip()
            if value:
                result.append({"value": value, "label": label})
        return result

    async def async_step_twofa_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if self._two_factor is None or self._api is None:
            return await self.async_step_user()

        available = list(self._two_factor.methods.keys())
        if not available:
            return self.async_show_form(
                step_id="user",
                data_schema=_user_schema(self._pending),
                errors={"base": "twofa_unavailable"},
            )

        current_method = "sms" if "sms" in available else available[0]

        if user_input is not None:
            current_method = str(user_input[CONF_2FA_METHOD])
            target = str(user_input.get(CONF_2FA_TARGET, "") or "").strip()
            try:
                await self._api.send_2fa_code(
                    self._two_factor, current_method, target or None
                )
                self._pending[CONF_2FA_METHOD] = current_method
                if target:
                    self._pending[CONF_2FA_TARGET] = target
                else:
                    self._pending.pop(CONF_2FA_TARGET, None)
                return await self.async_step_twofa_code()
            except DigiTwoFactorError:
                errors["base"] = "twofa_send_failed"

        targets = self._target_options(current_method)
        schema: dict[Any, Any] = {
            vol.Required(CONF_2FA_METHOD, default=current_method): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        {"value": value, "label": value.upper()} for value in available
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        }
        if current_method == "sms" and len(targets) > 1:
            default_target = str(
                (user_input or {}).get(CONF_2FA_TARGET)
                or self._pending.get(CONF_2FA_TARGET)
                or targets[0]["value"]
            )
            schema[
                vol.Required(CONF_2FA_TARGET, default=default_target)
            ] = SelectSelector(
                SelectSelectorConfig(options=targets, mode=SelectSelectorMode.DROPDOWN)
            )

        return self.async_show_form(
            step_id="twofa_method",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_twofa_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if self._two_factor is None or self._api is None:
            return await self.async_step_user()

        if user_input is not None:
            try:
                final_url, html = await self._api.validate_2fa_code(
                    self._two_factor,
                    self._pending[CONF_2FA_METHOD],
                    str(user_input[CONF_2FA_CODE]),
                )
                return await self._async_handle_login_result(final_url, html)
            except DigiTwoFactorError:
                errors["base"] = "invalid_code"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating Digi 2FA code")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="twofa_code",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_2FA_CODE): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    )
                }
            ),
            errors=errors,
        )

    # ── Account / address selection ─────────────────────────────────────────
    async def async_step_select_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if not self._address_options or self._api is None:
            return await self._async_finish()

        if user_input is not None:
            account_id = str(user_input[CONF_SELECTED_ACCOUNT_ID])
            try:
                await self._api.confirm_address(account_id)
                selected = next(
                    (o for o in self._address_options if o.value == account_id), None
                )
                self._pending[CONF_SELECTED_ACCOUNT_ID] = account_id
                self._pending[CONF_SELECTED_ACCOUNT_LABEL] = (
                    selected.label if selected else account_id
                )
                return await self._async_finish()
            except DigiAccountSelectionRequired:
                errors["base"] = "account_selection_failed"

        return self.async_show_form(
            step_id="select_account",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SELECTED_ACCOUNT_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": o.value, "label": o.label}
                                for o in self._address_options
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    # ── Finalize ────────────────────────────────────────────────────────────
    def _build_entry_data(self) -> dict[str, Any]:
        assert self._api is not None
        return {
            CONF_USERNAME: self._pending[CONF_USERNAME],
            CONF_PASSWORD: self._pending[CONF_PASSWORD],
            CONF_UPDATE_INTERVAL: self._pending[CONF_UPDATE_INTERVAL],
            CONF_HISTORY_LIMIT: self._pending[CONF_HISTORY_LIMIT],
            CONF_CLIENT_CODE: self._pending.get(CONF_CLIENT_CODE),
            CONF_ADDRESS_MAP: self._pending.get(CONF_ADDRESS_MAP) or {},
            CONF_SELECTED_ACCOUNT_ID: self._pending.get(CONF_SELECTED_ACCOUNT_ID),
            CONF_SELECTED_ACCOUNT_LABEL: self._pending.get(
                CONF_SELECTED_ACCOUNT_LABEL
            ),
            CONF_COOKIES: self._api.export_cookies(),
        }

    async def _async_finish(self) -> ConfigFlowResult:
        assert self._api is not None
        # One entry per Digi login; all of its addresses appear as devices.
        email = self._pending[CONF_USERNAME]
        unique_id = email.lower()

        # Read the Digi client code ("Cod client") once; it prefixes entity_ids.
        try:
            self._pending[CONF_CLIENT_CODE] = await self._api.async_fetch_client_code()
        except Exception:  # noqa: BLE001 - best effort, entity_ids fall back
            _LOGGER.debug("Could not read Digi client code")

        # Read the {address-id: label} map from my-services. The dropdown is
        # present for both single- and multi-address accounts, so this gives the
        # real Digi address-ids regardless of whether the login showed a selector.
        try:
            self._pending[CONF_ADDRESS_MAP] = await self._api.async_fetch_address_map()
        except Exception:  # noqa: BLE001 - best effort, entity_ids fall back
            _LOGGER.debug("Could not read Digi address map")

        data = self._build_entry_data()

        # Re-authentication: update the existing entry instead of creating one.
        if self._reauth_entry_data is not None:
            entry = self._get_reauth_entry()
            if entry is not None:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="reauth_account_mismatch")
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, **data}
                )

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=f"Digi — {email}", data=data)

    # ── Reauth ──────────────────────────────────────────────────────────────
    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry_data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        existing = self._reauth_entry_data or (entry.data if entry else {})

        if user_input is not None:
            self._pending = {
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_UPDATE_INTERVAL: int(
                    existing.get(
                        CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_HOURS
                    )
                ),
                CONF_HISTORY_LIMIT: int(
                    existing.get(CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT)
                ),
                CONF_SELECTED_ACCOUNT_ID: existing.get(CONF_SELECTED_ACCOUNT_ID),
                CONF_SELECTED_ACCOUNT_LABEL: existing.get(
                    CONF_SELECTED_ACCOUNT_LABEL
                ),
            }
            return await self._async_start_login(errors)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=str(existing.get(CONF_USERNAME, "")),
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> DigiOptionsFlow:
        return DigiOptionsFlow()


class DigiOptionsFlow(OptionsFlow):
    """Adjust the update interval and invoice history depth."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry

        if user_input is not None:
            new_data = {
                **entry.data,
                CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                CONF_HISTORY_LIMIT: int(user_input[CONF_HISTORY_LIMIT]),
            }
            self.hass.config_entries.async_update_entry(entry, data=new_data)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_UPDATE_INTERVAL,
                        default=int(
                            entry.data.get(
                                CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_HOURS
                            )
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_UPDATE_INTERVAL_HOURS,
                            max=MAX_UPDATE_INTERVAL_HOURS,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="h",
                        )
                    ),
                    vol.Required(
                        CONF_HISTORY_LIMIT,
                        default=int(
                            entry.data.get(CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT)
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_HISTORY_LIMIT,
                            max=MAX_HISTORY_LIMIT,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
