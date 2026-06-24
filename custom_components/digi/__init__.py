"""The Digi (RCS & RDS) integration."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import DigiConfigEntry, DigiCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: DigiConfigEntry) -> bool:
    """Set up Digi from a config entry."""
    coordinator = DigiCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DigiConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and (coordinator := entry.runtime_data) is not None:
        await coordinator.async_shutdown()
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: DigiConfigEntry) -> None:
    """Reload the entry only when tunable settings change (not on cookie writes)."""
    coordinator = entry.runtime_data
    if coordinator is None or coordinator.settings_changed():
        await hass.config_entries.async_reload(entry.entry_id)
