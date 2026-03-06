"""Tests for the SoCal Gas browser authentication module."""
import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock homeassistant modules before importing
sys.modules.setdefault("homeassistant", MagicMock())
sys.modules.setdefault("homeassistant.config_entries", MagicMock())
sys.modules.setdefault("homeassistant.core", MagicMock())

from custom_components.socalgas.api import (  # noqa: E402
    SoCalGasAuthError,
    SoCalGasConnectionError,
)
from custom_components.socalgas.browser import browser_authenticate  # noqa: E402


def _make_mock_response(status: int, body: dict) -> AsyncMock:
    """Create a mock aiohttp response."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=body)
    # Support async context manager
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_mock_session(response: AsyncMock) -> AsyncMock:
    """Create a mock aiohttp.ClientSession that returns the given response on post()."""
    session = AsyncMock()
    session.post = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestBrowserAuthenticate:
    """Tests for the HTTP-based browser_authenticate function."""

    def test_successful_authentication(self):
        """Test successful authentication returns token and account number."""
        mock_resp = _make_mock_response(200, {
            "access_token": "test-token-123",
            "account_number": "1234567890",
        })
        mock_session = _make_mock_session(mock_resp)

        with patch("custom_components.socalgas.browser.aiohttp.ClientSession",
                   return_value=mock_session):
            token, account = asyncio.get_event_loop().run_until_complete(
                browser_authenticate("http://playwright:3000", "user@test.com", "pass")
            )

        assert token == "test-token-123"
        assert account == "1234567890"

    def test_successful_auth_without_account_number(self):
        """Test successful auth when account_number is missing from response."""
        mock_resp = _make_mock_response(200, {
            "access_token": "test-token-123",
        })
        mock_session = _make_mock_session(mock_resp)

        with patch("custom_components.socalgas.browser.aiohttp.ClientSession",
                   return_value=mock_session):
            token, account = asyncio.get_event_loop().run_until_complete(
                browser_authenticate("http://playwright:3000", "user@test.com", "pass")
            )

        assert token == "test-token-123"
        assert account == ""

    def test_401_raises_auth_error(self):
        """Test that a 401 response raises SoCalGasAuthError."""
        mock_resp = _make_mock_response(401, {
            "error": "Login failed — page did not redirect.",
        })
        mock_session = _make_mock_session(mock_resp)

        with patch("custom_components.socalgas.browser.aiohttp.ClientSession",
                   return_value=mock_session):
            with pytest.raises(SoCalGasAuthError, match="Login failed"):
                asyncio.get_event_loop().run_until_complete(
                    browser_authenticate("http://playwright:3000", "user@test.com", "wrong")
                )

    def test_500_raises_connection_error(self):
        """Test that a 500 response raises SoCalGasConnectionError."""
        mock_resp = _make_mock_response(500, {
            "error": "Browser automation failed: timeout",
        })
        mock_session = _make_mock_session(mock_resp)

        with patch("custom_components.socalgas.browser.aiohttp.ClientSession",
                   return_value=mock_session):
            with pytest.raises(SoCalGasConnectionError, match="Browser automation failed"):
                asyncio.get_event_loop().run_until_complete(
                    browser_authenticate("http://playwright:3000", "user@test.com", "pass")
                )

    def test_connection_failure_raises_connection_error(self):
        """Test that a connection failure raises SoCalGasConnectionError."""
        import aiohttp as real_aiohttp

        mock_session = AsyncMock()
        mock_session.post = MagicMock(
            side_effect=real_aiohttp.ClientConnectorError(
                connection_key=MagicMock(), os_error=OSError("Connection refused")
            )
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("custom_components.socalgas.browser.aiohttp.ClientSession",
                   return_value=mock_session):
            with pytest.raises(SoCalGasConnectionError, match="Cannot connect to Playwright sidecar"):
                asyncio.get_event_loop().run_until_complete(
                    browser_authenticate("http://playwright:3000", "user@test.com", "pass")
                )


class TestBrowserAuthenticateFallback:
    """Tests for the API fallback to browser authentication."""

    def test_authenticate_falls_back_to_browser_on_bot_protection(self):
        """Test that authenticate() falls back to browser when HTTP gets bot protection."""
        from custom_components.socalgas.api import AccountInfo, SoCalGasAPI

        api = SoCalGasAPI("test@email.com", "testpassword")

        expected_info = AccountInfo(
            account_number="1234567890",
            meter_number="12345678",
            gnn_id="1234567800",
            service_location_id="1234567800",
        )

        async def mock_http_auth():
            raise SoCalGasAuthError("Login blocked (possible bot protection).")

        async def mock_browser_auth():
            api._access_token = "fake-token"
            api._account_info = expected_info
            return expected_info

        with (
            patch.object(api, "_authenticate_http", side_effect=mock_http_auth),
            patch.object(api, "_authenticate_browser", side_effect=mock_browser_auth),
        ):
            result = asyncio.get_event_loop().run_until_complete(api.authenticate())
            assert result == expected_info

    def test_authenticate_falls_back_on_accesstoken_error(self):
        """Test that authenticate() falls back when AccessToken cannot be obtained."""
        from custom_components.socalgas.api import AccountInfo, SoCalGasAPI

        api = SoCalGasAPI("test@email.com", "testpassword")

        expected_info = AccountInfo(
            account_number="1234567890",
            meter_number="12345678",
            gnn_id="1234567800",
            service_location_id="1234567800",
        )

        async def mock_http_auth():
            raise SoCalGasAuthError("Could not obtain AccessToken from SSO bridge.")

        async def mock_browser_auth():
            api._access_token = "fake-token"
            api._account_info = expected_info
            return expected_info

        with (
            patch.object(api, "_authenticate_http", side_effect=mock_http_auth),
            patch.object(api, "_authenticate_browser", side_effect=mock_browser_auth),
        ):
            result = asyncio.get_event_loop().run_until_complete(api.authenticate())
            assert result == expected_info

    def test_authenticate_does_not_fallback_on_invalid_credentials(self):
        """Test that authenticate() does NOT fall back for wrong password errors."""
        from custom_components.socalgas.api import SoCalGasAPI

        api = SoCalGasAPI("test@email.com", "wrongpassword")

        async def mock_http_auth():
            raise SoCalGasAuthError("Invalid username or password")

        with patch.object(api, "_authenticate_http", side_effect=mock_http_auth):
            with pytest.raises(SoCalGasAuthError, match="Invalid username or password"):
                asyncio.get_event_loop().run_until_complete(api.authenticate())

    def test_no_browser_url_raises_auth_error(self):
        """Test that missing SOCALGAS_BROWSER_URL raises a helpful error."""
        from custom_components.socalgas.api import SoCalGasAPI

        api = SoCalGasAPI("test@email.com", "testpassword")

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(SoCalGasAuthError, match="sidecar is not configured"):
                asyncio.get_event_loop().run_until_complete(
                    api._authenticate_browser()
                )
