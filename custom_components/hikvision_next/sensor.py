"""Platform for sensor integration."""

from __future__ import annotations

from homeassistant.components.sensor import ENTITY_ID_FORMAT, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikvisionConfigEntry
from .const import CONF_ALARM_SERVER_HOST, EVENTS_COORDINATOR, SECONDARY_COORDINATOR, STORAGE_DATA
from .isapi import StorageInfo

NOTIFICATION_HOST_KEYS = [
    "protocol_type",
    "address", # ip_address or host_name
    "port_no",
    "path",
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add diagnostic sensors for hikvision alarm server settings and storage items."""

    device = entry.runtime_data
    secondary_coordinator = device.coordinators.get(SECONDARY_COORDINATOR)
    events_coordinator = device.coordinators.get(EVENTS_COORDINATOR)

    entities = []
    if secondary_coordinator and device.capabilities.support_alarm_server:
        for key in NOTIFICATION_HOST_KEYS:
            entities.append(AlarmServerSensor(secondary_coordinator, key))

    if events_coordinator:
        for item in list(device.storage):
            entities.append(StorageSensor(events_coordinator, item))

    if entities:
        async_add_entities(entities, True)


class AlarmServerSensor(CoordinatorEntity, SensorEntity):
    """Alarm Server settings sensor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:ip-network"

    def __init__(self, coordinator, key: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = f"{device.device_info.serial_no}_{CONF_ALARM_SERVER_HOST}_{key}"
        self.entity_id = ENTITY_ID_FORMAT.format(self.unique_id)
        self._attr_device_info = device.hass_device_info()
        self._attr_translation_key = f"notifications_host_{key}"
        self.key = key

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        host = (self.coordinator.data or {}).get(CONF_ALARM_SERVER_HOST)
        return host.get(self.key) if isinstance(host, dict) else None

    @property
    def available(self) -> bool:
        """Return whether the alarm server sample is current."""
        return super().available and self.coordinator.data_is_current(CONF_ALARM_SERVER_HOST)


class StorageSensor(CoordinatorEntity, SensorEntity):
    """HDD, NAS status sensor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:harddisk"

    def __init__(self, coordinator, hdd: StorageInfo) -> None:
        """Initialize."""
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = f"{device.device_info.serial_no}_{hdd.id}_{hdd.name}"
        self.entity_id = ENTITY_ID_FORMAT.format(self.unique_id)
        self._attr_device_info = device.hass_device_info()
        self._attr_name = f"{hdd.type} {hdd.name}"
        self.hdd = hdd

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        hdd = self._current_storage()
        return str(hdd.status).upper() if hdd else None

    @property
    def available(self) -> bool:
        """Return whether the storage sample is current and still contains this device."""
        return (
            super().available
            and self.coordinator.data_is_current(STORAGE_DATA)
            and self._current_storage() is not None
        )

    def _current_storage(self) -> StorageInfo | None:
        """Return this storage device from the last successful sample."""
        storage = (self.coordinator.data or {}).get(STORAGE_DATA, self.coordinator.device.storage)
        return next((item for item in storage if item.id == self.hdd.id), None)

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        hdd = self._current_storage() or self.hdd
        attrs = {}
        attrs["type"] = hdd.type
        attrs["capacity"] = hdd.capacity
        attrs["freespace"] = hdd.freespace
        if hdd.ip:
            attrs["ip"] = hdd.ip
        return attrs
