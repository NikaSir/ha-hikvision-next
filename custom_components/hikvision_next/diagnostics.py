"""Diagnostics support for Wiser."""

from __future__ import annotations

import inspect
import json
import random
from typing import Any

from httpx import HTTPStatusError

from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry

from . import HikvisionConfigEntry
from .isapi import ISAPIForbiddenError, ISAPIUnauthorizedError
from .isapi.const import GET, STREAM_TYPE


def anonymise_mac(orignal: str):
    """Anonymise MAC address."""

    mac = [random.randint(0x00, 0xFF) for _ in range(6)]
    return ":".join("%02x" % x for x in mac)


def anonymise_ip(orignal: str):
    """Anonymise IP address."""
    if not orignal or orignal[0] == "0":
        return orignal
    return f"1.0.0.{random.randint(0x00, 0xff)}"


def anonymise_serial(orignal: str):
    """Anonymise serial number."""

    if len(orignal) > 32:
        return orignal[:12] + "".join("0" if c.isdigit() else c for c in orignal[12:])
    return "".join("0" if c.isdigit() else c for c in orignal)


ANON_KEYS = {
    "ip_address": anonymise_ip,
    "ip_addr": anonymise_ip,
    "ipAddress": anonymise_ip,
    "mac_address": anonymise_mac,
    "macAddress": anonymise_mac,
    "serial_no": anonymise_serial,
    "serialNumber": anonymise_serial,
    "subSerialNumber": anonymise_serial,
    "unique_id": anonymise_serial,
    "deviceID": anonymise_serial,
    "entity_id": anonymise_serial,
}

anon_map = {}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: HikvisionConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return await _async_get_diagnostics(hass, entry)


@callback
async def _async_get_diagnostics(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    device: DeviceEntry | None = None,
) -> dict[str, Any]:
    device = entry.runtime_data

    # Get info set
    info = {}

    # Include the post-setup runtime and registry state. Raw ISAPI data alone
    # cannot distinguish stream discovery failures from entity/device registry
    # conflicts.
    info["runtime"] = {
        "cameras": [
            {
                "id": camera.id,
                "name": camera.name,
                "serial_no": camera.serial_no,
                "streams": [
                    {
                        "id": stream.id,
                        "type_id": stream.type_id,
                        "enabled": stream.enabled,
                        "codec": stream.codec,
                        "width": stream.width,
                        "height": stream.height,
                    }
                    for stream in camera.streams
                ],
            }
            for camera in device.cameras
        ],
        "camera_entities": [],
        "devices": [],
    }

    entity_registry = er.async_get(hass)
    info["runtime"]["camera_entities"] = [
        {
            "entity_id": registry_entry.entity_id,
            "unique_id": registry_entry.unique_id,
            "disabled_by": (registry_entry.disabled_by.value if registry_entry.disabled_by is not None else None),
            "device_id": registry_entry.device_id,
            "active": hass.states.get(registry_entry.entity_id) is not None,
        }
        for registry_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if registry_entry.domain == CAMERA_DOMAIN and registry_entry.platform == entry.domain
    ]

    device_registry = dr.async_get(hass)
    info["runtime"]["devices"] = [
        {
            "id": registry_device.id,
            "kind": type(registry_device).__name__,
            "name": registry_device.name,
            "identifiers": [
                {"domain": domain, "serial_no": identifier}
                for domain, identifier in sorted(registry_device.identifiers)
            ],
            "via_device_id": getattr(registry_device, "via_device_id", None),
            "parent_device_id": getattr(registry_device, "parent_device_id", None),
        }
        for registry_device in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    ]
    info["runtime"] = anonymise_data(info["runtime"])

    # ISAPI responses
    responses = {}
    endpoints = [
        "System/deviceInfo",
        "System/capabilities",
        "System/IO/inputs/1/status",
        "System/IO/outputs/1/status",
        "System/Holidays",
        "System/Video/inputs/channels",
        "ContentMgmt/InputProxy/channels",
        "ContentMgmt/Storage",
        "Security/adminAccesses",
        "Event/triggers",
        "Event/channels/capabilities",
        "Event/triggers/scenechangedetection-1",
        "Event/notification/httpHosts",
        "Streaming/channels",
    ]

    for endpoint in endpoints:
        responses[endpoint] = await get_isapi_data(device, endpoint)

    # channels
    for camera in device.cameras:
        for stream_type_id in STREAM_TYPE:
            endpoint = f"Streaming/channels/{camera.id}0{stream_type_id}"
            responses[endpoint] = await get_isapi_data(device, endpoint)

    # event states
    for camera in device.cameras:
        for event in camera.events_info:
            responses[event.url] = await get_isapi_data(device, event.url)

    info["ISAPI"] = responses
    return info


async def get_isapi_data(isapi, endpoint: str) -> dict:
    """Get data from ISAPI."""
    entry = {}
    try:
        response = await isapi.request(GET, endpoint)
        entry["response"] = anonymise_data(response)
    except (HTTPStatusError, ISAPIUnauthorizedError, ISAPIForbiddenError) as ex:
        entry["status_code"] = ex.response.status_code
    except Exception as ex:  # noqa: BLE001
        entry["error"] = type(ex).__name__
    return entry


def to_json(obj):
    """Convert object to json."""
    result = json.dumps(obj, cls=ObjectEncoder, sort_keys=True, indent=2)
    result = json.loads(result)
    return anonymise_data(result)


def anonymise_data(data):
    """Anonymise sensitive data."""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key in ANON_KEYS and value is not None:
                if value in anon_map:
                    result[key] = anon_map[value]
                else:
                    anon_fn = ANON_KEYS[key]
                    anon_map[value] = result[key] = anon_fn(value)
            else:
                result[key] = anonymise_data(value)
        return result
    if isinstance(data, list):
        result = []
        for item in data:
            result.append(anonymise_data(item))
        return result
    return data


class ObjectEncoder(json.JSONEncoder):
    """Class to encode object to json."""

    def default(self, o):
        """Implement encoding logic."""
        if hasattr(o, "to_json"):
            return self.default(o.to_json())

        if hasattr(o, "__dict__"):
            data = {
                key: value
                for key, value in inspect.getmembers(o)
                if not key.startswith("__")
                and not inspect.isabstract(value)
                and not inspect.isbuiltin(value)
                and not inspect.isfunction(value)
                and not inspect.isgenerator(value)
                and not inspect.isgeneratorfunction(value)
                and not inspect.ismethod(value)
                and not inspect.ismethoddescriptor(value)
                and not inspect.isroutine(value)
            }
            return self.default(data)
        return o
