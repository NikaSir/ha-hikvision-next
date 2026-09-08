"""Events listener."""

from __future__ import annotations

import ipaddress
import logging
import socket
from http import HTTPStatus
from urllib.parse import urlparse

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONTENT_TYPE_TEXT_PLAIN, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_registry import async_get
from homeassistant.util import slugify
from requests_toolbelt.multipart import MultipartDecoder

from .const import ALARM_SERVER_PATH, DOMAIN, HIKVISION_EVENT
from .hikvision_device import HikvisionDevice
from .isapi import AlertInfo, IPCamera, ISAPIClient
from .isapi.const import EVENT_IO

_LOGGER = logging.getLogger(__name__)

CONTENT_TYPE = "Content-Type"
CONTENT_TYPE_XML = (
    "application/xml",
    'application/xml; charset="UTF-8"',
    "text/xml",
)
CONTENT_TYPE_TEXT_HTML = "text/html"
CONTENT_TYPE_IMAGE = "image/jpeg"


class UntrustedEventSourceError(ValueError):
    """Raised when an event notification did not come from a configured device."""


class EventNotificationsView(HomeAssistantView):
    """Event notifications listener."""

    def __init__(self, hass: HomeAssistant):
        """Initialize."""
        self.requires_auth = False
        self.url = ALARM_SERVER_PATH
        self.name = DOMAIN
        self.device: HikvisionDevice
        self.hass = hass

    async def post(self, request: web.Request):
        """Accept the POST request from NVR or IP Camera."""

        try:
            _LOGGER.debug("--- Incoming event notification ---")
            _LOGGER.debug("Source: %s", request.remote)
            xml = await self.parse_event_request(request)
            _LOGGER.debug("alert info: %s", xml)
            alert = ISAPIClient.parse_event_notification(xml)
            self.device = await self.get_isapi_device(request.remote, alert)
            self.update_alert_channel(alert)
            self.trigger_sensor(alert)
        except UntrustedEventSourceError as ex:
            _LOGGER.warning("Rejected incoming event notification: %s", ex)
            return web.Response(status=HTTPStatus.FORBIDDEN, content_type=CONTENT_TYPE_TEXT_PLAIN)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Cannot process incoming event %s", ex)

        response = web.Response(status=HTTPStatus.OK, content_type=CONTENT_TYPE_TEXT_PLAIN)
        return response

    async def get_isapi_device(self, device_ip: str | None, alert: AlertInfo) -> HikvisionDevice:
        """Get integration instance for device sending alert."""
        source_ip = self._normalize_ip(device_ip)
        integration_entries = [
            entry
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if not entry.disabled_by and entry.state is ConfigEntryState.LOADED
        ]

        resolved_hosts: dict[str, set[str]] = {}
        source_matches = []
        configured_hosts = []
        for entry in integration_entries:
            hostname = urlparse(entry.runtime_data.host).hostname
            if not hostname:
                continue

            configured_hosts.append(hostname)
            if hostname not in resolved_hosts:
                resolved_hosts[hostname] = await self._resolve_host_ips(hostname)
            if source_ip in resolved_hosts[hostname]:
                source_matches.append(entry)

        if not source_matches:
            raise UntrustedEventSourceError(
                f"source {source_ip} does not match a loaded Hikvision device ({configured_hosts})"
            )

        if len(source_matches) == 1:
            return source_matches[0].runtime_data

        # A MAC supplied inside the XML is not authentication. It is only safe
        # to use it for routing after the network source matched configured hosts.
        alert_mac = self._normalize_mac(alert.mac)
        mac_matches = [
            entry
            for entry in source_matches
            if alert_mac and self._normalize_mac(entry.runtime_data.device_info.mac_address) == alert_mac
        ]
        if len(mac_matches) == 1:
            return mac_matches[0].runtime_data

        raise UntrustedEventSourceError(
            f"source {source_ip} matches multiple Hikvision devices and cannot be identified"
        )

    @staticmethod
    def _normalize_ip(ip_string: str | None) -> str:
        """Return a canonical source address, including IPv4-mapped IPv6."""

        if not ip_string:
            raise UntrustedEventSourceError("event notification has no source address")

        try:
            address = ipaddress.ip_address(ip_string.split("%", 1)[0])
        except ValueError as ex:
            raise UntrustedEventSourceError(f"invalid event source address {ip_string!r}") from ex

        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return str(address)

    async def _resolve_host_ips(self, hostname: str) -> set[str]:
        """Resolve a configured device host without blocking the event loop."""

        try:
            return {self._normalize_ip(hostname)}
        except UntrustedEventSourceError:
            pass

        try:
            addresses = await self.hass.async_add_executor_job(
                socket.getaddrinfo,
                hostname,
                None,
                socket.AF_UNSPEC,
                socket.SOCK_STREAM,
            )
        except OSError as ex:
            _LOGGER.warning("Cannot resolve configured Hikvision host %s: %s", hostname, ex)
            return set()

        resolved = {self._normalize_ip(address[4][0]) for address in addresses}
        _LOGGER.debug("Resolved host %s to %s", hostname, sorted(resolved))
        return resolved

    @staticmethod
    def _normalize_mac(mac_address: str | None) -> str:
        """Normalize a MAC address for source-matched routing."""

        if not mac_address:
            return ""
        return "".join(character for character in mac_address.lower() if character.isalnum())

    async def parse_event_request(self, request: web.Request) -> str:
        """Extract XML content from multipart request or from simple request."""

        data = await request.read()

        content_type_header = request.headers.get(CONTENT_TYPE).strip()

        _LOGGER.debug("request headers: %s", request.headers)
        xml = None
        if content_type_header in CONTENT_TYPE_XML:
            xml = data.decode("utf-8")
        else:
            # "multipart/form-data; boundary=boundary"
            decoder = MultipartDecoder(data, content_type_header)
            for part in decoder.parts:
                headers = {}
                for key, value in part.headers.items():
                    assert isinstance(key, bytes)
                    headers[key.decode("ascii")] = value.decode("ascii")
                _LOGGER.debug("part headers: %s", headers)
                if headers.get(CONTENT_TYPE) in CONTENT_TYPE_XML:
                    xml = part.text
                if headers.get(CONTENT_TYPE) == CONTENT_TYPE_IMAGE:
                    _LOGGER.debug("image found")
                    # Use camera.snapshot service instead
                    # from datetime import datetime
                    # import aiofiles
                    # now = datetime.now()
                    # filename = f"/media/{DOMAIN}/snapshots/{now.strftime('%Y-%m-%d_%H-%M-%S_%f')}.jpg"
                    # async with aiofiles.open(filename, "wb") as image_file:
                    #     await image_file.write(part.content)
                    #     await image_file.flush()

        if not xml:
            raise ValueError(f"Unexpected event Content-Type {content_type_header}")
        return xml

    def update_alert_channel(self, alert: AlertInfo) -> AlertInfo:
        """Fix channel id for NVR/DVR alert."""

        if alert.channel_id > 32:
            # channel id above 32 is an IP camera
            # On DVRs that support analog cameras 33 may not be
            # camera 1 but camera 5 for example
            try:
                alert.channel_id = [
                    camera.id
                    for camera in self.device.cameras
                    if isinstance(camera, IPCamera) and camera.input_port == alert.channel_id - 32
                ][0]
            except IndexError:
                alert.channel_id = alert.channel_id - 32

    def trigger_sensor(self, alert: AlertInfo) -> None:
        """Determine entity and set binary sensor state."""

        _LOGGER.debug("Alert: %s", alert)

        serial_no = self.device.device_info.serial_no.lower()

        device_id_param = f"_{alert.channel_id}" if alert.channel_id != 0 and alert.event_id != EVENT_IO else ""
        io_port_id_param = f"_{alert.io_port_id}" if alert.io_port_id != 0 else ""
        unique_id = f"binary_sensor.{slugify(serial_no)}{device_id_param}{io_port_id_param}_{alert.event_id}"

        _LOGGER.debug("UNIQUE_ID: %s", unique_id)

        entity_registry = async_get(self.hass)
        entity_id = entity_registry.async_get_entity_id(Platform.BINARY_SENSOR, DOMAIN, unique_id)
        if entity_id:
            entity = self.hass.states.get(entity_id)
            if entity:
                self.hass.states.async_set(entity_id, STATE_ON, entity.attributes)
                self.fire_hass_event(alert)
            return
        raise ValueError(f"Entity not found {entity_id}")

    def fire_hass_event(self, alert: AlertInfo):
        """Fire HASS event."""
        camera_name = ""
        if camera := self.device.get_camera_by_id(alert.channel_id):
            camera_name = camera.name

        message = {
            "channel_id": alert.channel_id,
            "io_port_id": alert.io_port_id,
            "camera_name": camera_name,
            "event_id": alert.event_id,
        }
        if alert.detection_target:
            message["detection_target"] = alert.detection_target
            message["region_id"] = alert.region_id

        self.hass.bus.fire(
            HIKVISION_EVENT,
            message,
        )
