"""Privacy mode switches, with state read back from DP 532."""
from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .entity import ImCamEntity, async_add_camera_entities


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_camera_entities(entry, coordinator, async_add_entities,
                              lambda serial: [ImCamPrivacySwitch(coordinator, serial)])


class ImCamPrivacySwitch(ImCamEntity, SwitchEntity):
    _attr_name = 'Privacy mode'
    _attr_icon = 'mdi:camera-off'

    def __init__(self, coordinator, serial):
        super().__init__(coordinator, serial)
        self._attr_unique_id = f'{serial}_privacy'

    @property
    def available(self):
        return super().available and isinstance(self.device.get('privacy'), bool)

    @property
    def is_on(self):
        return self.device.get('privacy')

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_privacy(self._serial, True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_privacy(self._serial, False)
