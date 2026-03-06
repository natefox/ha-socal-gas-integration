"""SoCal Gas API client for automated data fetching."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import aiohttp

_LOGGER = logging.getLogger(__name__)

SMARTCMOBILE_BASE = "https://socal.smartcmobile.com"

ACCOUNT_LIST_URL = f"{SMARTCMOBILE_BASE}/connectorsso/api/account/list"
GNN_MAPPING_URL = f"{SMARTCMOBILE_BASE}/connectorsso/api/usage/gnnmapping"
GREEN_BUTTON_URL = (
    f"{SMARTCMOBILE_BASE}/greenbuttonservices/api/greenbutton/zipfile"
)

SMARTCMOBILE_HEADERS = {
    "PortalType": "R",
    "Module": "",
    "X-SEW-CallerType": "socal",
}


class SoCalGasAuthError(Exception):
    """Raised when authentication fails."""


class SoCalGasConnectionError(Exception):
    """Raised when a connection error occurs."""


@dataclass
class AccountInfo:
    """Account and meter information discovered during login."""

    account_number: str  # 10-digit account number
    meter_number: str
    gnn_id: str
    service_location_id: str


class SoCalGasAPI:
    """Client for the SoCal Gas API.

    Authentication is handled by Browserless Chrome (browser
    automation via /function API) which captures the AccessToken.
    Data downloads use plain HTTP with the captured token.
    """

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
        browserless_url: str | None = None,
    ) -> None:
        """Initialize the API client."""
        self._username = username
        self._password = password
        self._external_session = session is not None
        self._session = session
        self._browserless_url = browserless_url
        self._access_token: str | None = None
        self._account_info: AccountInfo | None = None

    @property
    def account_info(self) -> AccountInfo | None:
        """Return discovered account info."""
        return self._account_info

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp session with cookie jar."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar()
            )
        return self._session

    async def close(self) -> None:
        """Close the session if we own it."""
        if not self._external_session and self._session and not self._session.closed:
            await self._session.close()

    async def authenticate(self) -> AccountInfo:
        """Perform full authentication flow and return account info.

        Uses Browserless Chrome to handle the socalgas.com login
        (which requires client-side JavaScript).
        """
        from .browser import browser_authenticate

        if not self._browserless_url:
            raise SoCalGasAuthError(
                "Browserless Chrome is not configured. "
                "Set the Browserless URL in the integration config "
                "(e.g. http://browserless:3000) and ensure the Browserless "
                "container or add-on is running."
            )

        access_token, account_number = await browser_authenticate(
            self._browserless_url, self._username, self._password
        )
        self._access_token = access_token

        # If browser didn't capture account number, get it via SmartCMobile API
        if not account_number:
            session = await self._ensure_session()
            account_number = await self._get_account_number_smartcmobile(
                session
            )

        # Use the captured token for GNN mapping via plain HTTP
        session = await self._ensure_session()
        account_info = await self._get_gnn_mapping(session, account_number)
        self._account_info = account_info
        return account_info

    async def _get_account_number_smartcmobile(
        self, session: aiohttp.ClientSession
    ) -> str:
        """Get the account number via SmartCMobile using the AccessToken."""
        try:
            async with session.post(
                ACCOUNT_LIST_URL,
                headers={
                    **SMARTCMOBILE_HEADERS,
                    "AccessToken": self._access_token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            ) as resp:
                _LOGGER.debug(
                    "SmartCMobile account list status: %s", resp.status
                )
                if resp.status == 200:
                    data = await resp.json()
                    _LOGGER.debug("SmartCMobile account list response: %s", data)
                    # Response is a list of account objects
                    accounts = data if isinstance(data, list) else data.get(
                        "billAccounts", data.get("accounts", [])
                    ) if isinstance(data, dict) else []
                    if accounts:
                        acct = accounts[0]
                        if isinstance(acct, dict):
                            number = str(
                                acct.get("Id", "")
                                or acct.get("BillAccount", "")
                                or acct.get("billAccountNumber", "")
                                or acct.get("accountNumber", "")
                            )
                            if len(number) == 11:
                                number = number[:10]
                            if number:
                                return number
        except (aiohttp.ClientError, ValueError) as err:
            _LOGGER.debug("Could not get SmartCMobile account list: %s", err)

        raise SoCalGasAuthError(
            "Could not determine account number. "
            "Please provide it manually or use file upload."
        )

    async def _get_gnn_mapping(
        self, session: aiohttp.ClientSession, account_number: str
    ) -> AccountInfo:
        """Get GNN mapping to find meter number and GNN ID."""
        try:
            async with session.post(
                GNN_MAPPING_URL,
                json={"BillAccount": account_number},
                headers={
                    **SMARTCMOBILE_HEADERS,
                    "AccessToken": self._access_token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            ) as resp:
                if resp.status != 200:
                    raise SoCalGasConnectionError(
                        f"GNN mapping failed with status {resp.status}"
                    )
                data = await resp.json()
                _LOGGER.debug("GNN mapping response: %s", data)

                # Parse the response - structure may vary
                if isinstance(data, dict) and "GnnMeterMap" in data:
                    # Wrapped format: {"GnnMeterMap": [{...}]}
                    meter_list = data["GnnMeterMap"]
                    if isinstance(meter_list, list) and meter_list:
                        mapping = meter_list[0]
                    else:
                        raise SoCalGasConnectionError(
                            f"Empty GnnMeterMap in response: {data}"
                        )
                elif isinstance(data, list) and data:
                    mapping = data[0]
                elif isinstance(data, dict):
                    mapping = data
                else:
                    raise SoCalGasConnectionError(
                        f"Unexpected GNN mapping response: {data}"
                    )

                gnn_id = str(
                    mapping.get("GnnId", "")
                    or mapping.get("gnnId", "")
                )
                return AccountInfo(
                    account_number=account_number,
                    meter_number=str(
                        mapping.get("MeterNumber", "")
                        or mapping.get("meterNumber", "")
                    ),
                    gnn_id=gnn_id,
                    service_location_id=str(
                        mapping.get("ServiceLocationId", "")
                        or mapping.get("serviceLocationId", "")
                        or gnn_id
                    ),
                )
        except aiohttp.ClientError as err:
            raise SoCalGasConnectionError(
                f"GNN mapping error: {err}"
            ) from err

    async def download_green_button(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> bytes:
        """Download Green Button ZIP data for the given date range.

        Args:
            start_date: Start of the data range.
            end_date: End of the data range.

        Returns:
            ZIP file content as bytes.
        """
        if not self._access_token or not self._account_info:
            raise SoCalGasAuthError("Must authenticate before downloading")

        session = await self._ensure_session()
        info = self._account_info

        request_body = {
            "MeterNumber": info.meter_number,
            "AccountNumber": info.account_number,
            "StartDate": start_date.strftime("%m/%d/%y"),
            "EndDate": end_date.strftime("%m/%d/%y"),
            "CustomAttribute": {
                "GnnId": info.gnn_id,
                "ServiceLocationId": info.service_location_id,
            },
            "ServiceType": "GAS",
            "Type": "HOURLY",
        }

        _LOGGER.info(
            "Green Button download: %s to %s",
            start_date.strftime("%m/%d/%y"), end_date.strftime("%m/%d/%y"),
        )

        try:
            async with session.post(
                GREEN_BUTTON_URL,
                json=request_body,
                headers={
                    **SMARTCMOBILE_HEADERS,
                    "AccessToken": self._access_token,
                    "Content-Type": "application/json",
                    "Accept": "application/zip, application/json",
                },
            ) as resp:
                if resp.status == 401:
                    raise SoCalGasAuthError("AccessToken expired")
                if resp.status != 200:
                    text = await resp.text()
                    raise SoCalGasConnectionError(
                        f"Green Button download failed ({resp.status}): {text}"
                    )
                return await resp.read()
        except aiohttp.ClientError as err:
            raise SoCalGasConnectionError(
                f"Green Button download error: {err}"
            ) from err
