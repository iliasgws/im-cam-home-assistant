"""Standalone IM CAM Home Assistant integration."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ImCamApi
from .const import (
    CONF_ACCOUNT,
    CONF_ENABLE_TRANSCODING,
    CONF_PASSWORD,
    CONF_TRANSCODER_API_KEY,
    CONF_TRANSCODER_URL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TRANSCODER_URL,
    DOMAIN,
)

PLATFORMS = ["sensor", "switch", "button", "camera", "select"]
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = ImCamCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, coordinator.async_shutdown))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
        return True
    return False


class ImCamCoordinator(DataUpdateCoordinator):
    """Discover cameras and read their privacy settings."""

    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.entry = entry
        self.api = ImCamApi(async_get_clientsession(hass), entry.data[CONF_ACCOUNT], entry.data[CONF_PASSWORD])
        self.transcoding_enabled = entry.options.get(CONF_ENABLE_TRANSCODING, False)
        self.transcoder_url = entry.options.get(CONF_TRANSCODER_URL, DEFAULT_TRANSCODER_URL).rstrip("/")
        self.transcoder_api_key = entry.options.get(CONF_TRANSCODER_API_KEY, "")
        self._ptz_locks = {}

    def async_control_session(self):
        return self.api.control_session()

    async def async_shutdown(self, event=None):
        if media := getattr(self, "im_cam_rtc_media", None):
            await media.async_shutdown()

    async def _close_camera_media(self, serial):
        if media := getattr(self, "im_cam_rtc_media", None):
            for session_id, session in tuple(media._sessions.items()):
                if session.serial == serial:
                    await media.async_close_session(session_id)
            await media.async_close_transcoded_stream(serial)

    async def _async_update_data(self):
        try:
            async with self.async_control_session() as session:
                response = await session.request("cli_dev_list_ungroup", {
                    "time": 0, "type": 0, "page": 1, "num": 10000, "search": ""})
                body = response.get("body")
                rows = body.get("list") if isinstance(body, dict) else None
                if not isinstance(rows, list):
                    raise UpdateFailed("IM CAM device response has no device list")
                devices = {}
                for row in rows:
                    if not isinstance(row, dict) or not row.get("sn"):
                        continue
                    serial = row["sn"]
                    net = row.get("net")
                    online = None if net is None else str(net).lower() not in {"0", "false", "offline"}
                    privacy = None
                    if online is not False:
                        try:
                            privacy = await session.read_privacy(serial)
                        except Exception:
                            _LOGGER.debug("Privacy state could not be read for a camera")
                    devices[serial] = {
                        "alias": row.get("alias") or serial, "online": online,
                        "state": "unknown" if online is None else "online" if online else "offline",
                        "privacy": privacy,
                    }
                # Keep short-lived TURN credentials fresh for the next browser offer.
                if media := getattr(self, "im_cam_rtc_media", None):
                    for serial, device in devices.items():
                        if device.get("privacy") is True:
                            await self._close_camera_media(serial)
                    try:
                        await media.async_refresh_client_configuration()
                    except Exception:
                        _LOGGER.debug("WebRTC configuration refresh is unavailable")
                return devices
        except Exception as err:
            raise UpdateFailed("Unable to refresh IM CAM cameras") from err

    async def async_set_privacy(self, serial, enabled):
        try:
            async with self.async_control_session() as session:
                actual = await session.set_privacy(serial, enabled)
            if enabled:
                await self._close_camera_media(serial)
            data = {key: dict(value) for key, value in self.data.items()}
            data[serial]["privacy"] = actual
            self.async_set_updated_data(data)
            if actual is None:
                raise HomeAssistantError("Camera accepted privacy command but did not report its state; refresh and check the camera")
            if actual != enabled:
                raise HomeAssistantError("Camera has not confirmed the requested privacy state; refresh and try again")
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError("Unable to change IM CAM privacy mode") from err

    async def async_ptz(self, serial, direction):
        try:
            if direction == "stop":
                async with self.async_control_session() as session:
                    await session.ptz(serial, "stop")
                return
            lock = self._ptz_locks.setdefault(serial, asyncio.Lock())
            async with lock, self.async_control_session() as session:
                try:
                    await session.ptz(serial, direction)
                    await asyncio.sleep(0.5)
                finally:
                    # FORWARD messages may never acknowledge; stop is timed from send.
                    stop = asyncio.create_task(session.ptz(serial, "stop"))
                    try:
                        await asyncio.shield(stop)
                    except asyncio.CancelledError:
                        await stop
                        raise
        except Exception as err:
            raise HomeAssistantError("Unable to send IM CAM pan/tilt command") from err
