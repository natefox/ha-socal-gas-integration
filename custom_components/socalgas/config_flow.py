"""Config flow for SoCal Gas integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    DateSelector,
    DateSelectorConfig,
    FileSelector,
    FileSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import SoCalGasAPI, SoCalGasAuthError, SoCalGasConnectionError
from .const import (
    CONF_ACCOUNT_NAME,
    CONF_ACCOUNT_NUMBER,
    CONF_BROWSERLESS_URL,
    CONF_LOOKBACK_DAYS,
    CONF_METER_NUMBER,
    CONF_PASSWORD,
    CONF_REFRESH_INTERVAL_HOURS,
    CONF_UPLOADED_FILE,
    CONF_USERNAME,
    DEFAULT_BROWSERLESS_URL,
    DEFAULT_REFRESH_INTERVAL_HOURS,
    DOMAIN,
)
from .green_button_parser import parse_green_button_zip
from .statistics import (
    async_get_existing_states,
    async_get_prior_sums,
    async_import_to_ha,
    merge_readings_with_existing,
    readings_to_hourly_statistics,
)

_LOGGER = logging.getLogger(__name__)

STEP_CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(
            CONF_BROWSERLESS_URL, default=DEFAULT_BROWSERLESS_URL
        ): str,
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

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow handler."""
        return SoCalGasOptionsFlow(config_entry)

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._account_name: str = ""
        self._username: str = ""
        self._password: str = ""
        self._browserless_url: str = DEFAULT_BROWSERLESS_URL
        self._account_number: str = ""
        self._meter_number: str = ""
        self._api: SoCalGasAPI | None = None
        self._lookback_days: int = 365
        self._accounts: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — show menu to choose login or upload."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["credentials", "upload"],
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the credentials step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            self._browserless_url = user_input.get(
                CONF_BROWSERLESS_URL, DEFAULT_BROWSERLESS_URL
            )

            api = SoCalGasAPI(
                self._username,
                self._password,
                browserless_url=self._browserless_url,
            )
            try:
                account_info = await api.authenticate()
                self._account_number = account_info.account_number
                self._meter_number = account_info.meter_number
                self._api = api

                return await self.async_step_account_name()
            except SoCalGasAuthError as err:
                _LOGGER.warning("Authentication failed: %s", err)
                errors["base"] = "invalid_auth"
                await api.close()
            except SoCalGasConnectionError as err:
                _LOGGER.warning("Connection error: %s", err)
                errors["base"] = "cannot_connect"
                await api.close()

        return self.async_show_form(
            step_id="credentials",
            data_schema=STEP_CREDENTIALS_SCHEMA,
            errors=errors,
        )

    async def async_step_account_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle account naming after successful login."""
        if user_input is not None:
            self._account_name = user_input[CONF_ACCOUNT_NAME]
            return await self.async_step_lookback()

        # Build a default name from the account number
        default_name = f"Gas {self._account_number[-4:]}" if self._account_number else "Home"

        return self.async_show_form(
            step_id="account_name",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCOUNT_NAME, default=default_name): str,
                }
            ),
            description_placeholders={
                "account_number": self._account_number,
                "meter_number": self._meter_number,
            },
        )

    async def async_step_lookback(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask how far back to import historical data."""
        if user_input is not None:
            self._lookback_days = int(user_input[CONF_LOOKBACK_DAYS])
            return await self.async_step_finish()

        return self.async_show_form(
            step_id="lookback",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOOKBACK_DAYS, default=365): NumberSelector(
                        NumberSelectorConfig(
                            min=3,
                            max=730,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="days",
                        )
                    ),
                }
            ),
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show confirmation and create the entry."""
        if user_input is not None:
            # Stash the already-authenticated API so the coordinator can
            # reuse it for the first refresh instead of re-authenticating
            # (back-to-back logins trigger SoCal Gas rate limiting).
            if self._api:
                self.hass.data.setdefault(DOMAIN, {})
                self.hass.data[DOMAIN]["pending_api"] = self._api

            return self.async_create_entry(
                title=self._account_name,
                data={
                    CONF_ACCOUNT_NAME: self._account_name,
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_BROWSERLESS_URL: self._browserless_url,
                    CONF_ACCOUNT_NUMBER: self._account_number,
                    CONF_METER_NUMBER: self._meter_number,
                    CONF_LOOKBACK_DAYS: self._lookback_days,
                },
            )

        return self.async_show_form(
            step_id="finish",
            data_schema=vol.Schema({}),
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
                    return await self.async_step_upload_name(
                        reading_count=len(readings),
                        readings=readings,
                    )
            except Exception:
                _LOGGER.exception("Failed to parse uploaded file")
                errors["base"] = "invalid_file"

        return self.async_show_form(
            step_id="upload",
            data_schema=STEP_UPLOAD_SCHEMA,
            errors=errors,
        )

    async def async_step_upload_name(
        self,
        user_input: dict[str, Any] | None = None,
        reading_count: int = 0,
        readings: list | None = None,
    ) -> ConfigFlowResult:
        """Name the account after file upload."""
        if readings is not None:
            self._upload_readings = readings
            self._upload_reading_count = reading_count

        if user_input is not None:
            self._account_name = user_input[CONF_ACCOUNT_NAME]
            stats = readings_to_hourly_statistics(self._upload_readings)
            name_slug = self._account_name.lower().replace(" ", "_")
            await async_import_to_ha(self.hass, stats, name_slug)
            _LOGGER.info(
                "Imported %d readings for %s",
                self._upload_reading_count,
                self._account_name,
            )
            return self.async_create_entry(
                title=self._account_name,
                data={
                    CONF_ACCOUNT_NAME: self._account_name,
                    "reading_count": self._upload_reading_count,
                },
            )

        return self.async_show_form(
            step_id="upload_name",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCOUNT_NAME, default="Home"): str,
                }
            ),
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when credentials expire."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle re-authentication confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            reauth_entry = self._get_reauth_entry()
            browserless_url = user_input.get(
                CONF_BROWSERLESS_URL,
                reauth_entry.data.get(CONF_BROWSERLESS_URL, DEFAULT_BROWSERLESS_URL),
            )
            api = SoCalGasAPI(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                browserless_url=browserless_url,
            )
            try:
                account_info = await api.authenticate()
                await api.close()

                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={
                        **reauth_entry.data,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_BROWSERLESS_URL: browserless_url,
                        CONF_ACCOUNT_NUMBER: account_info.account_number,
                        CONF_METER_NUMBER: account_info.meter_number,
                    },
                )
            except SoCalGasAuthError:
                errors["base"] = "invalid_auth"
            except SoCalGasConnectionError:
                errors["base"] = "cannot_connect"
            finally:
                await api.close()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_CREDENTIALS_SCHEMA,
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
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show menu with available options."""
        menu_options = ["upload", "settings"]
        # Only show re-download for credential-based entries
        if self._entry.data.get(CONF_USERNAME):
            menu_options.insert(0, "redownload")
        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
        )

    async def async_step_redownload(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle re-download of a date range."""
        from datetime import date, datetime, timedelta, timezone

        errors: dict[str, str] = {}

        if user_input is not None:
            start_date = date.fromisoformat(user_input["start_date"])
            end_date = date.fromisoformat(user_input["end_date"])
            earliest = date.today() - timedelta(days=730)

            if start_date < earliest:
                errors["start_date"] = "date_too_old"
            elif end_date < start_date:
                errors["end_date"] = "end_before_start"
            else:
                start = datetime.combine(
                    start_date, datetime.min.time(), tzinfo=timezone.utc
                )
                end = datetime.combine(
                    end_date, datetime.min.time(), tzinfo=timezone.utc
                )
                coordinator = self.hass.data[DOMAIN].get(self._entry.entry_id)
                if coordinator:
                    self._entry.async_create_background_task(
                        self.hass,
                        coordinator.async_redownload_range(start, end),
                        f"{DOMAIN}_redownload_{self._entry.entry_id}",
                    )
                return self.async_abort(reason="redownload_started")

        today = date.today()
        earliest = today - timedelta(days=730)
        default_start = (today - timedelta(days=30)).isoformat()
        default_end = today.isoformat()

        return self.async_show_form(
            step_id="redownload",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "start_date", default=default_start
                    ): DateSelector(DateSelectorConfig()),
                    vol.Required(
                        "end_date", default=default_end
                    ): DateSelector(DateSelectorConfig()),
                }
            ),
            errors=errors,
            description_placeholders={
                "earliest_date": earliest.strftime("%B %-d, %Y"),
            },
        )

    async def async_step_upload(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle ZIP file upload."""
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
                    name_slug = self._entry.data.get(
                        CONF_ACCOUNT_NAME, "home"
                    ).lower().replace(" ", "_")
                    earliest_reading = min(r.start for r in readings)
                    # Merge with existing data so sums stay consistent
                    existing = await async_get_existing_states(
                        self.hass, name_slug, earliest_reading
                    )
                    merged = merge_readings_with_existing(
                        readings, existing
                    )
                    prior_usage, prior_cost = await async_get_prior_sums(
                        self.hass, name_slug, merged[0].start
                    )
                    stats = readings_to_hourly_statistics(
                        merged, prior_usage, prior_cost
                    )
                    await async_import_to_ha(
                        self.hass, stats, name_slug
                    )
                    _LOGGER.info(
                        "Re-imported %d readings (%d merged with existing)",
                        len(readings), len(merged),
                    )
                    return self.async_create_entry(data=self._entry.options)
            except Exception as err:
                _LOGGER.error("Failed to parse uploaded file: %s", err)
                errors["base"] = "invalid_file"

        return self.async_show_form(
            step_id="upload",
            data_schema=STEP_UPLOAD_SCHEMA,
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle settings changes."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **self._entry.options,
                    CONF_REFRESH_INTERVAL_HOURS: user_input[
                        CONF_REFRESH_INTERVAL_HOURS
                    ],
                }
            )

        current_interval = self._entry.options.get(
            CONF_REFRESH_INTERVAL_HOURS, DEFAULT_REFRESH_INTERVAL_HOURS
        )
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_REFRESH_INTERVAL_HOURS,
                        default=current_interval,
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=168,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="hours",
                        )
                    ),
                }
            ),
        )

    def _parse_upload(self, file_id: str):
        """Parse an uploaded Green Button ZIP file."""
        with process_uploaded_file(self.hass, file_id) as file_path:
            return parse_green_button_zip(file_path)
