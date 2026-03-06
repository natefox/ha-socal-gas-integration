"""Statistics import utilities for SoCal Gas integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant

from .green_button_parser import GreenButtonReading

_LOGGER = logging.getLogger(__name__)


@dataclass
class StatisticEntry:
    """A prepared statistic entry ready for HA import."""
    start: datetime  # top-of-hour, timezone-aware
    usage_state: float  # ft³ this hour
    usage_sum: float  # cumulative ft³
    cost_state: float  # dollars this hour
    cost_sum: float  # cumulative dollars


def readings_to_hourly_statistics(
    readings: list[GreenButtonReading],
    initial_usage_sum: float = 0.0,
    initial_cost_sum: float = 0.0,
) -> list[StatisticEntry]:
    """Convert Green Button readings to hourly statistics with running sums."""
    usage_sum = initial_usage_sum
    cost_sum = initial_cost_sum
    stats = []
    for reading in readings:
        start = reading.start.replace(minute=0, second=0, microsecond=0)
        ft3 = reading.therms * 100  # 1 therm ≈ 100 ft³
        usage_sum += ft3
        cost_sum += reading.cost_dollars
        stats.append(StatisticEntry(
            start=start,
            usage_state=round(ft3, 2),
            usage_sum=round(usage_sum, 2),
            cost_state=reading.cost_dollars,
            cost_sum=round(cost_sum, 4),
        ))
    return stats


def _get_recorder(hass: HomeAssistant):
    """Get the recorder instance for proper database executor access."""
    from homeassistant.components.recorder import get_instance
    return get_instance(hass)


def _ts_to_dt(val) -> datetime:
    """Convert a value to a timezone-aware datetime.

    ``statistics_during_period`` returns ``start`` as a float (Unix
    timestamp) in some HA versions and as a datetime in others.
    """
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc)
    return val


async def async_get_prior_sums(
    hass: HomeAssistant, name_slug: str, before: datetime
) -> tuple[float, float]:
    """Get cumulative usage and cost sums just before a given timestamp.

    Queries the HA recorder for existing statistics so that new imports
    can continue the running totals correctly.

    Returns (usage_sum, cost_sum). Returns (0, 0) if no prior data.
    """
    from homeassistant.components.recorder.statistics import (
        statistics_during_period,
    )

    from .const import DOMAIN

    usage_id = f"{DOMAIN}:gas_consumption_{name_slug}"
    cost_id = f"{DOMAIN}:gas_cost_{name_slug}"

    query_start = before - timedelta(days=730)

    result = await _get_recorder(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        query_start,
        before,
        {usage_id, cost_id},
        "hour",
        None,
        {"sum"},
    )

    usage_sum = 0.0
    cost_sum = 0.0

    if usage_id in result and result[usage_id]:
        usage_sum = result[usage_id][-1].get("sum", 0.0)

    if cost_id in result and result[cost_id]:
        cost_sum = result[cost_id][-1].get("sum", 0.0)

    return usage_sum, cost_sum


async def async_get_existing_states(
    hass: HomeAssistant, name_slug: str, after: datetime
) -> dict[datetime, tuple[float, float]]:
    """Query existing hourly state values from the recorder.

    Returns ``{hour_dt: (usage_ft3, cost_dollars)}`` for every hour
    that already has a statistic on or after ``after``.
    """
    from homeassistant.components.recorder.statistics import (
        statistics_during_period,
    )

    from .const import DOMAIN

    usage_id = f"{DOMAIN}:gas_consumption_{name_slug}"
    cost_id = f"{DOMAIN}:gas_cost_{name_slug}"

    query_end = after + timedelta(days=730)

    result = await _get_recorder(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        after,
        query_end,
        {usage_id, cost_id},
        "hour",
        None,
        {"state"},
    )

    usage_rows = result.get(usage_id, [])
    cost_rows = result.get(cost_id, [])

    # Build cost lookup
    cost_by_ts: dict[datetime, float] = {}
    for row in cost_rows:
        dt = _ts_to_dt(row["start"])
        cost_by_ts[dt] = row.get("state", 0.0) or 0.0

    existing: dict[datetime, tuple[float, float]] = {}
    for row in usage_rows:
        dt = _ts_to_dt(row["start"])
        usage_state = row.get("state", 0.0) or 0.0
        cost_state = cost_by_ts.get(dt, 0.0)
        existing[dt] = (usage_state, cost_state)

    return existing


def merge_readings_with_existing(
    new_readings: list[GreenButtonReading],
    existing: dict[datetime, tuple[float, float]],
) -> list[GreenButtonReading]:
    """Merge new readings with existing statistics.

    New readings take priority. For hours that already exist in HA
    but are NOT in the new download, synthetic readings are created
    from the existing state values so that cumulative sums stay
    consistent across the entire timeline.

    Returns a sorted list of readings covering the full range.
    """
    # Build map from new readings (by truncated hour)
    new_by_hour: dict[datetime, GreenButtonReading] = {}
    for r in new_readings:
        key = r.start.replace(minute=0, second=0, microsecond=0)
        new_by_hour[key] = r

    # Start with new readings
    merged: dict[datetime, GreenButtonReading] = dict(new_by_hour)

    # Add existing data for hours NOT covered by new readings
    added_existing = 0
    for dt, (usage_ft3, cost_dollars) in existing.items():
        if dt not in merged:
            # Convert ft³ back to therms for the GreenButtonReading
            merged[dt] = GreenButtonReading(
                start=dt,
                duration_seconds=3600,
                therms=usage_ft3 / 100.0,
                cost_dollars=cost_dollars,
            )
            added_existing += 1

    if added_existing > 0:
        _LOGGER.info(
            "Merged %d new readings with %d existing hours "
            "(total %d hours to import)",
            len(new_by_hour), added_existing, len(merged),
        )

    return sorted(merged.values(), key=lambda r: r.start)


async def async_import_to_ha(hass, statistics_entries: list[StatisticEntry], name_slug: str) -> None:
    """Import statistics into Home Assistant's recorder."""
    from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
    from homeassistant.components.recorder.models.statistics import StatisticMeanType
    from homeassistant.components.recorder.statistics import async_add_external_statistics

    from .const import DOMAIN

    # Usage statistics
    usage_metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        name="SoCal Gas Usage",
        source=DOMAIN,
        statistic_id=f"{DOMAIN}:gas_consumption_{name_slug}",
        unit_class="volume",
        unit_of_measurement="ft³",
    )
    usage_stats = [
        StatisticData(
            start=entry.start,
            state=entry.usage_state,
            sum=entry.usage_sum,
        )
        for entry in statistics_entries
    ]
    async_add_external_statistics(hass, usage_metadata, usage_stats)

    # Cost statistics
    cost_metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        name="SoCal Gas Cost",
        source=DOMAIN,
        statistic_id=f"{DOMAIN}:gas_cost_{name_slug}",
        unit_class=None,
        unit_of_measurement="USD",
    )
    cost_stats = [
        StatisticData(
            start=entry.start,
            state=entry.cost_state,
            sum=entry.cost_sum,
        )
        for entry in statistics_entries
    ]
    async_add_external_statistics(hass, cost_metadata, cost_stats)
