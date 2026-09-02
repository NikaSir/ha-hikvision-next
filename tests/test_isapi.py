"""Tests for specific ISAPI responses."""

from contextlib import suppress
from unittest.mock import AsyncMock

import httpx
import respx

from custom_components.hikvision_next.isapi import AnalogCamera, StorageInfo
from tests.conftest import load_fixture, mock_endpoint


def stream_response(stream_id: int) -> str:
    """Return a minimal valid StreamingChannel response."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<StreamingChannel>
  <id>{stream_id}</id>
  <channelName>Camera {stream_id}</channelName>
  <enabled>true</enabled>
  <Video>
    <videoCodecType>H.264</videoCodecType>
    <videoResolutionWidth>640</videoResolutionWidth>
    <videoResolutionHeight>360</videoResolutionHeight>
  </Video>
  <Audio><enabled>false</enabled></Audio>
</StreamingChannel>"""


@respx.mock
async def test_stream_discovery_retries_transient_forbidden(mock_isapi, monkeypatch):
    """A transient 403 must not permanently hide a required stream."""
    isapi = mock_isapi
    isapi.pending_initialization = True
    sleep = AsyncMock()
    monkeypatch.setattr("custom_components.hikvision_next.isapi.isapi.asyncio.sleep", sleep)

    route = respx.get(f"{isapi.host}/ISAPI/Streaming/channels/101")
    route.side_effect = [
        httpx.Response(403),
        httpx.Response(200, text=stream_response(101)),
    ]

    streams = await isapi.get_camera_streams(1, (1,), attempts=3)

    assert [stream.id for stream in streams] == [101]
    assert route.call_count == 2
    sleep.assert_awaited_once()


@respx.mock
async def test_required_streams_are_discovered_before_optional_streams(mock_isapi):
    """Unsupported optional streams must be queried after all main streams."""
    isapi = mock_isapi
    isapi.pending_initialization = True
    isapi.cameras = [
        AnalogCamera(1, "Camera 1", "Model", "serial-1", 1, "Direct"),
        AnalogCamera(2, "Camera 2", "Model", "serial-2", 2, "Direct"),
    ]

    for stream_id in (101, 102, 201, 202):
        respx.get(f"{isapi.host}/ISAPI/Streaming/channels/{stream_id}").respond(
            200,
            text=stream_response(stream_id),
        )
    for stream_id in (103, 104, 203, 204):
        respx.get(f"{isapi.host}/ISAPI/Streaming/channels/{stream_id}").respond(403)

    await isapi.discover_camera_streams()

    stream_requests = [
        int(call.request.url.path.rsplit("/", 1)[1])
        for call in respx.calls
        if "/Streaming/channels/" in call.request.url.path
    ]
    assert stream_requests == [101, 102, 201, 202, 103, 104, 203, 204]
    assert [[stream.id for stream in camera.streams] for camera in isapi.cameras] == [[101, 102], [201, 202]]


@respx.mock
async def test_storage(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("ContentMgmt/Storage", "hdd1")
    storage_list = await isapi.get_storage_devices()
    assert len(storage_list) == 1
    assert storage_list[0] == StorageInfo(
        id=1,
        name="hdd1",
        type="SATA",
        status="ok",
        capacity=1907729,
        freespace=0,
        property="RW",
        ip="",
    )

    mock_endpoint("ContentMgmt/Storage", "hdd1_nas1")
    storage_list = await isapi.get_storage_devices()
    assert len(storage_list) == 2
    assert storage_list[0].type == "SATA"
    assert storage_list[1].type == "NFS"
    assert storage_list[1].ip != ""

    mock_endpoint("ContentMgmt/Storage", status_code=500)
    with suppress(Exception):
        storage_list = await isapi.get_storage_devices()
        assert len(storage_list) == 0


@respx.mock
async def test_notification_hosts(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    host_nvr = await isapi.get_alarm_server()

    mock_endpoint("Event/notification/httpHosts", "ipc_list")
    host_ipc = await isapi.get_alarm_server()

    assert host_nvr == host_ipc


@respx.mock
async def test_update_notification_hosts(mock_isapi):
    isapi = mock_isapi

    def update_side_effect(request, route):
        payload = load_fixture("ISAPI/Event.notification.httpHosts", "set_alarm_server_payload")
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    url = f"{isapi.host}/ISAPI/Event/notification/httpHosts"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)
    await isapi.set_alarm_server("http://1.0.0.11:8123", "/api/hikvision")

    assert endpoint.called


@respx.mock
async def test_update_notification_hosts_from_ipaddress_to_hostname(mock_isapi):
    isapi = mock_isapi

    def update_side_effect(request, route):
        payload = load_fixture("ISAPI/Event.notification.httpHosts", "set_alarm_server_outside_network_payload")
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    url = f"{isapi.host}/ISAPI/Event/notification/httpHosts"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)
    await isapi.set_alarm_server("https://ha.hostname.domain", "/api/hikvision")

    assert endpoint.called
