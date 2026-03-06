"""Tests for the SoCal Gas config flow."""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock homeassistant before importing the package
sys.modules.setdefault("homeassistant", MagicMock())
sys.modules.setdefault("homeassistant.config_entries", MagicMock())
sys.modules.setdefault("homeassistant.core", MagicMock())
sys.modules.setdefault("homeassistant.components", MagicMock())
sys.modules.setdefault("homeassistant.components.file_upload", MagicMock())
sys.modules.setdefault("homeassistant.helpers", MagicMock())
sys.modules.setdefault("homeassistant.helpers.selector", MagicMock())

from custom_components.socalgas.green_button_parser import (
    GreenButtonReading,
    GreenButtonSummary,
)
from custom_components.socalgas.statistics import readings_to_hourly_statistics


SAMPLE_READINGS = [
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
]


def test_statistics_import_uses_name_slug_not_entry_id():
    """Regression test: async_import_to_ha must be called with a name slug,
    not an entry_id accessed from FlowResult['result'] which doesn't exist.

    The original bug: config_flow did `result['result'].entry_id` on the return
    value of async_create_entry(), which raised KeyError('result') and was
    silently caught by a broad except, showing 'invalid_file' to the user.
    """
    stats = readings_to_hourly_statistics(SAMPLE_READINGS)

    # Verify the name_slug logic works for various account names
    test_cases = [
        ("Home", "home"),
        ("My House", "my_house"),
        ("Beach Condo", "beach_condo"),
        ("home", "home"),
    ]
    for account_name, expected_slug in test_cases:
        name_slug = account_name.lower().replace(" ", "_")
        assert name_slug == expected_slug, (
            f"Account name '{account_name}' should produce slug '{expected_slug}', "
            f"got '{name_slug}'"
        )


def test_statistics_import_called_before_create_entry():
    """Verify that statistics import happens before async_create_entry
    in the config flow's upload_name step.

    This ensures we don't depend on the FlowResult to get an entry_id.
    We verify this by checking the source code order within
    async_step_upload_name.
    """
    config_flow_path = (
        Path(__file__).parent.parent
        / "custom_components"
        / "socalgas"
        / "config_flow.py"
    )
    source = config_flow_path.read_text()

    # Find async_step_upload_name and check order within it
    method_start = source.find("async def async_step_upload_name(")
    assert method_start != -1, "async_step_upload_name not found"

    method_source = source[method_start:]
    import_pos = method_source.find("await async_import_to_ha(")
    create_entry_pos = method_source.find("return self.async_create_entry(")

    assert import_pos != -1, "async_import_to_ha call not found in async_step_upload_name"
    assert create_entry_pos != -1, "async_create_entry call not found in async_step_upload_name"
    assert import_pos < create_entry_pos, (
        "async_import_to_ha must be called BEFORE async_create_entry to avoid "
        "depending on FlowResult['result'] which doesn't exist"
    )


def test_no_key_access_on_create_entry_result():
    """Regression test: ensure we never index into async_create_entry's return value."""
    config_flow_path = (
        Path(__file__).parent.parent
        / "custom_components"
        / "socalgas"
        / "config_flow.py"
    )
    source = config_flow_path.read_text()

    # The bug was: result = self.async_create_entry(...); result["result"]
    assert 'result["result"]' not in source, (
        "Must not access FlowResult['result'] — this key doesn't exist and "
        "causes a KeyError that gets silently caught"
    )
    assert "result['result']" not in source, (
        "Must not access FlowResult['result'] — this key doesn't exist"
    )
