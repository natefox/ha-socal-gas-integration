"""The SoCal Gas integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_BROWSERLESS_URL, CONF_USERNAME, DEFAULT_BROWSERLESS_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = []


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to a new version."""
    if entry.version == 1:
        _LOGGER.debug("Migrating config entry from version 1 to 2")
        new_data = {**entry.data}
        if entry.data.get(CONF_USERNAME):
            new_data[CONF_BROWSERLESS_URL] = DEFAULT_BROWSERLESS_URL
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)
        _LOGGER.info("Migration to version 2 successful")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SoCal Gas from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Only set up coordinator if credentials are configured
    if entry.data.get(CONF_USERNAME):
        from .coordinator import SoCalGasCoordinator

        coordinator = SoCalGasCoordinator(hass, entry)
        hass.data[DOMAIN][entry.entry_id] = coordinator

        # Schedule the first refresh as a background task so the config
        # flow finishes immediately. Progress is reported via notifications.
        entry.async_create_background_task(
            hass,
            coordinator.async_request_refresh(),
            f"socalgas_initial_refresh_{entry.entry_id}",
        )

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
