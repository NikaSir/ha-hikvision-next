"""Sensor identifiers must be valid before HA registration and preserve identity."""

import pytest
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_next.const import DOMAIN, EVENTS_COORDINATOR, SECONDARY_COORDINATOR
from custom_components.hikvision_next.sensor import AlarmServerSensor, NOTIFICATION_HOST_KEYS, StorageSensor


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-2CD2T86G2-ISU"], indirect=True)
async def test_sensor_ids_are_valid_before_registration(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Do not rely on HA's deprecated repair of invalid suggested entity IDs."""
    device = init_integration.runtime_data
    sensors = [
        AlarmServerSensor(device.coordinators[SECONDARY_COORDINATOR], key)
        for key in NOTIFICATION_HOST_KEYS
    ]
    sensors.extend(
        StorageSensor(device.coordinators[EVENTS_COORDINATOR], item)
        for item in device.storage
    )
    assert len(sensors) >= 5
    for sensor in sensors:
        assert valid_entity_id(sensor.entity_id), sensor.entity_id
        # The registry key remains the original case-sensitive device identity.
        assert sensor.unique_id.startswith(device.device_info.serial_no + "_")


@pytest.mark.parametrize("init_integration", [("DS-2CD2T86G2-ISU", True)], indirect=True)
async def test_legacy_sensor_registry_entries_survive_setup_and_reload(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Retain old unique IDs, user names and a user-disabled storage sensor."""
    registry = er.async_get(hass)
    legacy_serial = "DS-2CD2T86G2-ISU/SL00000000AAWRAE0000000"
    expected = [
        (legacy_serial + "_alarm_server_address", "sensor.camera_alarm_address", "Camera alarm", None),
        (legacy_serial + "_1_hdde", "sensor.camera_archive_disk", "Camera archive", er.RegistryEntryDisabler.USER),
    ]
    registry_ids = {}
    for unique_id, entity_id, name, disabled_by in expected:
        registered = registry.async_get_or_create(
            "sensor",
            DOMAIN,
            unique_id,
            config_entry=init_integration,
            suggested_object_id="legacy_sensor",
            disabled_by=disabled_by,
        )
        registered = registry.async_update_entity(
            registered.entity_id, new_entity_id=entity_id, name=name
        )
        registry_ids[unique_id] = registered.id

    for _ in range(2):
        assert await hass.config_entries.async_setup(init_integration.entry_id)
        await hass.async_block_till_done()
        assert init_integration.state is ConfigEntryState.LOADED

        for unique_id, entity_id, name, disabled_by in expected:
            registered = registry.async_get(entity_id)
            assert registered is not None
            assert registered.id == registry_ids[unique_id]
            assert registered.unique_id == unique_id
            assert registered.name == name
            assert registered.disabled_by == disabled_by
            assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) == entity_id

        assert hass.states.get("sensor.camera_alarm_address").state == "ha.hostname.domain"
        assert hass.states.get("sensor.camera_archive_disk") is None
        assert hass.states.get(
            "sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_alarm_server_address"
        ) is None

        assert await hass.config_entries.async_unload(init_integration.entry_id)
        await hass.async_block_till_done()
