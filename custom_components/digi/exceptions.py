"""Exception hierarchy for the Digi integration.

Kept in its own module so both the API client and the HTML parser can raise
the same types without importing each other. Everything derives from
``DigiError``, which the coordinator catches to turn a fetch failure into an
``UpdateFailed`` rather than a traceback.
"""

from __future__ import annotations


class DigiError(Exception):
    """Base Digi exception."""


class DigiAuthError(DigiError):
    """Credentials invalid."""


class DigiTwoFactorRequired(DigiError):
    """2FA step required but could not be parsed."""


class DigiTwoFactorError(DigiError):
    """2FA validation failed."""


class DigiAccountSelectionRequired(DigiError):
    """Account / address selection is needed."""


class DigiReauthRequired(DigiError):
    """Saved session expired — re-authentication is required."""
