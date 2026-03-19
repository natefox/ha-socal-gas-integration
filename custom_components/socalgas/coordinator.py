"""DataUpdateCoordinator for SoCal Gas integration."""
from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from datetime import datetime, timedelta, timezone

from homeassistant.components.persistent_notification import (
    async_create,
    async_dismiss,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import SoCalGasAPI, SoCalGasAuthError, SoCalGasConnectionError
from .const import (
    CONF_BROWSERLESS_URL,
    CONF_LOOKBACK_DAYS,
    CONF_PASSWORD,
    CONF_REFRESH_INTERVAL_HOURS,
    CONF_USERNAME,
    DEFAULT_REFRESH_INTERVAL_HOURS,
    DOMAIN,
)
from .green_button_parser import parse_green_button_xml
from .statistics import (
    async_get_existing_states,
    async_get_prior_sums,
    async_import_to_ha,
    merge_readings_with_existing,
    readings_to_hourly_statistics,
)

_LOGGER = logging.getLogger(__name__)

# Green Button API returns at most ~31 days per request
CHUNK_DAYS = 30

CONF_INITIAL_IMPORT_DONE = "initial_import_done"
MAX_REFRESH_DAYS = 30


class SoCalGasCoordinator(DataUpdateCoordinator):
    """Coordinator that fetches SoCal Gas data daily."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        interval_hours = entry.options.get(
            CONF_REFRESH_INTERVAL_HOURS, DEFAULT_REFRESH_INTERVAL_HOURS
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=interval_hours),
        )
        self.entry = entry
        self._api: SoCalGasAPI | None = None
        self._download_lock = asyncio.Lock()

    async def _async_update_data(self) -> dict:
        """Fetch new data from SoCal Gas.

        On first run after setup, imports historical data based on
        lookback_days. On all subsequent runs (including after restarts),
        fetches the last 3 days to catch gaps.
        """
        username = self.entry.data.get(CONF_USERNAME)
        password = self.entry.data.get(CONF_PASSWORD)
        if not username or not password:
            raise UpdateFailed("No credentials configured")

        browserless_url = self.entry.data.get(CONF_BROWSERLESS_URL)

        async with self._download_lock:
            return await self._do_update(username, password, browserless_url)

    async def _do_update(self, username: str, password: str, browserless_url: str | None = None) -> dict:
        """Run the actual update (must be called under _download_lock)."""
        # Reuse the already-authenticated API from the config flow if
        # available, to avoid a second login that triggers rate limiting.
        pending_api = self.hass.data.get(DOMAIN, {}).pop("pending_api", None)
        if pending_api:
            _LOGGER.info("Reusing authenticated API from config flow")
            api = pending_api
        else:
            _LOGGER.info("Creating new API session, will authenticate")
            api = SoCalGasAPI(username, password, browserless_url=browserless_url)
        try:
            if not pending_api:
                _LOGGER.info("Authenticating with SoCal Gas...")
                try:
                    account_info = await api.authenticate()
                    _LOGGER.info("Authentication successful")
                except SoCalGasAuthError as err:
                    _LOGGER.error("Authentication failed: %s", err)
                    err_msg = str(err).lower()
                    if "interstitial" in err_msg or "confirm account" in err_msg:
                        async_create(
                            self.hass,
                            "SoCal Gas requires you to confirm account "
                            "information before data can be downloaded. "
                            "Please log in to socalgas.com in a browser, "
                            "address the popup, then restart the integration.",
                            title="SoCal Gas: Action Required",
                            notification_id="socalgas_interstitial",
                        )
                    if "invalid" in err_msg and "password" in err_msg:
                        raise ConfigEntryAuthFailed(str(err)) from err
                    raise UpdateFailed(str(err)) from err
                except SoCalGasConnectionError as err:
                    _LOGGER.error("Connection error during auth: %s", err)
                    raise UpdateFailed(str(err)) from err

            end_date = datetime.now(tz=timezone.utc)
            initial_import_done = self.entry.data.get(
                CONF_INITIAL_IMPORT_DONE, False
            )

            if not initial_import_done:
                lookback = self.entry.data.get(CONF_LOOKBACK_DAYS, 3)
                start_date = end_date - timedelta(days=lookback)
                _LOGGER.info(
                    "Initial import: %d day lookback (%s to %s)",
                    lookback, start_date.date(), end_date.date(),
                )
            else:
                # Dynamic refresh: fetch from the latest statistic in HA
                # (minus 5 day overlap to cover SoCal Gas data lag),
                # capped at MAX_REFRESH_DAYS
                last_stat = await self._get_latest_statistic_time()
                if last_stat:
                    start_date = last_stat - timedelta(days=5)
                    earliest = end_date - timedelta(days=MAX_REFRESH_DAYS)
                    if start_date < earliest:
                        start_date = earliest
                    days_back = (end_date - start_date).days
                    _LOGGER.info(
                        "Refresh: %d days (%s to %s), "
                        "latest stat was %s",
                        days_back, start_date.date(), end_date.date(),
                        last_stat.date(),
                    )
                else:
                    start_date = end_date - timedelta(days=3)
                    _LOGGER.info(
                        "Refresh: no existing stats found, "
                        "fetching last 3 days",
                    )

            total_readings = await self._download_range(
                api, start_date, end_date
            )

            # Persist the initial import flag
            if not initial_import_done:
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    data={
                        **self.entry.data,
                        CONF_INITIAL_IMPORT_DONE: True,
                    },
                )

            return {
                "last_update": end_date.isoformat(),
                "readings_count": total_readings,
            }
        finally:
            await api.close()

    def _name_slug(self) -> str:
        """Return the name slug for this entry."""
        return (
            self.entry.data.get("account_name", "home")
            .lower()
            .replace(" ", "_")
        )

    async def _get_latest_statistic_time(self) -> datetime | None:
        """Query HA recorder for the latest statistic timestamp."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import (
            get_last_statistics,
        )

        statistic_id = f"{DOMAIN}:gas_consumption_{self._name_slug()}"

        result = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, False, {"start"}
        )
        if result and statistic_id in result and result[statistic_id]:
            row = result[statistic_id][0]
            ts = row["start"]
            if isinstance(ts, (int, float)):
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            return ts
        return None

    async def _clear_statistics(self) -> None:
        """Clear all existing statistics for this entry."""
        from homeassistant.components.recorder import get_instance

        name_slug = self._name_slug()
        statistic_ids = [
            f"{DOMAIN}:gas_consumption_{name_slug}",
            f"{DOMAIN}:gas_cost_{name_slug}",
        ]
        done = asyncio.Event()
        get_instance(self.hass).async_clear_statistics(
            statistic_ids, on_done=done.set
        )
        await done.wait()
        _LOGGER.info("Cleared existing statistics: %s", statistic_ids)

    async def async_redownload_range(
        self, start_date: datetime, end_date: datetime
    ) -> None:
        """Re-download a specific date range on demand."""
        _LOGGER.info(
            "Redownload requested: %s to %s",
            start_date.date(), end_date.date(),
        )
        username = self.entry.data.get(CONF_USERNAME)
        password = self.entry.data.get(CONF_PASSWORD)
        if not username or not password:
            _LOGGER.error("Cannot redownload: no credentials configured")
            return

        browserless_url = self.entry.data.get(CONF_BROWSERLESS_URL)

        async with self._download_lock:
            api = SoCalGasAPI(username, password, browserless_url=browserless_url)
            try:
                await api.authenticate()
                await self._download_range(
                    api, start_date, end_date, label="Re-download",
                    clear_first=True,
                )
            except (SoCalGasAuthError, SoCalGasConnectionError) as err:
                _LOGGER.error("Redownload failed: %s", err)
            finally:
                await api.close()

    async def _download_range(
        self,
        api: SoCalGasAPI,
        start_date: datetime,
        end_date: datetime,
        label: str = "Import",
        clear_first: bool = False,
    ) -> int:
        """Download and import data in chunks. Returns total readings.

        Downloads all chunks first, deduplicates readings by hour,
        then computes cumulative sums once over the complete dataset.
        This prevents sum discontinuities caused by overlapping data
        between adjacent API chunks.
        """
        name_slug = self._name_slug()
        chunk_start = start_date

        # Calculate total chunks for progress notifications
        total_days = (end_date - start_date).days
        total_chunks = max(1, (total_days + CHUNK_DAYS - 1) // CHUNK_DAYS)
        chunk_num = 0
        label_slug = label.lower().replace(" ", "_")
        notification_id = f"{DOMAIN}_{label_slug}_{name_slug}"

        # Phase 1: Download all chunks, collecting raw readings
        all_readings = []

        while chunk_start < end_date:
            chunk_num += 1
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), end_date)

            _LOGGER.info(
                "Downloading chunk %d/%d: %s to %s",
                chunk_num, total_chunks, chunk_start.date(), chunk_end.date(),
            )

            async_create(
                self.hass,
                f"Downloading data: chunk {chunk_num} of {total_chunks}\n"
                f"({chunk_start.strftime('%b %d, %Y')} – "
                f"{chunk_end.strftime('%b %d, %Y')})",
                title=f"SoCal Gas {label}",
                notification_id=notification_id,
            )

            try:
                zip_bytes = await api.download_green_button(
                    chunk_start, chunk_end
                )
            except SoCalGasAuthError as err:
                async_dismiss(self.hass, notification_id)
                raise ConfigEntryAuthFailed(str(err)) from err
            except SoCalGasConnectionError as err:
                async_dismiss(self.hass, notification_id)
                raise UpdateFailed(str(err)) from err

            try:
                xml_content = self._extract_xml_from_zip(zip_bytes)
                readings, summary = parse_green_button_xml(xml_content)
            except Exception as err:
                async_dismiss(self.hass, notification_id)
                raise UpdateFailed(
                    f"Failed to parse downloaded data: {err}"
                ) from err

            if readings:
                _LOGGER.info(
                    "Chunk %d/%d: %d readings (%s to %s)",
                    chunk_num, total_chunks, len(readings),
                    readings[0].start.date(), readings[-1].start.date(),
                )
                all_readings.extend(readings)
            else:
                _LOGGER.info(
                    "Chunk %d/%d: no data returned for %s to %s",
                    chunk_num, total_chunks,
                    chunk_start.date(), chunk_end.date(),
                )

            chunk_start = chunk_end

            # Rate-limit protection: pause between chunks (not after last)
            if chunk_start < end_date:
                _LOGGER.info("Sleeping 5s between chunks (rate-limit protection)")
                await asyncio.sleep(5)

        # Phase 2: Deduplicate downloaded readings by hour
        if not all_readings:
            async_dismiss(self.hass, notification_id)
            _LOGGER.info("No readings in downloaded data")
            return 0

        hour_map: dict[datetime, object] = {}
        for r in all_readings:
            key = r.start.replace(minute=0, second=0, microsecond=0)
            hour_map[key] = r
        unique_readings = sorted(hour_map.values(), key=lambda r: r.start)

        dupes = len(all_readings) - len(unique_readings)
        if dupes > 0:
            _LOGGER.info(
                "Deduplicated %d overlapping readings (kept %d unique)",
                dupes, len(unique_readings),
            )

        # Phase 3: Merge with existing data.
        # Query all existing statistics from the earliest new reading
        # onward. New readings take priority; existing hours that are
        # NOT in the download are kept so their sums stay consistent.
        earliest = unique_readings[0].start
        existing = await async_get_existing_states(
            self.hass, name_slug, earliest
        )
        merged = merge_readings_with_existing(unique_readings, existing)

        # Phase 3.5: Clear old statistics if requested (redownload).
        # Done after download succeeds so we don't wipe data if download fails.
        if clear_first:
            await self._clear_statistics()
            # Re-query existing (now empty) and re-merge
            existing = {}
            merged = merge_readings_with_existing(unique_readings, existing)

        # Phase 4: Compute cumulative sums and import
        running_usage_sum, running_cost_sum = await async_get_prior_sums(
            self.hass, name_slug, merged[0].start
        )
        _LOGGER.info(
            "Starting sums before %s: usage=%.2f ft³, cost=$%.4f",
            merged[0].start.date(), running_usage_sum, running_cost_sum,
        )

        async_create(
            self.hass,
            f"Importing {len(merged)} readings...",
            title=f"SoCal Gas {label}",
            notification_id=notification_id,
        )

        stats = readings_to_hourly_statistics(
            merged, running_usage_sum, running_cost_sum
        )
        await async_import_to_ha(self.hass, stats, name_slug)

        _LOGGER.info(
            "Final sums: usage=%.2f ft³, cost=$%.4f",
            stats[-1].usage_sum, stats[-1].cost_sum,
        )

        async_dismiss(self.hass, notification_id)
        _LOGGER.info(
            "%s complete: %d new readings + %d existing merged (%s to %s)",
            label, len(unique_readings),
            len(merged) - len(unique_readings),
            merged[0].start.date(),
            merged[-1].start.date(),
        )

        return len(unique_readings)

    @staticmethod
    def _extract_xml_from_zip(zip_bytes: bytes) -> str:
        """Extract the first XML file from ZIP bytes."""
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            xml_files = [n for n in zf.namelist() if n.endswith(".xml")]
            if not xml_files:
                raise ValueError("No XML file found in downloaded ZIP")
            with zf.open(xml_files[0]) as f:
                return f.read().decode("utf-8")
