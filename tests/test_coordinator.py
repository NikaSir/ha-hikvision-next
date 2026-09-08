"""Tests for truthful coordinator failure handling."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from homeassistant.core import HomeAssistant

from custom_components.hikvision_next.const import CONF_ALARM_SERVER_HOST, HOLIDAY_MODE
from custom_components.hikvision_next.coordinator import EventsCoordinator, SecondaryCoordinator
from custom_components.hikvision_next.isapi import EventInfo, ISAPIUnauthorizedError
from custom_components.hikvision_next.isapi.models import AlarmServer
from custom_components.hikvision_next.sensor import AlarmServerSensor


def make_event(unique_id: str) -> EventInfo:
    """Return one enabled event fixture."""
    return EventInfo(
        id="motiondetection",
        channel_id=1,
        io_port_id=0,
        unique_id=unique_id,
        disabled=False,
    )


def make_device(*events: EventInfo):
    """Return the minimum device surface used by both coordinators."""
    return SimpleNamespace(
        entry=None,
        cameras=[SimpleNamespace(events_info=list(events))],
        events_info=[],
        capabilities=SimpleNamespace(
            output_ports=0,
            support_holiday_mode=False,
            support_alarm_server=False,
        ),
        device_info=SimpleNamespace(serial_no="TEST123"),
        storage=[],
        auth_token_expired=False,
        get_event_enabled_state=AsyncMock(),
        get_io_port_status=AsyncMock(),
        get_storage_devices=AsyncMock(),
        get_holiday_enabled_state=AsyncMock(),
        get_alarm_server=AsyncMock(),
        handle_exception=Mock(),
    )


async def test_events_total_failure_keeps_last_sample_and_recovers(hass: HomeAssistant) -> None:
    """A total failure makes entities unavailable without discarding their last data."""
    event = make_event("camera_motion")
    device = make_device(event)
    device.get_event_enabled_state.side_effect = [TimeoutError("offline"), False]
    coordinator = EventsCoordinator(hass, device)
    key = "switch.camera_motion"
    old_timestamp = datetime.now(UTC) - timedelta(minutes=5)
    coordinator.data = {key: True}
    coordinator.last_successful_update[key] = old_timestamp

    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.data == {key: True}
    assert coordinator.failed_keys == {key}
    assert coordinator.last_successful_update[key] == old_timestamp
    assert coordinator.data_is_current(key) is False

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data[key] is False
    assert coordinator.failed_keys == set()
    assert coordinator.last_successful_update[key] > old_timestamp
    assert coordinator.data_is_current(key) is True


async def test_events_partial_failure_updates_only_current_samples(hass: HomeAssistant) -> None:
    """A partial response retains failed values and marks only those values stale."""
    current_event = make_event("current_motion")
    failed_event = make_event("failed_motion")
    device = make_device(current_event, failed_event)
    device.get_event_enabled_state.side_effect = [True, TimeoutError("one endpoint failed")]
    coordinator = EventsCoordinator(hass, device)
    current_key = "switch.current_motion"
    failed_key = "switch.failed_motion"
    old_timestamp = datetime.now(UTC) - timedelta(minutes=5)
    coordinator.data = {current_key: False, failed_key: True}
    coordinator.last_successful_update = {
        current_key: old_timestamp,
        failed_key: old_timestamp,
    }

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data == {current_key: True, failed_key: True}
    assert coordinator.failed_keys == {failed_key}
    assert coordinator.data_is_current(current_key) is True
    assert coordinator.data_is_current(failed_key) is False
    assert coordinator.last_successful_update[current_key] > old_timestamp
    assert coordinator.last_successful_update[failed_key] == old_timestamp


async def test_secondary_total_and_partial_failure(hass: HomeAssistant) -> None:
    """The secondary coordinator rejects total failure and accepts a truthful partial sample."""
    device = make_device()
    device.capabilities.support_holiday_mode = True
    device.capabilities.support_alarm_server = True
    device.get_holiday_enabled_state.side_effect = TimeoutError("holiday offline")
    device.get_alarm_server.side_effect = [TimeoutError("alarm offline"), AlarmServer("1.0.0.1", "", 80, "/", "HTTP")]
    coordinator = SecondaryCoordinator(hass, device)
    old_host = {"protocol_type": "HTTP", "address": "1.0.0.2", "port_no": 80, "path": "/old"}
    coordinator.data = {HOLIDAY_MODE: False, CONF_ALARM_SERVER_HOST: old_host}
    old_timestamp = datetime.now(UTC) - timedelta(minutes=5)
    coordinator.last_successful_update = {
        HOLIDAY_MODE: old_timestamp,
        CONF_ALARM_SERVER_HOST: old_timestamp,
    }

    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.data[CONF_ALARM_SERVER_HOST] == old_host

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.failed_keys == {HOLIDAY_MODE}
    assert coordinator.data_is_current(HOLIDAY_MODE) is False
    assert coordinator.data_is_current(CONF_ALARM_SERVER_HOST) is True
    assert coordinator.data[CONF_ALARM_SERVER_HOST]["address"] == "1.0.0.1"


async def test_unsupported_secondary_endpoints_are_not_failures(hass: HomeAssistant) -> None:
    """An absent capability is not treated as a failed request."""
    device = make_device()
    coordinator = SecondaryCoordinator(hass, device)

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data == {}
    device.get_holiday_enabled_state.assert_not_awaited()
    device.get_alarm_server.assert_not_awaited()


async def test_unauthorized_poll_is_not_reported_as_success(hass: HomeAssistant) -> None:
    """Authentication failures make the coordinator unavailable."""
    event = make_event("auth_motion")
    device = make_device(event)
    request = httpx.Request("GET", "http://camera/ISAPI/event")
    response = httpx.Response(401, request=request)
    error = ISAPIUnauthorizedError(httpx.HTTPStatusError("unauthorized", request=request, response=response))
    device.get_event_enabled_state.side_effect = error
    coordinator = EventsCoordinator(hass, device)

    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.failed_keys == {"switch.auth_motion"}
    device.handle_exception.assert_called_once()


def test_alarm_server_sensor_handles_missing_payload() -> None:
    """A missing or malformed alarm-server payload returns unknown instead of raising."""
    coordinator = SimpleNamespace(
        data={},
        device=SimpleNamespace(
            device_info=SimpleNamespace(serial_no="TEST123"),
            hass_device_info=lambda: {},
        ),
    )
    sensor = AlarmServerSensor(coordinator, "address")

    assert sensor.native_value is None
    coordinator.data = {CONF_ALARM_SERVER_HOST: None}
    assert sensor.native_value is None

