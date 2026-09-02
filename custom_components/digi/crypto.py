"""At-rest encryption for the stored Digi session.

Only the session cookies are persisted, and only encrypted. The password is
never stored: the integration cannot replay it unattended (Digi accounts use
two-factor authentication), so keeping it would be pure liability. The cookies
*are* a logged-in session — whoever holds them is signed in as the account
holder without the password or a code — which is why they get this treatment.

## What this does and does not protect against

Home Assistant keeps config-entry data as plaintext JSON in
`.storage/core.config_entries`, and an integration must be able to replay the
session unattended — so the key has to live on the same machine. Anyone with
full filesystem access can therefore still recover the credentials.
**This is not a substitute for disk encryption.**

What it *does* buy is meaningful, because the realistic exposure for a home
setup is partial rather than total:

* the key is kept in a **separate** store (`.storage/digi_key`) from the
  ciphertext (`.storage/core.config_entries`), so leaking one file is not enough;
* pasting `core.config_entries` into a forum thread, an unencrypted backup of a
  single file, or a diagnostics dump no longer reveals a reusable session or
  password.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from `cryptography`, which is a hard
requirement of Home Assistant core, so this adds no external dependency.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_KEY_SECRET, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)

# Marks a value as produced by this module, so a plaintext value stored by an
# older version is still recognised and can be migrated in place.
PREFIX = "enc:v1:"


class DigiCipher:
    """Encrypts/decrypts stored credentials using a locally stored key."""

    def __init__(self, key: bytes) -> None:
        from cryptography.fernet import Fernet

        self._fernet = Fernet(key)

    @staticmethod
    def _generate_key() -> str:
        from cryptography.fernet import Fernet

        return Fernet.generate_key().decode()

    @classmethod
    async def async_load(cls, hass: HomeAssistant) -> DigiCipher:
        """Load the key from its own store, creating one on first use.

        One key serves every Digi entry on the instance. There is deliberately
        no plaintext fallback: `cryptography` is a hard requirement of Home
        Assistant core, and a session that cannot be encrypted is not stored.
        """
        store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY_SECRET, private=True
        )
        data = await store.async_load() or {}
        key = data.get("key")
        if not key:
            key = cls._generate_key()
            await store.async_save({"key": key})
            _LOGGER.debug("Generated a new %s encryption key", DOMAIN)
        return cls(key.encode())

    def encrypt(self, value: str) -> str:
        """Return ``value`` encrypted and tagged, ready to persist."""
        return PREFIX + self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        """Return the plaintext for a stored value.

        Values without the marker are returned unchanged: they were written by a
        version that stored credentials in the clear, and the caller migrates
        them. Raises :class:`ValueError` when a tagged value cannot be decrypted
        (typically a restored backup whose key file is missing).
        """
        if not is_encrypted(value):
            return value
        from cryptography.fernet import InvalidToken

        try:
            return self._fernet.decrypt(value[len(PREFIX) :].encode()).decode()
        except (InvalidToken, ValueError) as err:
            raise ValueError(
                "The stored Digi credentials could not be decrypted — the "
                "encryption key is missing or does not match. Re-authenticate "
                "to store them again."
            ) from err

    def encrypt_json(self, value: Any) -> str:
        """Encrypt a JSON-serialisable value (the cookie jar is a list)."""
        return self.encrypt(json.dumps(value, separators=(",", ":")))

    def decrypt_json(self, value: Any) -> Any:
        """Inverse of :meth:`encrypt_json`.

        A value that is not a tagged string is returned as-is — that is the
        pre-encryption representation, which the caller migrates.
        """
        if not is_encrypted(value):
            return value
        return json.loads(self.decrypt(value))


def is_encrypted(value: Any) -> bool:
    """True when ``value`` was written by this module."""
    return isinstance(value, str) and value.startswith(PREFIX)
