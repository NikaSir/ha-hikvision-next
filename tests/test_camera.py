"""Tests for camera platform."""

import pytest
import respx
import httpx
from homeassistant.core import HomeAssistant
from homeassistant.const import STATE_IDLE
from homeassistant.components.camera.helper import get_camera_from_entity_id
from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.helpers import device_registry as dr
from custom_components.hikvision_next.hikvision_device import HikvisionDevice
from custom_components.hikvision_next.const import DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests.conftest import load_fixture
from tests.conftest import mock_device_endpoints
from tests.conftest import TEST_HOST
import homeassistant.helpers.entity_registry as er


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera initialization."""

    # The fourth NVR channel has no readable stream metadata in this fixture,
    # but still receives a main-stream entity through the standard-ID fallback.
    assert len(hass.states.async_entity_ids(CAMERA_DOMAIN)) == 4

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101"
    assert hass.states.get(entity_id)

    camera_entity = get_camera_from_entity_id(hass, entity_id)
    assert camera_entity.state == STATE_IDLE
    assert camera_entity.name == "garden"

    stream_url = await camera_entity.stream_source()
    assert stream_url == "rtsp://u1:%2A%2A%2A@1.0.0.255:10554/Streaming/channels/101"

    entity_registry = er.async_get(hass)
    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_102"
    camera_entity = entity_registry.async_get(entity_id)
    assert camera_entity.disabled
    assert camera_entity.original_name == "Sub-Stream"

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_104"
    camera_entity = entity_registry.async_get(entity_id)
    assert camera_entity.disabled
    assert camera_entity.original_name == "Transcoded Stream"


@pytest.mark.skipif(
    not hasattr(dr.DeviceRegistry, "async_get_or_create_child"),
    reason="Home Assistant child-device registry was introduced in 2026.9",
)
async def test_camera_with_existing_child_devices(
    hass: HomeAssistant,
    respx_mock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Camera entities must reuse devices already stored as child devices."""
    model = "DS-7608NXI-I2"
    mock_device_endpoints(model, mock_config_entry.data["host"])
    mock_config_entry.add_to_hass(hass)

    nvr_serial = "DS-7608NXI-I0/0P/S0000000000CCRRJ00000000WCVU"
    camera_serials = {
        2: "DS-2CD2386G2-IU00000000AAWRK00000002",
        3: "0000000000000-0C0F000000DD",
        4: f"{nvr_serial}_HIKVISION_4",
    }
    device_registry = dr.async_get(hass)
    parent = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, nvr_serial)},
        name="Network Video Recorder",
    )
    child_devices = {
        camera_id: device_registry.async_get_or_create_child(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, serial_no)},
            name=f"Camera {camera_id}",
            parent_device_id=parent.id,
        )
        for camera_id, serial_no in camera_serials.items()
    }

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert len(hass.states.async_entity_ids(CAMERA_DOMAIN)) == 4
    entity_registry = er.async_get(hass)
    for camera_id, child_device in child_devices.items():
        entity_id = f"camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_{camera_id}01"
        assert (camera_entry := entity_registry.async_get(entity_id))
        assert camera_entry.device_id == child_device.id


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera_snapshot(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera snapshot."""

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    respx.get(image_url).respond(content=b"binary image data")
    image = await camera_entity.async_camera_image()
    assert image == b"binary image data"


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera_snapshot_without_discovered_resolution(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Fallback streams must request a snapshot without invalid zero dimensions."""
    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)
    camera_entity.stream_info.width = 0
    camera_entity.stream_info.height = 0

    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    route = respx.get(image_url).respond(content=b"binary image data")
    image = await camera_entity.async_camera_image()

    assert image == b"binary image data"
    assert route.calls.last.request.url.query == b""


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_camera_snapshot_device_error(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera snapshot with 2 attempts."""

    entity_id = "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    route = respx.get(image_url)
    error_response = load_fixture("ISAPI/Streaming.channels.x0y.picture", "deviceError")
    route.side_effect = [
        httpx.Response(200, content=error_response),
        httpx.Response(200, content=error_response),
        httpx.Response(200, content=b"binary image data"),
    ]
    image = await camera_entity.async_camera_image()
    assert image == b"binary image data"


@respx.mock
@pytest.mark.parametrize("init_integration", ["DS-7616NI-Q2"], indirect=True)
async def test_camera_snapshot_alternate_url(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera snapshot with alternate url."""

    entity_id = "camera.ds_7616ni_q2_00p0000000000ccrre00000000wcvu_101"
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    error_response = load_fixture("ISAPI/Streaming.channels.x0y.picture", "badXmlContent")
    image_url = f"{TEST_HOST}/ISAPI/Streaming/channels/101/picture"
    respx.get(image_url).respond(content=error_response)
    image_url = f"{TEST_HOST}/ISAPI/ContentMgmt/StreamingProxy/channels/101/picture"
    respx.get(image_url).respond(content=b"binary image data")
    image = await camera_entity.async_camera_image()
    assert image == b"binary image data"


device_data = {
    "DS-7608NXI-I2": {
        "entity_id": "camera.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_101",
        "codec": "H.264",
        "width": "3840",
        "height": "2160",
        "rtsp_port": 10554,
    },
    "DS-7616NI-Q2": {
        "entity_id": "camera.ds_7616ni_q2_00p0000000000ccrre00000000wcvu_101",
        "codec": "H.265",
        "width": "2560",
        "height": "1440",
        "rtsp_port": 554,
    },
}


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7616NI-Q2"], indirect=True)
async def test_camera_stream_info(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test camera snapshot with alternate url."""

    data = device_data[init_integration.title]
    entity_id = data["entity_id"]
    camera_entity = get_camera_from_entity_id(hass, entity_id)

    assert camera_entity.stream_info.codec == data["codec"]
    assert camera_entity.stream_info.width == data["width"]
    assert camera_entity.stream_info.height == data["height"]

    stream_url = await camera_entity.stream_source()
    assert stream_url == f"rtsp://u1:%2A%2A%2A@1.0.0.255:{data['rtsp_port']}/Streaming/channels/101"


@pytest.mark.parametrize("init_integration", ["DS-2TD1228-2-QA"], indirect=True)
async def test_camera_multichannel(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    entry = init_integration

    device: HikvisionDevice = entry.runtime_data
    assert len(device.cameras) == 2  # video channel + thermal channel
    assert device.cameras[0].input_port == 1
    assert device.cameras[1].input_port == 2


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2", "DS-7732NI-M4"], indirect=True)
async def test_nvr_with_onvif_cameras(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Test proxy cameras with repeated serial no."""

    entry = init_integration
    device: HikvisionDevice = entry.runtime_data

    unique_serial_no = set()
    for camera in device.cameras:
        unique_serial_no.add(camera.serial_no)

    assert len(device.cameras) == len(unique_serial_no)
