"""Shared pytest configuration for the Digi integration tests.

The pure HTML-parsing tests (``test_api_parsing``) have no Home Assistant
dependency and always run. The coordinator / config-flow tests require Home
Assistant and the ``pytest-homeassistant-custom-component`` plugin; they are
skipped automatically where that stack is unavailable (e.g. a dev machine
without a C toolchain to build HA's native deps).
"""

from __future__ import annotations

import pytest

try:  # pragma: no cover - environment dependent
    import pytest_homeassistant_custom_component  # noqa: F401

    pytest_plugins = "pytest_homeassistant_custom_component"
    HA_AVAILABLE = True
except ImportError:  # pragma: no cover - environment dependent
    HA_AVAILABLE = False


if HA_AVAILABLE:

    @pytest.fixture(autouse=True)
    def _auto_enable_custom_integrations(enable_custom_integrations):
        """Allow Home Assistant to load the ``digi`` custom integration."""
        yield
