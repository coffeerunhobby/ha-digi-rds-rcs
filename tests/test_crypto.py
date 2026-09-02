"""Tests for at-rest encryption of the stored credentials.

Requires Home Assistant (crypto.py imports its Store helper); skipped otherwise.
"""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.digi.crypto import (  # noqa: E402
    PREFIX,
    DigiCipher,
    is_encrypted,
)

SECRET = "synthetic-password-not-real"
COOKIES = [
    {"key": "session", "value": "synthetic-session-value", "domain": "www.digi.ro"},
    {"key": "csrf", "value": "synthetic-csrf", "domain": "www.digi.ro"},
]


@pytest.fixture
def cipher() -> DigiCipher:
    return DigiCipher(DigiCipher._generate_key().encode())


def test_round_trip(cipher: DigiCipher):
    token = cipher.encrypt(SECRET)
    assert cipher.decrypt(token) == SECRET


def test_ciphertext_does_not_contain_the_secret(cipher: DigiCipher):
    token = cipher.encrypt(SECRET)
    assert SECRET not in token
    assert is_encrypted(token)


def test_encryption_is_non_deterministic(cipher: DigiCipher):
    # A random IV means an observer cannot tell that two entries share a
    # password, nor confirm a guess by comparing ciphertexts.
    assert cipher.encrypt(SECRET) != cipher.encrypt(SECRET)


def test_plaintext_passes_through_for_migration(cipher: DigiCipher):
    # Values written before encryption existed are untagged and must survive
    # unchanged so __init__ can migrate them in place.
    assert is_encrypted(SECRET) is False
    assert cipher.decrypt(SECRET) == SECRET


def test_a_different_key_cannot_decrypt(cipher: DigiCipher):
    other = DigiCipher(DigiCipher._generate_key().encode())
    with pytest.raises(ValueError):
        other.decrypt(cipher.encrypt(SECRET))


def test_tampering_is_detected(cipher: DigiCipher):
    token = cipher.encrypt(SECRET)
    tampered = PREFIX + token[len(PREFIX) : -4] + "AAAA"
    with pytest.raises(ValueError):
        cipher.decrypt(tampered)


def test_cookie_jar_round_trips_as_json(cipher: DigiCipher):
    token = cipher.encrypt_json(COOKIES)
    assert is_encrypted(token)
    # No cookie value may survive in the stored form.
    assert "synthetic-session-value" not in token
    assert cipher.decrypt_json(token) == COOKIES


def test_a_plaintext_cookie_list_passes_through_for_migration(cipher: DigiCipher):
    # Older versions stored the jar as a bare list; it must come back untouched
    # so the coordinator keeps working until __init__ rewrites it.
    assert is_encrypted(COOKIES) is False
    assert cipher.decrypt_json(COOKIES) == COOKIES


@pytest.mark.parametrize("value", [None, "", "plain", "encv1:x", [], {}])
def test_is_encrypted_rejects_untagged_values(value):
    assert is_encrypted(value) is False
