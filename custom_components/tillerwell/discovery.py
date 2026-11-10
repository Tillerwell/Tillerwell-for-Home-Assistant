"""Finding boxes on the network, and what to call them."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant
from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

SERVICE_TYPE = "_hearth-ha._tcp.local."
SCAN_SECONDS = 3


def title_for(device_name: str) -> str:
    """`hearth-a1b2c3` becomes `Hearth a1b2c3`."""
    prefix, sep, rest = device_name.partition("-")
    return f"{prefix.capitalize()} {rest}" if sep and rest else device_name


def box_name(info: dict[str, Any]) -> str:
    """The name the owner gave the box in the app (`display_name`), else `Hearth a1b2c3` from its id."""
    display = (info.get("display_name") or "").strip()
    return display or title_for(info["device_name"])


async def async_discover_hosts(hass: HomeAssistant) -> list[str]:
    """Browse the network for Hearth boxes for a few seconds and return their IPv4 addresses."""
    aiozc = await zeroconf.async_get_async_instance(hass)
    names: set[str] = set()

    def on_change(zeroconf: Any, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if state_change is not ServiceStateChange.Removed:
            names.add(name)

    browser = AsyncServiceBrowser(aiozc.zeroconf, SERVICE_TYPE, handlers=[on_change])
    try:
        await asyncio.sleep(SCAN_SECONDS)
    finally:
        await browser.async_cancel()

    hosts: list[str] = []
    for name in sorted(names):
        info = AsyncServiceInfo(SERVICE_TYPE, name)
        if await info.async_request(aiozc.zeroconf, 2000):
            hosts.extend(addr for addr in info.parsed_addresses(IPVersion.V4Only) if addr not in hosts)
    return hosts
