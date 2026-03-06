"""The SoCal Gas integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SoCal Gas from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Only set up coordinator if credentials are configured
    if entry.data.get(CONF_USERNAME):
        from .coordinator import SoCalGasCoordinator

        coordinator = SoCalGasCoordinator(hass, entry)
        hass.data[DOMAIN][entry.entry_id] = coordinator

        # Await the first refresh so errors surface in the HA UI.
        # If it fails, HA marks the entry as "Retrying setup" and
        # shows the error on the integration card with a retry button.
        await coordinator.async_config_entry_first_refresh()

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
