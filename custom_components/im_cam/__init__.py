"""Standalone IM CAM Home Assistant integration."""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import re
import time
import uuid

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ACCOUNT,
    CONF_APP_VERSION,
    CONF_BUNDLE_ID,
    CONF_OEM_VID,
    CONF_PASSWORD,
    CONF_VID,
    CONF_VKEY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

PLATFORMS = ["sensor"]
_LOGGER = logging.getLogger(__name__)
DEFAULT_ENDPOINT = "wss://fast.jfgou.com:443/fast"
_PASSWORD_COMPLEXITY = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[A-Za-z\d\W_]{6,64}$")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = ImCamCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as err:
        raise ConfigEntryNotReady from err
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        return True
    return False


class ImCamCoordinator(DataUpdateCoordinator):
    """Poll the service directly over its WebSocket control API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.entry = entry

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            return await self._fetch_devices()
        except Exception as err:
            raise UpdateFailed("Unable to refresh IM CAM cameras") from err

    async def _request(self, ws, command: str, body: dict, sequence: int) -> dict:
        req_id = f"a_biz_{uuid.uuid4().hex[:6]}_{sequence}"
        await ws.send_json(
            {
                "headers": {
                    "callee": "",
                    "caller": "",
                    "id": command,
                    "req_id": req_id,
                    "time": int(time.time()),
                },
                "body": body,
            }
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            frame = await ws.receive(timeout=max(0.1, deadline - time.monotonic()))
            if frame.type.name == "ERROR":
                raise UpdateFailed("IM CAM WebSocket connection failed")
            if frame.type.name != "TEXT":
                continue
            try:
                message = json.loads(frame.data)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(message, dict):
                continue
            headers = message.get("headers") or {}
            if headers.get("req_id") != req_id:
                continue
            if message.get("ret", -1) != 0:
                raise UpdateFailed(f"IM CAM service rejected {command} (ret={message.get('ret')})")
            return message
        raise UpdateFailed(f"IM CAM request timed out: {command}")

    async def _login_and_list(self, endpoint: str) -> tuple[dict[str, dict], str | None]:
        data = self.entry.data
        session = async_get_clientsession(self.hass)
        async with session.ws_connect(endpoint, headers={"Origin": "https://fast.jfgou.com"}, heartbeat=30) as ws:
            await ws.send_json({"headers": {"id": "pub_heartbeat", "req_id": "1", "time": 1}})
            password = data[CONF_PASSWORD]
            password_hash = hashlib.md5(password.encode("utf-8")).hexdigest()
            simple_password = not bool(_PASSWORD_COMPLEXITY.fullmatch(password))
            login = await self._request(
                ws,
                "cli_login",
                {
                    "account": data[CONF_ACCOUNT],
                    "passwd": password_hash,
                    "sessid": "",
                    "simple_passwd": simple_password,
                    "language_type": 1,
                    "service_type": 0,
                    "device_token": "",
                    "sys_version": platform.release(),
                    "bundle_id": data[CONF_BUNDLE_ID],
                    "model": "Home Assistant",
                    "net": 1,
                    "os": 2,
                    "version": data[CONF_APP_VERSION],
                    "vid": data[CONF_VID],
                    "vkey": data[CONF_VKEY],
                    "oem_vid": data[CONF_OEM_VID],
                    "udid": "",
                },
                1,
            )
            body = login.get("body") or {}
            urls = body.get("url") or []
            regional_url = next((url for url in urls if isinstance(url, str) and url.startswith("wss://")), None)
            if body.get("switch_region"):
                if not regional_url or regional_url == endpoint:
                    raise UpdateFailed("IM CAM requested a region change without a usable endpoint")
                return {}, regional_url

            device_response = await self._request(
                ws,
                "cli_dev_list_ungroup",
                {"time": 0, "type": 0, "page": 1, "num": 10000, "search": ""},
                2,
            )
            rows = (device_response.get("body") or {}).get("list") or []
            devices = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                serial = row.get("sn")
                if not serial:
                    continue
                online = row.get("net")
                devices[serial] = {
                    "alias": row.get("alias") or serial,
                    "online": bool(online) if online is not None else None,
                    "state": "online" if online else "offline" if online is not None else "unknown",
                }
            return devices, None

    async def _fetch_devices(self) -> dict[str, dict]:
        endpoint = DEFAULT_ENDPOINT
        for _ in range(2):
            devices, next_endpoint = await self._login_and_list(endpoint)
            if next_endpoint is None:
                return devices
            endpoint = next_endpoint
        raise UpdateFailed("IM CAM requested multiple region changes")
