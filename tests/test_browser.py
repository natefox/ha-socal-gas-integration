"""Tests for the SoCal Gas browser authentication module."""
import asyncio
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
from custom_components.socalgas.browser import (  # noqa: E402
    _build_function_url,
    browser_authenticate,
)


def _make_mock_response(status: int, body: dict) -> AsyncMock:
    """Create a mock aiohttp response."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=body)
    resp.text = AsyncMock(return_value=str(body))
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


class TestBuildFunctionUrl:
    """Tests for _build_function_url helper."""

    def test_simple_url(self):
        assert _build_function_url("http://browserless:3000") == "http://browserless:3000/function?timeout=120000"

    def test_url_with_trailing_slash(self):
        assert _build_function_url("http://browserless:3000/") == "http://browserless:3000/function?timeout=120000"

    def test_url_with_token(self):
        result = _build_function_url("http://browserless:3000?token=secret")
        assert "http://browserless:3000/function?" in result
        assert "token=secret" in result
        assert "timeout=120000" in result

    def test_url_with_path_and_token(self):
        result = _build_function_url("http://browserless:3000/chrome?token=secret")
        assert "http://browserless:3000/chrome/function?" in result
        assert "token=secret" in result
        assert "timeout=120000" in result


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
                browser_authenticate("http://browserless:3000", "user@test.com", "pass")
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
                browser_authenticate("http://browserless:3000", "user@test.com", "pass")
            )

        assert token == "test-token-123"
        assert account == ""

    def test_auth_error_from_js(self):
        """Test that an auth error from JS raises SoCalGasAuthError."""
        mock_resp = _make_mock_response(200, {
            "error": "Login failed — page did not redirect.",
            "error_type": "auth",
        })
        mock_session = _make_mock_session(mock_resp)

        with patch("custom_components.socalgas.browser.aiohttp.ClientSession",
                   return_value=mock_session):
            with pytest.raises(SoCalGasAuthError, match="Login failed"):
                asyncio.get_event_loop().run_until_complete(
                    browser_authenticate("http://browserless:3000", "user@test.com", "wrong")
                )

    def test_connection_error_from_js(self):
        """Test that a connection error from JS raises SoCalGasConnectionError."""
        mock_resp = _make_mock_response(200, {
            "error": "Login redirected to error page",
            "error_type": "connection",
        })
        mock_session = _make_mock_session(mock_resp)

        with patch("custom_components.socalgas.browser.aiohttp.ClientSession",
                   return_value=mock_session):
            with pytest.raises(SoCalGasConnectionError, match="error page"):
                asyncio.get_event_loop().run_until_complete(
                    browser_authenticate("http://browserless:3000", "user@test.com", "pass")
                )

    def test_browserless_http_error(self):
        """Test that a non-200 from Browserless raises SoCalGasConnectionError."""
        mock_resp = _make_mock_response(500, {})
        mock_resp.text = AsyncMock(return_value="Internal Server Error")
        mock_session = _make_mock_session(mock_resp)

        with patch("custom_components.socalgas.browser.aiohttp.ClientSession",
                   return_value=mock_session):
            with pytest.raises(SoCalGasConnectionError, match="Browserless error"):
                asyncio.get_event_loop().run_until_complete(
                    browser_authenticate("http://browserless:3000", "user@test.com", "pass")
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
            with pytest.raises(SoCalGasConnectionError, match="Cannot connect to Browserless"):
                asyncio.get_event_loop().run_until_complete(
                    browser_authenticate("http://browserless:3000", "user@test.com", "pass")
                )

    def test_no_token_in_response(self):
        """Test that missing access_token in response raises SoCalGasAuthError."""
        mock_resp = _make_mock_response(200, {
            "account_number": "1234567890",
        })
        mock_session = _make_mock_session(mock_resp)

        with patch("custom_components.socalgas.browser.aiohttp.ClientSession",
                   return_value=mock_session):
            with pytest.raises(SoCalGasAuthError, match="Could not capture AccessToken"):
                asyncio.get_event_loop().run_until_complete(
                    browser_authenticate("http://browserless:3000", "user@test.com", "pass")
                )

    def test_sends_correct_payload(self):
        """Test that the correct JSON payload is sent to Browserless."""
        mock_resp = _make_mock_response(200, {
            "access_token": "test-token",
            "account_number": "1234567890",
        })
        mock_session = _make_mock_session(mock_resp)

        with patch("custom_components.socalgas.browser.aiohttp.ClientSession",
                   return_value=mock_session):
            asyncio.get_event_loop().run_until_complete(
                browser_authenticate("http://browserless:3000", "user@test.com", "mypass")
            )

        # Verify the post was called with correct URL and payload
        call_args = mock_session.post.call_args
        assert call_args[0][0] == "http://browserless:3000/function?timeout=120000"
        payload = call_args[1]["json"]
        assert "code" in payload
        assert payload["context"] == {"username": "user@test.com", "password": "mypass"}
