"""SD/HD remote camera video quality selector."""

from homeassistant.components.select import SelectEntity

from .camera import media_for
from .const import DOMAIN
from .entity import ImCamEntity, async_add_camera_entities


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    media = media_for(coordinator)
    async_add_camera_entities(
        entry,
        coordinator,
        async_add_entities,
        lambda serial: [ImCamVideoQualitySelect(coordinator, serial, media)],
    )


class ImCamVideoQualitySelect(ImCamEntity, SelectEntity):
    """Select the camera's observed SD (1) or HD (2) remote stream profile."""

    _attr_options = ["SD", "HD"]
    _attr_name = "Video quality"
    _attr_icon = "mdi:high-definition-box"

    def __init__(self, coordinator, serial, media):
        super().__init__(coordinator, serial)
        self._media = media
        self._attr_unique_id = f"{serial}_video_quality"

    @property
    def current_option(self):
        return self._media.quality(self._serial)

    @property
    def available(self):
        return super().available and self.device.get("privacy") is not True

    async def async_select_option(self, option):
        await self._media.async_set_quality(self._serial, option)
        self.async_write_ha_state()
