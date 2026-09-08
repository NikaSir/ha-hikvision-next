"""Coordinators."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging

from homeassistant.components.switch import ENTITY_ID_FORMAT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import slugify

from .const import CONF_ALARM_SERVER_HOST, DOMAIN, HOLIDAY_MODE, STORAGE_DATA
from .isapi import ISAPIUnauthorizedError

SCAN_INTERVAL_EVENTS = timedelta(seconds=120)
SCAN_INTERVAL_HOLIDAYS = timedelta(minutes=60)

_LOGGER = logging.getLogger(__name__)


class HikvisionCoordinator(DataUpdateCoordinator):
    """Track the quality and age of each independent ISAPI sample."""

    def __init__(self, hass: HomeAssistant, device, name: str, update_interval: timedelta) -> None:
        """Initialize a Hikvision coordinator."""
        self.device = device
        self.failed_keys: set[str] = set()
        self.last_successful_update: dict[str, datetime] = {}

        super().__init__(
            hass,
            _LOGGER,
            config_entry=device.entry,
            name=f"{DOMAIN} {name}",
            update_interval=update_interval,
        )

    def _record_failure(
        self,
        failures: dict[str, Exception],
        key: str,
        ex: Exception,
        details: str,
    ) -> None:
        """Record one failed endpoint without repeatedly handling the same 401 poll."""
        auth_already_handled = any(isinstance(error, ISAPIUnauthorizedError) for error in failures.values())
        failures[key] = ex
        if not isinstance(ex, ISAPIUnauthorizedError) or not auth_already_handled:
            self.device.handle_exception(ex, details)

    def _complete_poll(
        self,
        data: dict,
        attempted: set[str],
        succeeded: set[str],
        failures: dict[str, Exception],
    ) -> dict:
        """Finish a poll, preserving old samples and rejecting a total failure."""
        self.failed_keys = set(failures)
        now = datetime.now(UTC)
        for key in succeeded:
            self.last_successful_update[key] = now

        if attempted and not succeeded:
            failure_types = ", ".join(sorted({type(ex).__name__ for ex in failures.values()}))
            raise UpdateFailed(
                f"All {len(attempted)} supported ISAPI poll requests failed ({failure_types})"
            ) from next(iter(failures.values()))

        if succeeded and not any(isinstance(ex, ISAPIUnauthorizedError) for ex in failures.values()):
            self.device.auth_token_expired = False
        return data

    def data_is_current(self, key: str) -> bool:
        """Return whether the latest poll produced a current value for one key."""
        return self.last_update_success and key not in self.failed_keys and key in self.last_successful_update


class EventsCoordinator(HikvisionCoordinator):
    """Manage fetching events state from NVR or camera."""

    def __init__(self, hass: HomeAssistant, device) -> None:
        """Initialize."""
        super().__init__(hass, device, "events", SCAN_INTERVAL_EVENTS)
        # Only poll storage when setup discovered devices for which entities exist.
        self._poll_storage = bool(device.storage)

    async def _async_update_data(self):
        """Update data via ISAPI."""
        data = dict(self.data or {})
        attempted: set[str] = set()
        succeeded: set[str] = set()
        failures: dict[str, Exception] = {}

        # Get camera event status
        for camera in self.device.cameras:
            for event in camera.events_info:
                if event.disabled:
                    continue
                _id = ENTITY_ID_FORMAT.format(event.unique_id)
                attempted.add(_id)
                try:
                    data[_id] = await self.device.get_event_enabled_state(event)
                except Exception as ex:  # pylint: disable=broad-except
                    self._record_failure(failures, _id, ex, f"Cannot fetch state for {event.id}")
                else:
                    succeeded.add(_id)

        # Get NVR event status
        for event in self.device.events_info:
            if event.disabled:
                continue
            _id = ENTITY_ID_FORMAT.format(event.unique_id)
            attempted.add(_id)
            try:
                data[_id] = await self.device.get_event_enabled_state(event)
            except Exception as ex:  # pylint: disable=broad-except
                self._record_failure(failures, _id, ex, f"Cannot fetch state for {event.id}")
            else:
                succeeded.add(_id)

        # Get output port(s) status
        for i in range(1, self.device.capabilities.output_ports + 1):
            _id = ENTITY_ID_FORMAT.format(f"{slugify(self.device.device_info.serial_no.lower())}_{i}_alarm_output")
            attempted.add(_id)
            try:
                data[_id] = await self.device.get_io_port_status("output", i)
            except Exception as ex:  # pylint: disable=broad-except
                self._record_failure(failures, _id, ex, f"Cannot fetch state for alarm output {i}")
            else:
                succeeded.add(_id)

        # Refresh HDD data
        if self._poll_storage:
            attempted.add(STORAGE_DATA)
            try:
                storage = await self.device.get_storage_devices()
            except Exception as ex:  # pylint: disable=broad-except
                self._record_failure(failures, STORAGE_DATA, ex, "Cannot fetch storage state")
            else:
                self.device.storage = storage
                data[STORAGE_DATA] = storage
                succeeded.add(STORAGE_DATA)

        return self._complete_poll(data, attempted, succeeded, failures)


class SecondaryCoordinator(HikvisionCoordinator):
    """Manage fetching events state from NVR."""

    def __init__(self, hass: HomeAssistant, device) -> None:
        """Initialize."""
        super().__init__(hass, device, "secondary", SCAN_INTERVAL_HOLIDAYS)

    async def _async_update_data(self):
        """Update data via ISAPI."""
        data = dict(self.data or {})
        attempted: set[str] = set()
        succeeded: set[str] = set()
        failures: dict[str, Exception] = {}

        if self.device.capabilities.support_holiday_mode:
            attempted.add(HOLIDAY_MODE)
            try:
                data[HOLIDAY_MODE] = await self.device.get_holiday_enabled_state()
            except Exception as ex:  # pylint: disable=broad-except
                self._record_failure(failures, HOLIDAY_MODE, ex, f"Cannot fetch state for {HOLIDAY_MODE}")
            else:
                succeeded.add(HOLIDAY_MODE)

        if self.device.capabilities.support_alarm_server:
            attempted.add(CONF_ALARM_SERVER_HOST)
            try:
                alarm_server = await self.device.get_alarm_server()
                data[CONF_ALARM_SERVER_HOST] = {
                    "protocol_type": alarm_server.protocol_type,
                    "address": alarm_server.ip_address or alarm_server.host_name,
                    "port_no": alarm_server.port_no,
                    "path": alarm_server.url,
                }
            except Exception as ex:  # pylint: disable=broad-except
                self._record_failure(
                    failures,
                    CONF_ALARM_SERVER_HOST,
                    ex,
                    f"Cannot fetch state for {CONF_ALARM_SERVER_HOST}",
                )
            else:
                succeeded.add(CONF_ALARM_SERVER_HOST)

        return self._complete_poll(data, attempted, succeeded, failures)
