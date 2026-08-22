"""Shared pytest configuration for the Digi integration tests.

The pure HTML-parsing tests (parser, dates, store) have no Home Assistant
dependency and always run. The rest require Home Assistant and the
``pytest-homeassistant-custom-component`` plugin; they are skipped automatically
where that stack is unavailable (e.g. a machine without a C toolchain to build
Home Assistant's native dependencies).
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
    # Pre-spawn aiohttp's singleton `_run_safe_shutdown_loop` daemon thread at
    # import time, before pytest snapshots the running threads.
    #
    # aiohttp starts that thread lazily on the first ClientSession and reuses it
    # for the rest of the process. If the first session is created inside a test
    # — which happens as soon as one actually loads the integration — the thread
    # appears mid-test and the harness reports it as a leak. Creating one session
    # here means it already exists before any test begins.
    def _warm_aiohttp_shutdown_thread() -> None:
        import asyncio

        import aiohttp

        async def _warm() -> None:
            session = aiohttp.ClientSession()
            await session.close()

        asyncio.run(_warm())

    _warm_aiohttp_shutdown_thread()

    @pytest.fixture(autouse=True)
    def _auto_enable_custom_integrations(request):
        """Let Home Assistant load the ``digi`` custom integration.

        Only for tests that use ``hass``: pure-logic tests must not spin one up,
        because its background threads trip the harness's cleanup check, and an
        eager ``hass`` conflicts with the recorder fixtures, which have to
        initialise first.

        A test that actually calls ``async_setup`` should still request
        ``enable_custom_integrations`` directly, so it is resolved before
        ``hass`` rather than alongside it.
        """
        if "hass" in request.fixturenames:
            request.getfixturevalue("enable_custom_integrations")
        yield
