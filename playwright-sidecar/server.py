"""Playwright sidecar server for SoCal Gas browser authentication.

Runs headless Chromium to handle the login flow that requires JavaScript
execution (AccessToken is generated client-side). Called over HTTP by the
Home Assistant integration.
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web
from playwright.async_api import async_playwright

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_LOGGER = logging.getLogger(__name__)

MYACCOUNT_BASE = "https://myaccount.socalgas.com"
LOGIN_PAGE = f"{MYACCOUNT_BASE}/ui/login"
USAGE_PAGE = f"{MYACCOUNT_BASE}/ui/analyze-usage"

BROWSER_TIMEOUT_MS = 60_000


async def health(_request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.json_response({"status": "ok"})


async def authenticate(request: web.Request) -> web.Response:
    """Run headless browser login and return AccessToken + account number.

    POST /authenticate
    Body: {"username": "...", "password": "..."}
    Success: 200 {"access_token": "...", "account_number": "..."}
    Auth error: 401 {"error": "..."}
    Browser error: 500 {"error": "..."}
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    username = body.get("username")
    password = body.get("password")
    if not username or not password:
        return web.json_response(
            {"error": "username and password are required"}, status=400
        )

    try:
        access_token, account_number = await _browser_login(username, password)
        return web.json_response(
            {"access_token": access_token, "account_number": account_number}
        )
    except AuthError as err:
        return web.json_response({"error": str(err)}, status=401)
    except BrowserError as err:
        return web.json_response({"error": str(err)}, status=500)


class AuthError(Exception):
    """Login credentials are wrong."""


class BrowserError(Exception):
    """Browser automation failed."""


async def _browser_login(username: str, password: str) -> tuple[str, str]:
    """Login via headless Chromium, return (access_token, account_number).

    Flow:
    1. Launch headless Chromium
    2. Navigate to myaccount.socalgas.com/ui/login
    3. Fill username + password, submit
    4. Wait for redirect to home/dashboard
    5. Navigate to the usage analysis page (triggers SSO + AccessToken)
    6. Intercept requests to socal.smartcmobile.com to capture AccessToken header
    7. Extract account number from API calls
    8. Close browser, return token + account number
    """
    access_token: str | None = None
    account_number: str | None = None

    async def intercept_smartcmobile(route, request):
        """Capture AccessToken from outgoing requests to SmartCMobile."""
        nonlocal access_token

        headers = request.headers
        if "accesstoken" in headers:
            access_token = headers["accesstoken"]
            _LOGGER.debug("Captured AccessToken from request to %s", request.url)

        await route.continue_()

    async def handle_response(response):
        """Capture account number from API responses."""
        nonlocal account_number

        if "get-bill-account-list" in response.url and response.status == 200:
            try:
                data = await response.json()
                if isinstance(data, dict):
                    accounts = data.get("billAccounts", data.get("accounts", []))
                    if isinstance(accounts, list) and accounts:
                        acct = accounts[0]
                        if isinstance(acct, dict):
                            number = str(
                                acct.get("billAccountNumber", "")
                                or acct.get("accountNumber", "")
                            )
                            if len(number) == 11:
                                number = number[:10]
                            if number:
                                account_number = number
                                _LOGGER.debug("Captured account number: %s", number)
            except Exception:
                _LOGGER.debug("Could not parse account list response")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            # Set up request interception for SmartCMobile
            await page.route("**/*smartcmobile.com/**", intercept_smartcmobile)
            page.on("response", handle_response)

            # Step 1: Navigate to login page
            _LOGGER.debug("Navigating to login page")
            await page.goto(
                LOGIN_PAGE, wait_until="networkidle", timeout=BROWSER_TIMEOUT_MS
            )

            # Step 2: Fill credentials and submit
            _LOGGER.debug("Filling login credentials")
            await page.fill('#email', username)
            await page.fill('#password', password)
            await page.locator(
                'button:has-text("Log In"), button[type="submit"]'
            ).first.click()

            # Step 3: Wait for navigation away from login page
            _LOGGER.debug("Waiting for login redirect")
            try:
                await page.wait_for_url(
                    lambda url: "/ui/login" not in url,
                    timeout=BROWSER_TIMEOUT_MS,
                )
            except Exception:
                raise AuthError(
                    "Login failed — page did not redirect. "
                    "Check your username and password."
                )

            await page.wait_for_load_state(
                "networkidle", timeout=BROWSER_TIMEOUT_MS
            )
            _LOGGER.debug("Post-login URL: %s", page.url)

            # Check if we landed on an error page
            if "/ui/error" in page.url:
                raise BrowserError(
                    "Login redirected to error page — site may be "
                    "rate-limiting. Try again in a few minutes."
                )

            # Step 4: Navigate to usage page to trigger SSO + AccessToken
            _LOGGER.debug("Navigating to usage analysis page")
            await page.goto(
                USAGE_PAGE, wait_until="networkidle", timeout=BROWSER_TIMEOUT_MS
            )

            # Wait for SmartCMobile requests to fire
            await asyncio.sleep(3)
            await page.wait_for_load_state(
                "networkidle", timeout=BROWSER_TIMEOUT_MS
            )

            # If we still don't have the token, wait longer
            if not access_token:
                _LOGGER.debug("AccessToken not yet captured, waiting longer")
                await asyncio.sleep(5)

            await browser.close()

    except AuthError:
        raise
    except Exception as err:
        raise BrowserError(f"Browser automation failed: {err}") from err

    if not access_token:
        raise AuthError(
            "Could not capture AccessToken from browser session. "
            "The usage page may not have loaded the energy widget."
        )

    if not account_number:
        _LOGGER.warning(
            "Could not capture account number from browser; "
            "it will need to be obtained via API"
        )

    return access_token, account_number or ""


def main() -> None:
    """Start the sidecar server."""
    port = int(os.environ.get("PORT", "3000"))
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/authenticate", authenticate)
    _LOGGER.info("Starting Playwright sidecar on port %d", port)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
