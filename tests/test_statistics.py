"""Tests for the statistics import module."""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock homeassistant before importing the package
sys.modules.setdefault("homeassistant", MagicMock())
sys.modules.setdefault("homeassistant.config_entries", MagicMock())
sys.modules.setdefault("homeassistant.core", MagicMock())

from custom_components.socalgas.green_button_parser import GreenButtonReading
from custom_components.socalgas.statistics import (
    readings_to_hourly_statistics,
    StatisticEntry,
)


def test_readings_to_hourly_statistics_basic():
    """Test converting readings to hourly statistics with running sum."""
    readings = [
        GreenButtonReading(
            start=datetime(2025, 2, 3, 0, 0, tzinfo=timezone.utc),
            duration_seconds=3600,
            therms=0.5,
            cost_dollars=1.50,
        ),
        GreenButtonReading(
            start=datetime(2025, 2, 3, 1, 0, tzinfo=timezone.utc),
            duration_seconds=3600,
            therms=0.75,
            cost_dollars=2.00,
        ),
        GreenButtonReading(
            start=datetime(2025, 2, 3, 2, 0, tzinfo=timezone.utc),
            duration_seconds=3600,
            therms=0.25,
            cost_dollars=0.80,
        ),
    ]
    stats = readings_to_hourly_statistics(readings)
    assert len(stats) == 3
    # therms * 100 = ft³: 0.5 therms = 50 ft³
    assert stats[0].usage_state == 50.0
    assert stats[0].usage_sum == 50.0
    assert stats[0].cost_sum == 1.50
    # 0.75 therms = 75 ft³, cumulative = 125
    assert stats[1].usage_state == 75.0
    assert stats[1].usage_sum == 125.0
    assert stats[1].cost_sum == 3.50
    # 0.25 therms = 25 ft³, cumulative = 150
    assert stats[2].usage_state == 25.0
    assert stats[2].usage_sum == 150.0
    assert stats[2].cost_sum == 4.30


def test_readings_to_hourly_statistics_snaps_to_hour():
    """Test that start times are snapped to the top of the hour."""
    readings = [
        GreenButtonReading(
            start=datetime(2025, 2, 3, 0, 15, 30, tzinfo=timezone.utc),
            duration_seconds=3600,
            therms=0.5,
            cost_dollars=1.50,
        ),
    ]
    stats = readings_to_hourly_statistics(readings)
    assert stats[0].start.minute == 0
    assert stats[0].start.second == 0


def test_readings_with_initial_sum():
    """Test continuing from an existing sum value."""
    readings = [
        GreenButtonReading(
            start=datetime(2025, 2, 3, 0, 0, tzinfo=timezone.utc),
            duration_seconds=3600,
            therms=0.5,
            cost_dollars=1.50,
        ),
    ]
    stats = readings_to_hourly_statistics(readings, initial_usage_sum=100.0, initial_cost_sum=500.0)
    # 0.5 therms = 50 ft³, cumulative = 100 + 50 = 150
    assert stats[0].usage_sum == 150.0
    assert stats[0].cost_sum == 501.50
