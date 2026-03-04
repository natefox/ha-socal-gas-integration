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
