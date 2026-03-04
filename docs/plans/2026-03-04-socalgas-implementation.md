# SoCal Gas Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a HACS-installable HA custom integration that imports SoCal Gas Green Button data as historical statistics and provides energy dashboard-compatible sensors.

**Architecture:** Custom component (`custom_components/socalgas`) with config flow file upload, ESPI XML parser, and `async_add_external_statistics` for historical data injection. Docker Compose dev environment for testing.

**Tech Stack:** Home Assistant Core (Docker), Python 3.12+, ESPI/Green Button XML, HA Recorder Statistics API

---

### Task 1: Set Up Docker Compose Dev Environment

**Files:**
- Create: `docker-compose.yml`
- Create: `.gitignore`

**Step 1: Create docker-compose.yml**

```yaml
version: "3.8"
services:
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    container_name: ha-socalgas-dev
    restart: unless-stopped
    volumes:
      - ./config:/config
      - ./custom_components:/config/custom_components
      - /etc/localtime:/etc/localtime:ro
    ports:
      - "8123:8123"
    environment:
      - TZ=America/Los_Angeles
```

**Step 2: Create .gitignore**

```
config/
!custom_components/
__pycache__/
*.pyc
.env
*.egg-info/
```

**Step 3: Create config directory**

Run: `mkdir -p config`

**Step 4: Start HA to verify it works**

Run: `docker compose up -d && sleep 15 && docker compose logs --tail 20 homeassistant`
Expected: HA starts, logs show "Home Assistant initialized"

**Step 5: Stop HA**

Run: `docker compose down`

**Step 6: Commit**

```bash
git add docker-compose.yml .gitignore
git commit -m "feat: add Docker Compose dev environment for HA"
```

---

### Task 2: Create Integration Skeleton (manifest, const, strings)

**Files:**
- Create: `custom_components/socalgas/__init__.py`
- Create: `custom_components/socalgas/manifest.json`
- Create: `custom_components/socalgas/const.py`
- Create: `custom_components/socalgas/strings.json`
- Create: `custom_components/socalgas/translations/en.json`
- Create: `hacs.json`

**Step 1: Create manifest.json**

```json
{
  "domain": "socalgas",
  "name": "SoCal Gas",
  "version": "0.1.0",
  "documentation": "https://github.com/nfox/ha-socal-gas-integration",
  "issue_tracker": "https://github.com/nfox/ha-socal-gas-integration/issues",
  "codeowners": ["@nfox"],
  "dependencies": ["file_upload"],
  "requirements": [],
  "config_flow": true,
  "iot_class": "local_push",
  "integration_type": "service"
}
```

**Step 2: Create const.py**

```python
"""Constants for the SoCal Gas integration."""
DOMAIN = "socalgas"
CONF_UPLOADED_FILE = "uploaded_file"
CONF_ACCOUNT_NAME = "account_name"
```

**Step 3: Create minimal __init__.py**

```python
"""The SoCal Gas integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SoCal Gas from a config entry."""
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True
```

**Step 4: Create strings.json**

```json
{
  "config": {
    "step": {
      "user": {
        "title": "SoCal Gas",
        "description": "Name this SoCal Gas account or service location.",
        "data": {
          "account_name": "Account Name"
        }
      },
      "upload": {
        "title": "Upload Usage Data",
        "description": "Upload a Green Button ZIP file downloaded from socalgas.com.",
        "data": {
          "uploaded_file": "Green Button ZIP File"
        }
      }
    },
    "error": {
      "invalid_file": "Could not parse the uploaded file. Please upload a valid Green Button ZIP from socalgas.com.",
      "no_data": "The uploaded file contained no usage data."
    },
    "abort": {
      "already_configured": "This account is already configured."
    }
  }
}
```

**Step 5: Create translations/en.json (copy of strings.json)**

Same content as strings.json.

**Step 6: Create hacs.json**

```json
{
  "name": "SoCal Gas",
  "render_readme": true,
  "homeassistant": "2024.1.0"
}
```

**Step 7: Commit**

```bash
git add custom_components/socalgas/ hacs.json
git commit -m "feat: add integration skeleton with manifest and strings"
```

---

### Task 3: Build Green Button XML Parser

**Files:**
- Create: `custom_components/socalgas/green_button_parser.py`
- Create: `tests/test_green_button_parser.py`

**Step 1: Write tests for the parser**

```python
"""Tests for the Green Button XML parser."""
import zipfile
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest

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
    # All readings should have required fields
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
    # The raw XML has powerOfTenMultiplier=2, meaning values are in therms * 100
    # Parser should divide by 10^2 = 100
    readings, _ = parse_green_button_zip(TEST_ZIP)
    # Values should be reasonable therms (0-5 range for hourly residential)
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
    # powerOfTenMultiplier=2 means divide by 100
    assert readings[0].therms == 0.50
    assert readings[0].cost_dollars == 1.50  # 150 cents / 100
    assert readings[1].therms == 0.75
    assert readings[1].cost_dollars == 2.00  # 200 cents / 100
    assert readings[0].start == datetime(2025, 2, 3, 0, 0, tzinfo=timezone.utc)
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/nfox/github/ha-socal-gas-integration && python -m pytest tests/test_green_button_parser.py -v`
Expected: ImportError — module does not exist yet

**Step 3: Implement the parser**

```python
"""Parser for Green Button (ESPI) XML data from SoCal Gas."""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ESPI_NS = "http://naesb.org/espi"
ATOM_NS = "http://www.w3.org/2005/Atom"


@dataclass
class GreenButtonReading:
    """A single interval reading from Green Button data."""
    start: datetime  # timezone-aware UTC
    duration_seconds: int
    therms: float
    cost_dollars: float


@dataclass
class GreenButtonSummary:
    """Billing period summary from Green Button data."""
    total_therms: float
    total_cost_dollars: float
    period_start: datetime
    period_duration_seconds: int


def parse_green_button_zip(zip_path: Path | str) -> tuple[list[GreenButtonReading], GreenButtonSummary | None]:
    """Parse a Green Button ZIP file and return readings and summary."""
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        xml_files = [n for n in zf.namelist() if n.endswith(".xml")]
        if not xml_files:
            raise ValueError("No XML file found in ZIP archive")
        with zf.open(xml_files[0]) as f:
            xml_content = f.read().decode("utf-8")
    return parse_green_button_xml(xml_content)


def parse_green_button_xml(xml_content: str) -> tuple[list[GreenButtonReading], GreenButtonSummary | None]:
    """Parse Green Button ESPI XML content."""
    root = ET.fromstring(xml_content)
    multiplier = _extract_power_of_ten_multiplier(root)
    readings = _extract_readings(root, multiplier)
    readings.sort(key=lambda r: r.start)
    summary = _extract_summary(root, multiplier)
    return readings, summary


def _extract_power_of_ten_multiplier(root: ET.Element) -> int:
    """Extract powerOfTenMultiplier from ReadingType."""
    elem = root.find(f".//{{{ESPI_NS}}}powerOfTenMultiplier")
    if elem is not None and elem.text:
        return int(elem.text)
    return 0


def _extract_readings(root: ET.Element, multiplier: int) -> list[GreenButtonReading]:
    """Extract all IntervalReadings from the XML."""
    divisor = 10 ** multiplier
    readings = []
    for interval_reading in root.iter(f"{{{ESPI_NS}}}IntervalReading"):
        cost_elem = interval_reading.find(f"{{{ESPI_NS}}}cost")
        time_period = interval_reading.find(f"{{{ESPI_NS}}}timePeriod")
        value_elem = interval_reading.find(f"{{{ESPI_NS}}}value")
        if time_period is None or value_elem is None:
            continue
        start_elem = time_period.find(f"{{{ESPI_NS}}}start")
        duration_elem = time_period.find(f"{{{ESPI_NS}}}duration")
        if start_elem is None or start_elem.text is None:
            continue
        start = datetime.fromtimestamp(int(start_elem.text), tz=timezone.utc)
        duration = int(duration_elem.text) if duration_elem is not None and duration_elem.text else 3600
        raw_value = int(value_elem.text) if value_elem.text else 0
        raw_cost = int(cost_elem.text) if cost_elem is not None and cost_elem.text else 0
        readings.append(GreenButtonReading(
            start=start,
            duration_seconds=duration,
            therms=raw_value / divisor,
            cost_dollars=raw_cost / divisor,
        ))
    return readings


def _extract_summary(root: ET.Element, multiplier: int) -> GreenButtonSummary | None:
    """Extract UsageSummary from the XML."""
    divisor = 10 ** multiplier
    summary_elem = root.find(f".//{{{ESPI_NS}}}UsageSummary")
    if summary_elem is None:
        return None
    bill_elem = summary_elem.find(f"{{{ESPI_NS}}}billLastPeriod")
    consumption_elem = summary_elem.find(f".//{{{ESPI_NS}}}overallConsumptionLastPeriod/{{{ESPI_NS}}}value")
    billing_period = summary_elem.find(f"{{{ESPI_NS}}}billingPeriod")
    total_cost = int(bill_elem.text) / 100 if bill_elem is not None and bill_elem.text else 0
    total_therms = int(consumption_elem.text) / divisor if consumption_elem is not None and consumption_elem.text else 0
    period_start = datetime.fromtimestamp(0, tz=timezone.utc)
    period_duration = 0
    if billing_period is not None:
        start_elem = billing_period.find(f"{{{ESPI_NS}}}start")
        dur_elem = billing_period.find(f"{{{ESPI_NS}}}duration")
        if start_elem is not None and start_elem.text:
            period_start = datetime.fromtimestamp(int(start_elem.text), tz=timezone.utc)
        if dur_elem is not None and dur_elem.text:
            period_duration = int(dur_elem.text)
    return GreenButtonSummary(
        total_therms=total_therms,
        total_cost_dollars=total_cost,
        period_start=period_start,
        period_duration_seconds=period_duration,
    )
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_green_button_parser.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add custom_components/socalgas/green_button_parser.py tests/test_green_button_parser.py
git commit -m "feat: add Green Button ESPI XML parser with tests"
```

---

### Task 4: Build Statistics Importer

**Files:**
- Create: `custom_components/socalgas/statistics.py`
- Create: `tests/test_statistics.py`

**Step 1: Write tests for the statistics module**

```python
"""Tests for the statistics import module."""
from datetime import datetime, timezone

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
    # Check running sums
    assert stats[0].usage_sum == 0.5
    assert stats[0].cost_sum == 1.50
    assert stats[1].usage_sum == 1.25  # 0.5 + 0.75
    assert stats[1].cost_sum == 3.50   # 1.50 + 2.00
    assert stats[2].usage_sum == 1.50  # 0.5 + 0.75 + 0.25
    assert stats[2].cost_sum == 4.30   # 1.50 + 2.00 + 0.80


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
    assert stats[0].usage_sum == 100.5
    assert stats[0].cost_sum == 501.50
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_statistics.py -v`
Expected: ImportError

**Step 3: Implement the statistics module**

```python
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


async def async_import_to_ha(hass, statistics_entries: list[StatisticEntry], entry_id: str) -> None:
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
        statistic_id=f"{DOMAIN}:gas_consumption_{entry_id[:8]}",
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
        statistic_id=f"{DOMAIN}:gas_cost_{entry_id[:8]}",
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
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_statistics.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add custom_components/socalgas/statistics.py tests/test_statistics.py
git commit -m "feat: add statistics conversion and HA import module"
```

---

### Task 5: Build Config Flow with File Upload

**Files:**
- Create: `custom_components/socalgas/config_flow.py`

**Step 1: Implement the config flow**

```python
"""Config flow for SoCal Gas integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
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
                    # Create config entry first to get entry_id
                    result = self.async_create_entry(
                        title=self._account_name,
                        data={
                            CONF_ACCOUNT_NAME: self._account_name,
                            "reading_count": len(readings),
                        },
                    )
                    # Import statistics
                    stats = readings_to_hourly_statistics(readings)
                    await async_import_to_ha(self.hass, stats, result["result"].entry_id)
                    _LOGGER.info(
                        "Imported %d readings for %s",
                        len(readings),
                        self._account_name,
                    )
                    return result
            except (ValueError, KeyError, Exception) as err:
                _LOGGER.error("Failed to parse uploaded file: %s", err)
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
```

**Step 2: Verify integration loads in Docker HA**

Run: `docker compose up -d && sleep 20 && docker compose logs --tail 30 homeassistant | grep -i -E "socalgas|error|custom"`
Expected: No errors about the socalgas integration

**Step 3: Commit**

```bash
git add custom_components/socalgas/config_flow.py
git commit -m "feat: add config flow with file upload for Green Button ZIP"
```

---

### Task 6: Wire Up __init__.py and Verify End-to-End

**Files:**
- Modify: `custom_components/socalgas/__init__.py`

**Step 1: Update __init__.py to support re-import**

The init is already minimal and correct for Phase 1 — no coordinator needed since we only import statistics during config flow. No sensor entities needed either since the statistics show up as external statistics in the energy dashboard automatically.

Verify the integration works end-to-end:

**Step 2: Start HA and test the full flow**

Run: `docker compose up -d`

1. Open http://localhost:8123
2. Complete onboarding if needed
3. Go to Settings → Devices & Services → Add Integration → SoCal Gas
4. Enter account name
5. Upload the Green Button ZIP file
6. Verify the integration appears
7. Go to Developer Tools → Statistics and look for `socalgas:gas_consumption_*` and `socalgas:gas_cost_*`

**Step 3: Commit any fixes**

```bash
git add -u
git commit -m "fix: wire up end-to-end integration flow"
```

---

### Task 7: Add Options Flow for Re-Import

**Files:**
- Modify: `custom_components/socalgas/config_flow.py`
- Modify: `custom_components/socalgas/__init__.py`

**Step 1: Add options flow handler to config_flow.py**

Add an `OptionsFlow` class that allows re-uploading a ZIP to update/replace statistics. This lets users import additional data files without removing and re-adding the integration.

```python
class SoCalGasOptionsFlow(OptionsFlow):
    """Handle options for SoCal Gas."""

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
                    await async_import_to_ha(
                        self.hass, stats, self.config_entry.entry_id
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
        with process_uploaded_file(self.hass, file_id) as file_path:
            return parse_green_button_zip(file_path)
```

**Step 2: Register options flow in config flow class**

Add to `SoCalGasConfigFlow`:
```python
@staticmethod
@callback
def async_get_options_flow(config_entry):
    return SoCalGasOptionsFlow(config_entry)
```

**Step 3: Add options flow strings**

Add to strings.json and translations/en.json:
```json
{
  "options": {
    "step": {
      "init": {
        "title": "Import Additional Data",
        "description": "Upload another Green Button ZIP file to import additional usage data.",
        "data": {
          "uploaded_file": "Green Button ZIP File"
        }
      }
    },
    "error": {
      "invalid_file": "Could not parse the uploaded file.",
      "no_data": "The uploaded file contained no usage data."
    }
  }
}
```

**Step 4: Commit**

```bash
git add -u
git commit -m "feat: add options flow for re-importing additional data"
```

---

### Task 8: Add README and Final Polish

**Files:**
- Create: `README.md`

**Step 1: Create README**

Write a README covering:
- What the integration does
- Installation via HACS
- How to download Green Button data from socalgas.com
- How to import data
- Energy dashboard setup
- Phase 2 roadmap

**Step 2: Final commit**

```bash
git add README.md
git commit -m "docs: add README with installation and usage instructions"
```

---

### Task 9: Verify Everything Works End-to-End

**Step 1: Clean restart**

```bash
docker compose down -v
rm -rf config/
mkdir config
docker compose up -d
```

**Step 2: Walk through the full flow**

1. Open http://localhost:8123, complete onboarding
2. Add SoCal Gas integration
3. Upload the ZIP file
4. Verify statistics appear in Developer Tools → Statistics
5. Configure Energy Dashboard with the gas consumption statistic
6. Verify historical data appears in the energy dashboard graphs

**Step 3: Tag release**

```bash
git tag v0.1.0
```
