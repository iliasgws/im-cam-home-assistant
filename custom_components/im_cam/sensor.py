"""Camera availability sensors."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    added = set()

    def add_new_devices():
        new_serials = set(coordinator.data) - added
        if new_serials:
            async_add_entities(ImCamSensor(coordinator, serial) for serial in new_serials)
            added.update(new_serials)

    add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(add_new_devices))


class ImCamSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:cctv"

    def __init__(self, coordinator, serial: str) -> None:
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{serial}_connection"
        self._attr_name = "Connection"

    @property
    def native_value(self):
        return self.coordinator.data.get(self._serial, {}).get("state", "unknown")

    @property
    def extra_state_attributes(self):
        device = self.coordinator.data.get(self._serial, {})
        return {"serial": self._serial, "online": device.get("online")}

    @property
    def device_info(self):
        device = self.coordinator.data.get(self._serial, {})
        return {
            "identifiers": {(DOMAIN, self._serial)},
            "name": device.get("alias", self._serial),
            "manufacturer": "IM CAM",
            "model": "Camera",
        }
