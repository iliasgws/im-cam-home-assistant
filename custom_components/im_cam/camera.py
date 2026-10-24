"""Native Home Assistant WebRTC camera entities for IM CAM."""

from homeassistant.components.camera import Camera, CameraEntityFeature, WebRTCError
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .entity import ImCamEntity, async_add_camera_entities
from .rtc_media import ImCamRtcMedia


def media_for(coordinator):
    """Share signaling sessions and quality state between camera/select entities."""
    manager = getattr(coordinator, "im_cam_rtc_media", None)
    if manager is None:
        manager = coordinator.im_cam_rtc_media = ImCamRtcMedia(coordinator)
    return manager


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_camera_entities(
        entry,
        coordinator,
        async_add_entities,
        lambda serial: [ImCamCamera(coordinator, serial, media_for(coordinator))],
    )


class ImCamCamera(ImCamEntity, Camera):
    """Receive-only live video and audio from one IM CAM camera."""

    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_brand = "IM CAM"
    _attr_model = "Camera"
    _attr_icon = "mdi:cctv"

    def __init__(self, coordinator, serial, media):
        super().__init__(coordinator, serial)
        Camera.__init__(self)
        self._media = media
        self._attr_unique_id = f"{serial}_camera"
        self._attr_name = "Camera"

    @property
    def available(self):
        return (
            super().available
            and self.device.get("privacy") is not True
            and self.device.get("state") != STATE_UNAVAILABLE
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # The frontend needs the service TURN/STUN endpoints before it creates
        # its offer. Keep them only in this manager's private in-memory state.
        try:
            await self._media.async_refresh_client_configuration()
        except Exception:
            # Offer handling retries and surfaces a safe HA error if bootstrap fails.
            pass

    def _async_get_webrtc_client_configuration(self):
        return self._media.client_configuration()

    async def async_handle_async_webrtc_offer(self, offer_sdp, session_id, send_message):
        try:
            await self._media.async_handle_offer(self._serial, offer_sdp, session_id, send_message)
        except Exception as err:
            # Camera WebRTC errors are out of band; do not leave the browser's
            # player waiting on a websocket command timeout.
            message = str(err) if isinstance(err, HomeAssistantError) else "Unable to start the IM CAM live stream."
            send_message(WebRTCError("im_cam_offer_failed", message))

    async def async_on_webrtc_candidate(self, session_id, candidate):
        await self._media.async_on_candidate(session_id, candidate)

    def close_webrtc_session(self, session_id):
        self._media.close_session(session_id)

    async def async_will_remove_from_hass(self):
        for session_id, session in tuple(self._media._sessions.items()):
            if session.serial == self._serial:
                await self._media.async_close_session(session_id)
        await super().async_will_remove_from_hass()
