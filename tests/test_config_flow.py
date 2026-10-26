"""Config flow tests."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tillerwell.const import DOMAIN

from .conftest import BASE, HOST, INFO

DISCOVER = "custom_components.tillerwell.config_flow.async_discover_hosts"


async def test_user_flow_picks_discovered_box(hass: HomeAssistant, box: AiohttpClientMocker) -> None:
    box.get("http://192.168.1.50/api/info", json=INFO)
    with patch(DISCOVER, return_value=["192.168.1.50"]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device": "192.168.1.50"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hearth a1b2c3"
    assert result["data"] == {"host": "192.168.1.50"}
    assert result["result"].unique_id == "hearth-a1b2c3"


async def test_user_flow_manual_after_scan(hass: HomeAssistant, box: AiohttpClientMocker) -> None:
    box.get("http://192.168.1.50/api/info", json=INFO)
    with patch(DISCOVER, return_value=["192.168.1.50"]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device": "manual"})
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"host": HOST}


async def test_user_flow_nothing_found_goes_manual(hass: HomeAssistant, box: AiohttpClientMocker) -> None:
    with patch(DISCOVER, return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"host": HOST}


async def test_scan_skips_configured_boxes(
    hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    box.get("http://192.168.1.50/api/info", json=INFO)
    with patch(DISCOVER, return_value=["192.168.1.50"]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["step_id"] == "manual"


async def test_manual_cannot_connect(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/api/info", exc=TimeoutError)
    with patch(DISCOVER, return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_manual_box_with_web_password_is_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{BASE}/api/info", status=401)
    with patch(DISCOVER, return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST})
    assert result["errors"] == {"base": "web_password_set"}


async def test_not_a_hearth_box(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/api/info", json={"hello": "world"})
    with patch(DISCOVER, return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_duplicate_updates_host(hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    with patch(DISCOVER, return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get("http://192.168.1.50/api/info", json=INFO)
    info = ZeroconfServiceInfo(
        ip_address="192.168.1.50",
        ip_addresses=["192.168.1.50"],
        hostname="hearth-a1b2c3.local.",
        name="Hearth._hearth._tcp.local.",
        port=80,
        properties={},
        type="_hearth._tcp.local.",
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_ZEROCONF}, data=info)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"host": "192.168.1.50"}


async def test_options_flow(hass: HomeAssistant, box: AiohttpClientMocker, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"external_temperature_entity": "sensor.cabin"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"external_temperature_entity": "sensor.cabin"}
