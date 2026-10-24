"""Short pan/tilt pulses and an immediate stop command."""
from homeassistant.components.button import ButtonEntity

from .api import DIRECTIONS
from .const import DOMAIN
from .entity import ImCamEntity, async_add_camera_entities

ICONS = {'up': 'mdi:arrow-up', 'down': 'mdi:arrow-down', 'left': 'mdi:arrow-left',
         'right': 'mdi:arrow-right', 'stop': 'mdi:stop'}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_camera_entities(entry, coordinator, async_add_entities,
                              lambda serial: [ImCamPtzButton(coordinator, serial, direction) for direction in DIRECTIONS])


class ImCamPtzButton(ImCamEntity, ButtonEntity):
    def __init__(self, coordinator, serial, direction):
        super().__init__(coordinator, serial)
        self._direction = direction
        self._attr_unique_id = f'{serial}_ptz_{direction}'
        self._attr_name = f'Pan/tilt {direction}'
        self._attr_icon = ICONS[direction]

    @property
    def available(self):
        return super().available and (self._direction == 'stop' or self.device.get('privacy') is not True)

    async def async_press(self):
        await self.coordinator.async_ptz(self._serial, self._direction)
