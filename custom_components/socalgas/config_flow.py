"""Config flow for SoCal Gas integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import FileSelector, FileSelectorConfig

from .const import CONF_ACCOUNT_NAME, CONF_UPLOADED_FILE, DOMAIN
from .green_button_parser import parse_green_button_zip
from .statistics import async_import_to_ha, readings_to_hourly_statistics

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCOUNT_NAME, default="Home"): str,
    }
)

STEP_UPLOAD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_UPLOADED_FILE): FileSelector(
            FileSelectorConfig(accept=".zip,application/zip")
        ),
    }
)


class SoCalGasConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SoCal Gas."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow handler."""
        return SoCalGasOptionsFlow(config_entry)

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._account_name: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the account name step."""
        if user_input is not None:
            self._account_name = user_input[CONF_ACCOUNT_NAME]
            return await self.async_step_upload()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
        )

    async def async_step_upload(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the file upload step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                file_id = user_input[CONF_UPLOADED_FILE]
                readings, summary = await self.hass.async_add_executor_job(
                    self._parse_upload, file_id
                )
                if not readings:
                    errors["base"] = "no_data"
                else:
                    # Import statistics before creating entry
                    # Use account name slug for stable statistic IDs
                    stats = readings_to_hourly_statistics(readings)
                    name_slug = self._account_name.lower().replace(" ", "_")
                    await async_import_to_ha(self.hass, stats, name_slug)
                    _LOGGER.info(
                        "Imported %d readings for %s",
                        len(readings),
                        self._account_name,
                    )
                    return self.async_create_entry(
                        title=self._account_name,
                        data={
                            CONF_ACCOUNT_NAME: self._account_name,
                            "reading_count": len(readings),
                        },
                    )
            except Exception as err:
                _LOGGER.exception("Failed to parse uploaded file")
                errors["base"] = "invalid_file"

        return self.async_show_form(
            step_id="upload",
            data_schema=STEP_UPLOAD_SCHEMA,
            errors=errors,
        )

    def _parse_upload(self, file_id: str):
        """Parse an uploaded Green Button ZIP file."""
        with process_uploaded_file(self.hass, file_id) as file_path:
            return parse_green_button_zip(file_path)


class SoCalGasOptionsFlow(OptionsFlow):
    """Handle options for SoCal Gas."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the options step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                file_id = user_input[CONF_UPLOADED_FILE]
                readings, summary = await self.hass.async_add_executor_job(
                    self._parse_upload, file_id
                )
                if not readings:
                    errors["base"] = "no_data"
                else:
                    stats = readings_to_hourly_statistics(readings)
                    name_slug = self.config_entry.data.get(
                        CONF_ACCOUNT_NAME, "home"
                    ).lower().replace(" ", "_")
                    await async_import_to_ha(
                        self.hass, stats, name_slug
                    )
                    _LOGGER.info(
                        "Re-imported %d readings",
                        len(readings),
                    )
                    return self.async_create_entry(data={})
            except Exception as err:
                _LOGGER.error("Failed to parse uploaded file: %s", err)
                errors["base"] = "invalid_file"

        return self.async_show_form(
            step_id="init",
            data_schema=STEP_UPLOAD_SCHEMA,
            errors=errors,
        )

    def _parse_upload(self, file_id: str):
        """Parse an uploaded Green Button ZIP file."""
        with process_uploaded_file(self.hass, file_id) as file_path:
            return parse_green_button_zip(file_path)
