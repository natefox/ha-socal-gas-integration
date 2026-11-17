"""Tests for the Green Button XML parser."""
import zipfile
import io
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from unittest.mock import MagicMock
import sys

import pytest

# Add custom_components to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock homeassistant before importing the package
sys.modules.setdefault("homeassistant", MagicMock())
sys.modules.setdefault("homeassistant.config_entries", MagicMock())
sys.modules.setdefault("homeassistant.core", MagicMock())

from custom_components.socalgas.green_button_parser import (
    parse_green_button_xml,
    parse_green_button_zip,
    GreenButtonReading,
    GreenButtonSummary,
)

# Path to the real test data
TEST_ZIP = Path(__file__).parent.parent / "SoCalGas_Gas_60_Minute_02-03-25_03-03-26.zip"


def test_parse_real_zip():
    """Test parsing the real SoCal Gas ZIP export."""
    readings, summary = parse_green_button_zip(TEST_ZIP)
    assert len(readings) > 100
    for r in readings[:10]:
        assert isinstance(r.start, datetime)
        assert r.start.tzinfo is not None
        assert isinstance(r.therms, float)
        assert r.therms >= 0
        assert isinstance(r.cost_dollars, float)
        assert r.duration_seconds == 3600


def test_parse_real_zip_summary():
    """Test that summary is extracted from real data."""
    readings, summary = parse_green_button_zip(TEST_ZIP)
    assert summary is not None
    assert summary.total_therms > 0
    assert summary.total_cost_dollars > 0


def test_readings_are_sorted_chronologically():
    """Test that readings come back sorted by start time."""
    readings, _ = parse_green_button_zip(TEST_ZIP)
    for i in range(1, len(readings)):
        assert readings[i].start >= readings[i - 1].start


def test_power_of_ten_multiplier_applied():
    """Test that powerOfTenMultiplier is correctly applied to values."""
    readings, _ = parse_green_button_zip(TEST_ZIP)
    for r in readings[:100]:
        assert r.therms < 10, f"Therms {r.therms} seems too high for hourly residential"


MINIMAL_XML = """\
<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
  <entry>
    <content>
      <ReadingType xmlns="http://naesb.org/espi">
        <powerOfTenMultiplier>2</powerOfTenMultiplier>
        <uom>169</uom>
      </ReadingType>
    </content>
  </entry>
  <entry>
    <content>
      <IntervalBlock xmlns="http://naesb.org/espi">
        <interval>
          <duration>86400</duration>
          <start>1738540800</start>
        </interval>
        <IntervalReading>
          <cost>150</cost>
          <timePeriod>
            <duration>3600</duration>
            <start>1738540800</start>
          </timePeriod>
          <value>50</value>
        </IntervalReading>
        <IntervalReading>
          <cost>200</cost>
          <timePeriod>
            <duration>3600</duration>
            <start>1738544400</start>
          </timePeriod>
          <value>75</value>
        </IntervalReading>
      </IntervalBlock>
    </content>
  </entry>
</feed>
"""


def test_parse_minimal_xml():
    """Test parsing a minimal Green Button XML."""
    readings, summary = parse_green_button_xml(MINIMAL_XML)
    assert len(readings) == 2
    assert readings[0].therms == 0.50
    assert readings[0].cost_dollars == 1.50
    assert readings[1].therms == 0.75
    assert readings[1].cost_dollars == 2.00
    # 1738540800 is 2025-02-03 00:00 as a PST wall-clock value,
    # i.e. 08:00 UTC.
    assert readings[0].start == datetime(2025, 2, 3, 8, 0, tzinfo=timezone.utc)


def _single_reading_xml(start_epoch: int) -> str:
    return f"""\
<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><content>
    <IntervalBlock xmlns="http://naesb.org/espi">
      <IntervalReading>
        <timePeriod><duration>3600</duration><start>{start_epoch}</start></timePeriod>
        <value>10</value>
      </IntervalReading>
    </IntervalBlock>
  </content></entry>
</feed>
"""


_LA = ZoneInfo("America/Los_Angeles")


def test_timestamps_are_pst_year_round_winter():
    """Winter: PST wall clock 2026-01-15 07:00 is 07:00 local."""
    epoch = int(datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc).timestamp())
    (reading,), _ = parse_green_button_xml(_single_reading_xml(epoch))
    assert reading.start == datetime(2026, 1, 15, 7, 0, tzinfo=_LA)


def test_timestamps_are_pst_year_round_summer():
    """Summer: PST wall clock 2026-10-08 23:00 is 2026-10-09 00:00 PDT.

    SoCal Gas does not shift its timestamps for DST, so the website's
    12 AM hour of Oct 9 arrives stamped 23:00 Oct 8 and must land on Oct 9.
    """
    epoch = int(datetime(2026, 10, 8, 23, 0, tzinfo=timezone.utc).timestamp())
    (reading,), _ = parse_green_button_xml(_single_reading_xml(epoch))
    assert reading.start == datetime(2026, 10, 9, 0, 0, tzinfo=_LA)
    assert reading.start.astimezone(timezone.utc) == datetime(
        2026, 10, 9, 7, 0, tzinfo=timezone.utc
    )
