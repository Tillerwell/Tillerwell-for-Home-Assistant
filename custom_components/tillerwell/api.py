"""Client for the Hearth box's local HTTP API (see the firmware repo's docs and DEVICE_API.md)."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import aiohttp

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class HearthError(Exception):
    """Base error for the Hearth client."""


class HearthConnectionError(HearthError):
    """The box could not be reached or sent something unusable."""


class HearthAuthError(HearthError):
    """The box wants a web password and it was missing or wrong."""


class HearthRequestError(HearthError):
    """The box answered with an error and a reason (for example a rejected timer list)."""


class HearthClient:
    """Talks to one Hearth box. JSON over plain HTTP on port 80, HTTP Basic if a web password is set."""

    def __init__(self, session: aiohttp.ClientSession, host: str, password: str | None = None) -> None:
        self._session = session
        self._base = f"http://{host}"
        token = base64.b64encode(f"admin:{password}".encode()).decode() if password else None
        self._headers = {"Authorization": f"Basic {token}"} if token else None
        # The box's HTTP server is small; one request at a time keeps it happy.
        self._lock = asyncio.Lock()

    @property
    def host_url(self) -> str:
        """Base URL of the box, for the device page's "visit" link."""
        return self._base

    async def _request(self, method: str, path: str, body: Any = None) -> Any:
        async with self._lock:
            try:
                async with self._session.request(
                    method,
                    f"{self._base}{path}",
                    json=body,
                    headers=self._headers,
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    if resp.status == 401:
                        raise HearthAuthError("The box needs its web password")
                    if resp.status == 400:
                        try:
                            data = await resp.json(content_type=None)
                        except (aiohttp.ContentTypeError, ValueError):
                            data = None
                        reason = data.get("error") if isinstance(data, dict) else None
                        raise HearthRequestError(reason or "The box rejected the request")
                    if resp.status >= 400:
                        raise HearthConnectionError(f"The box answered HTTP {resp.status}")
                    return await resp.json(content_type=None)
            except (TimeoutError, aiohttp.ClientError, ValueError) as err:
                raise HearthConnectionError(f"Cannot reach the box: {err}") from err

    async def get_info(self) -> dict[str, Any]:
        """Identity and network facts: `device_name`, `version`, `rssi`, `web_password_set`, ..."""
        data = await self._request("GET", "/api/info")
        if not isinstance(data, dict) or "device_name" not in data:
            raise HearthConnectionError("That is not a Hearth box")
        return data

    async def get_status(self) -> dict[str, Any]:
        """The live status document."""
        data = await self._request("GET", "/api/status")
        if not isinstance(data, dict):
            raise HearthConnectionError("Unexpected status document")
        return data

    async def get_timers(self) -> list[dict[str, Any]]:
        """The saved timer list."""
        data = await self._request("GET", "/api/timers")
        if not isinstance(data, list):
            raise HearthConnectionError("Unexpected timer list")
        return data

    async def put_timers(self, timers: list[dict[str, Any]]) -> None:
        """Replace the whole timer list. Raises HearthRequestError with the box's reason if refused."""
        data = await self._request("PUT", "/api/timers", timers)
        if not (isinstance(data, dict) and data.get("ok")):
            raise HearthRequestError("The box did not accept the timers")

    async def command(self, cmd: str, **params: Any) -> bool:
        """Send one command. False means the box accepted the call but queued nothing."""
        data = await self._request("POST", "/api/cmd", {"cmd": cmd, **params})
        return bool(isinstance(data, dict) and data.get("ok"))

    async def reboot(self) -> None:
        """Restart the box."""
        await self._request("POST", "/api/reboot", {})
