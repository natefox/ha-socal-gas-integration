"""Statistics import utilities for SoCal Gas integration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .green_button_parser import GreenButtonReading


@dataclass
class StatisticEntry:
    """A prepared statistic entry ready for HA import."""
    start: datetime  # top-of-hour, timezone-aware
    usage_state: float  # therms this hour
    usage_sum: float  # cumulative therms
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
        usage_sum += reading.therms
        cost_sum += reading.cost_dollars
        stats.append(StatisticEntry(
            start=start,
            usage_state=reading.therms,
            usage_sum=round(usage_sum, 4),
            cost_state=reading.cost_dollars,
            cost_sum=round(cost_sum, 4),
        ))
    return stats


async def async_import_to_ha(hass, statistics_entries: list[StatisticEntry], name_slug: str) -> None:
    """Import statistics into Home Assistant's recorder."""
    from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
    from homeassistant.components.recorder.statistics import async_add_external_statistics

    from .const import DOMAIN

    # Usage statistics
    usage_metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        name="SoCal Gas Usage",
        source=DOMAIN,
        statistic_id=f"{DOMAIN}:gas_consumption_{name_slug}",
        unit_of_measurement="therms",
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
        name="SoCal Gas Cost",
        source=DOMAIN,
        statistic_id=f"{DOMAIN}:gas_cost_{name_slug}",
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
