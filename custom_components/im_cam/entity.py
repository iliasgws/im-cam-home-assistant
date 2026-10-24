"""Shared device identity and entity discovery."""
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def async_add_camera_entities(entry, coordinator, async_add_entities, factory):
    added = set()

    def add_new_devices():
        new = set(coordinator.data or {}) - added
        if new:
            async_add_entities(entity for serial in new for entity in factory(serial))
            added.update(new)

    add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(add_new_devices))


class ImCamEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, serial):
        super().__init__(coordinator)
        self._serial = serial

    @property
    def device(self):
        return (self.coordinator.data or {}).get(self._serial, {})

    @property
    def available(self):
        return super().available and bool(self.device) and self.device.get('online') is not False

    @property
    def device_info(self):
        return {
            'identifiers': {(DOMAIN, self._serial)},
            'name': self.device.get('alias', self._serial),
            'manufacturer': 'IM CAM', 'model': 'Camera',
        }
