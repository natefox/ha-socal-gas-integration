"""Tests for the SoCal Gas API client."""
import asyncio
import sys
from unittest.mock import MagicMock

import pytest

# Mock homeassistant modules before importing api.py since it's in the
# socalgas package which imports homeassistant in __init__.py
sys.modules.setdefault("homeassistant", MagicMock())
sys.modules.setdefault("homeassistant.config_entries", MagicMock())
sys.modules.setdefault("homeassistant.core", MagicMock())

from custom_components.socalgas.api import (  # noqa: E402
    AccountInfo,
    SoCalGasAPI,
    SoCalGasAuthError,
    SoCalGasConnectionError,
)


@pytest.fixture
def api():
    """Create a SoCalGasAPI instance."""
    return SoCalGasAPI("test@email.com", "testpassword")


class TestSoCalGasAPI:
    """Tests for the SoCalGasAPI class."""

    def test_init(self, api):
        """Test API client initialization."""
        assert api._username == "test@email.com"
        assert api._password == "testpassword"
        assert api._access_token is None
        assert api._account_info is None

    def test_account_info_initially_none(self, api):
        """Test that account_info is None before authentication."""
        assert api.account_info is None

    def test_download_without_auth_raises(self, api):
        """Test that download raises if not authenticated."""
        from datetime import datetime, timezone

        with pytest.raises(SoCalGasAuthError, match="Must authenticate"):
            asyncio.get_event_loop().run_until_complete(
                api.download_green_button(
                    datetime(2025, 1, 1, tzinfo=timezone.utc),
                    datetime(2025, 2, 1, tzinfo=timezone.utc),
                )
            )

    def test_close_without_session(self, api):
        """Test that close works even without a session."""
        asyncio.get_event_loop().run_until_complete(api.close())

    def test_close_with_external_session(self):
        """Test that close does not close an external session."""
        mock_session = MagicMock()
        mock_session.closed = False
        api = SoCalGasAPI("test@email.com", "pass", session=mock_session)
        asyncio.get_event_loop().run_until_complete(api.close())
        mock_session.close.assert_not_called()


class TestAccountInfo:
    """Tests for the AccountInfo dataclass."""

    def test_account_info_fields(self):
        """Test AccountInfo dataclass fields."""
        info = AccountInfo(
            account_number="1408090780",
            meter_number="03894524",
            gnn_id="1408090700",
            service_location_id="1408090700",
        )
        assert info.account_number == "1408090780"
        assert info.meter_number == "03894524"
        assert info.gnn_id == "1408090700"
        assert info.service_location_id == "1408090700"


class TestExceptions:
    """Tests for custom exceptions."""

    def test_auth_error(self):
        """Test SoCalGasAuthError."""
        err = SoCalGasAuthError("bad credentials")
        assert str(err) == "bad credentials"

    def test_connection_error(self):
        """Test SoCalGasConnectionError."""
        err = SoCalGasConnectionError("timeout")
        assert str(err) == "timeout"
