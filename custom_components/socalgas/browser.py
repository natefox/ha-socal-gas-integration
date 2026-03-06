"""Browser authentication via Playwright sidecar container.

Sends credentials to the Playwright sidecar over HTTP. The sidecar runs
headless Chromium to handle the login flow and returns the AccessToken.

Once the AccessToken is obtained, all subsequent SmartCMobile API calls
(gnnmapping, Green Button download) work with plain HTTP.
"""
from __future__ import annotations

import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)


async def browser_authenticate(
    browser_url: str, username: str, password: str
) -> tuple[str, str]:
    """Authenticate via Playwright sidecar container.

    Args:
        browser_url: Base URL of the sidecar (e.g. http://playwright:3000).
        username: SoCal Gas username/email.
        password: SoCal Gas password.

    Returns:
        Tuple of (access_token, account_number).

    Raises:
        SoCalGasAuthError: If login credentials are wrong.
        SoCalGasConnectionError: If the sidecar is unreachable or fails.
    """
    from .api import SoCalGasAuthError, SoCalGasConnectionError

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{browser_url}/authenticate",
                json={"username": username, "password": password},
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resp:
                if resp.status == 401:
                    data = await resp.json()
                    raise SoCalGasAuthError(
                        data.get("error", "Login failed")
                    )
                if resp.status != 200:
                    data = await resp.json()
                    raise SoCalGasConnectionError(
                        data.get("error", "Browser error")
                    )
                data = await resp.json()
                return data["access_token"], data.get("account_number", "")
    except SoCalGasAuthError:
        raise
    except SoCalGasConnectionError:
        raise
    except Exception as err:
        raise SoCalGasConnectionError(
            f"Cannot connect to Playwright sidecar at {browser_url}: {err}"
        ) from err
