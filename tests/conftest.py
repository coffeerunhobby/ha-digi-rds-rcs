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

    @pytest.fixture(scope="session", autouse=True)
    def _warm_aiohttp_shutdown_thread():
        """Pre-spawn aiohttp's singleton ``_run_safe_shutdown_loop`` daemon thread.

        aiohttp starts that thread lazily on the first ``ClientSession`` and reuses
        it thereafter. Creating one session up front means the thread already exists
        before phacc's per-test ``verify_cleanup`` snapshot, so the standalone
        aiohttp test (UA pinning) doesn't trip its lingering-thread check on newer
        aiohttp/Home Assistant.
        """
        import asyncio

        import aiohttp

        async def _warm() -> None:
            session = aiohttp.ClientSession()
            await session.close()

        asyncio.run(_warm())
        yield

    @pytest.fixture(autouse=True)
    def _auto_enable_custom_integrations(request):
        """Allow Home Assistant to load the ``digi`` custom integration.

        Only for tests that actually use ``hass``. Pure-logic tests must not spin
        up Home Assistant — its background threads trip phacc's cleanup check, and
        setting up ``hass`` eagerly conflicts with the recorder fixtures (which
        must initialise before ``hass``).
        """
        if "hass" in request.fixturenames:
            request.getfixturevalue("enable_custom_integrations")
        yield
