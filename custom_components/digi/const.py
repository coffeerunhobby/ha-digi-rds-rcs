"""Constants for the Digi (RCS & RDS) integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "digi"

MANUFACTURER: Final = "Digi România (RCS & RDS)"
MODEL: Final = "Digi - cont online"
ATTRIBUTION: Final = "Date furnizate de www.digi.ro"

# ── Config / option keys ────────────────────────────────────────────────────
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_COOKIES: Final = "cookies"
CONF_2FA_METHOD: Final = "twofa_method"
CONF_2FA_TARGET: Final = "twofa_target"
CONF_2FA_CODE: Final = "twofa_code"
CONF_SELECTED_ACCOUNT_ID: Final = "selected_account_id"
CONF_SELECTED_ACCOUNT_LABEL: Final = "selected_account_label"
CONF_CLIENT_CODE: Final = "client_code"
CONF_ADDRESS_MAP: Final = "address_map"
CONF_HISTORY_LIMIT: Final = "history_limit"
CONF_UPDATE_INTERVAL: Final = "update_interval"

# ── Defaults / bounds ───────────────────────────────────────────────────────
DEFAULT_HISTORY_LIMIT: Final = 6
MIN_HISTORY_LIMIT: Final = 1
MAX_HISTORY_LIMIT: Final = 24

# Update interval is stored in hours in the config entry.
DEFAULT_UPDATE_INTERVAL_HOURS: Final = 6
MIN_UPDATE_INTERVAL_HOURS: Final = 1
MAX_UPDATE_INTERVAL_HOURS: Final = 24

CURRENCY_RON: Final = "RON"

# ── Digi web endpoints ──────────────────────────────────────────────────────
BASE_URL: Final = "https://www.digi.ro"
LOGIN_URL: Final = f"{BASE_URL}/auth/login?redirectTo=%2F"
TWO_FA_URL: Final = f"{BASE_URL}/auth/2fa?redirectTo=%2F"
TWO_FA_SEND_URL: Final = f"{BASE_URL}/api-post-2fa-send-code"
TWO_FA_VALIDATE_URL: Final = f"{BASE_URL}/api-post-2fa-validate-code"
ADDRESS_SELECT_URL: Final = f"{BASE_URL}/auth/address-select?redirectTo=%2F"
ADDRESS_CONFIRM_URL: Final = f"{BASE_URL}/store/address-confirm-existing"
INVOICES_URL: Final = f"{BASE_URL}/my-account/invoices"
ACCOUNT_DETAILS_URL: Final = f"{BASE_URL}/my-account/account-details"
MY_SERVICES_URL: Final = f"{BASE_URL}/my-account/my-services"

USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)
