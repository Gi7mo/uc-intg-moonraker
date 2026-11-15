"""Integration tests of MoonrakerDevice against a fake Moonraker server."""

import asyncio

import device as device_module
import pytest
from const import CONFIRM_MIN_GAP, Commands, PrinterConfig
from device import MoonrakerDevice
from fake_moonraker import FakeMoonraker
from ucapi import media_player


async def wait_for(predicate, timeout=3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


@pytest.fixture
async def server():
    fake = FakeMoonraker()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def device(server):
    config = PrinterConfig(
        identifier="test_printer",
        name="Voron",
        host="127.0.0.1",
        port=server.port,
        cam_interval=1,
        heaters=["extruder", "heater_bed", "temperature_sensor chamber", "heater_generic gone"],
        macros=["LOAD_FILAMENT", "MISSING_MACRO"],
    )
    dev = MoonrakerDevice(config)
    await dev.connect()
    await asyncio.wait_for(server.subscribed.wait(), 3)
    await wait_for(lambda: dev.print_state == "printing")
    yield dev
    await dev.disconnect()


async def test_connect_identifies_and_subscribes(server, device):
    methods = [m for m, _ in server.calls]
    assert methods[:2] == ["server.connection.identify", "server.info"]
    identify = server.calls[0][1]
    assert identify["client_name"] == "uc-intg-moonraker"
    subscribe = dict(server.calls)["printer.objects.subscribe"]["objects"]
    assert {"extruder", "heater_bed", "temperature_sensor chamber"} <= subscribe.keys()
    assert "heater_generic gone" not in subscribe
    assert device.klippy_state == "ready"


async def test_derived_state(device):
    assert device.progress == pytest.approx(0.25)  # (1100-100)/(4100-100)
    assert device.remaining == pytest.approx(1800)
    assert device.layer_text == "10 / 100"
    assert device.temperature_text("extruder") == "215 / 215 °C"
    assert device.temperature_text("temperature_sensor chamber") == "35 °C"
    assert device.summary == "Printing · 25% · 30m left"
    assert device.has_macro("load_filament")
    assert not device.has_macro("MISSING_MACRO")
    assert [w.name for w in device.webcams] == ["Nozzle", "Room"]


async def test_media_player_attributes_and_camera(server, device):
    await wait_for(lambda: server.snapshot_requests > 0 and device._camera_image)
    attrs = device.get_media_player_attributes()
    assert attrs.STATE == media_player.States.PLAYING
    assert attrs.MEDIA_TITLE == "benchy.gcode"
    assert attrs.MEDIA_ARTIST == "Printing · 25% · 30m left"
    assert attrs.MEDIA_ALBUM == "10 / 100"
    assert attrs.MEDIA_IMAGE_URL.startswith("data:image/jpeg;base64,")
    assert attrs.MEDIA_IMAGE_URL == device._camera_image
    # elapsed includes heating and pauses, like the web UIs show it
    assert attrs.MEDIA_POSITION == 660
    assert attrs.MEDIA_DURATION == 660 + 1800
    assert attrs.SOURCE == "Nozzle"
    assert attrs.SOURCE_LIST == ["Nozzle", "Room"]


async def test_thumbnail_loaded_with_largest_fitting_size(server, device):
    await wait_for(lambda: device._thumbnail is not None)
    assert server.thumbnail_requests == ["parts/.thumbs/benchy-300x300.png"]


async def test_status_updates_and_idle_artwork(server, device):
    await wait_for(lambda: device._camera_image is not None)
    await server.push_status(
        {
            "print_stats": {"state": "complete", "print_duration": 2350.0, "total_duration": 2350.0},
            "extruder": {"temperature": 120.0, "target": 0},
        }
    )
    await wait_for(lambda: device.print_state == "complete")
    assert device.temperature_text("extruder") == "120 / 0 °C"
    attrs = device.get_media_player_attributes()
    assert attrs.STATE == media_player.States.ON
    assert attrs.MEDIA_ARTIST == "Complete"
    assert attrs.MEDIA_POSITION == attrs.MEDIA_DURATION == 2350
    # camera stops when not printing; artwork falls back to the thumbnail
    await wait_for(lambda: device._camera_task is None)
    await wait_for(lambda: device._thumbnail is not None)
    assert device.artwork == device._thumbnail
    requests = server.snapshot_requests
    await asyncio.sleep(1.2)
    assert server.snapshot_requests == requests


async def test_new_file_triggers_metadata_refresh(server, device):
    await server.push_status({"print_stats": {"filename": "other.gcode", "state": "standby"}})
    await wait_for(lambda: device._metadata_file == "other.gcode")
    assert device._metadata is None
    assert device.artwork.startswith("data:image/jpeg;base64,")  # placeholder


async def test_pause_resume_single_press(server, device):
    await device.execute(Commands.PAUSE_RESUME)
    await wait_for(lambda: ("printer.print.pause", {}) in server.calls)
    await server.push_status({"print_stats": {"state": "paused"}})
    await wait_for(lambda: device.print_state == "paused")
    assert device.get_media_player_attributes().STATE == media_player.States.PAUSED
    await device.execute(Commands.PAUSE_RESUME)
    await wait_for(lambda: ("printer.print.resume", {}) in server.calls)


async def test_guarded_commands_need_double_press(server, device):
    for command, method in [
        (Commands.EMERGENCY_STOP, "printer.emergency_stop"),
        (Commands.CANCEL, "printer.print.cancel"),
        (Commands.FIRMWARE_RESTART, "printer.firmware_restart"),
    ]:
        await device.execute(command)
        await asyncio.sleep(0.1)
        assert all(m != method for m, _ in server.calls)
        assert "again to confirm" in device.status_text
        assert "again to confirm" in device.get_media_player_attributes().MEDIA_ARTIST
        await asyncio.sleep(CONFIRM_MIN_GAP)
        await device.execute(command)
        await wait_for(lambda m=method: any(c == m for c, _ in server.calls))
        assert device.confirm.hint == ""


async def test_macro_double_press_uses_klipper_case(server, device):
    assert await device.run_macro("load_filament") is False
    await asyncio.sleep(CONFIRM_MIN_GAP)
    assert await device.run_macro("load_filament") is True
    await wait_for(lambda: ("printer.gcode.script", {"script": "LOAD_FILAMENT"}) in server.calls)


async def test_camera_switching(device):
    await device.next_camera()
    assert device.current_webcam.name == "Room"
    await device.next_camera()
    assert device.current_webcam.name == "Nozzle"
    await device.previous_camera()
    assert device.current_webcam.name == "Room"
    await device.select_camera("Nozzle")
    assert device.get_media_player_attributes().SOURCE == "Nozzle"


async def test_klippy_shutdown_and_ready(server, device):
    await server.notify("notify_klippy_shutdown")
    await wait_for(lambda: device.klippy_state == "shutdown")
    assert device.get_media_player_attributes().STATE == media_player.States.OFF
    assert device.status_text == "Klipper shutdown"
    server.subscribed.clear()
    await server.notify("notify_klippy_ready")
    await asyncio.wait_for(server.subscribed.wait(), 3)
    await wait_for(lambda: device.klippy_state == "ready")


async def test_reconnects_after_connection_loss(server, device):
    assert server.connections == 1
    server.subscribed.clear()
    await server.drop_connections()
    await wait_for(lambda: server.connections == 2, timeout=6)
    await asyncio.wait_for(server.subscribed.wait(), 3)
    await wait_for(lambda: device.is_connected and device.klippy_state == "ready")


async def test_commands_fail_when_disconnected(server):
    dev = MoonrakerDevice(PrinterConfig("x", "X", "127.0.0.1", server.port))
    with pytest.raises(ConnectionError):
        await dev.execute(Commands.EMERGENCY_STOP)


async def test_api_key_is_sent():
    fake = FakeMoonraker(api_key="secret")
    await fake.start()
    try:
        with pytest.raises(PermissionError):
            await MoonrakerDevice.probe("127.0.0.1", fake.port)
        info = await MoonrakerDevice.probe("127.0.0.1", fake.port, api_key="secret")
        assert info["hostname"] == "voron"
        assert info["heaters"] == [
            "extruder",
            "heater_bed",
            "temperature_sensor chamber",
        ]
        assert info["macros"] == ["LOAD_FILAMENT", "_HELPER"]

        dev = MoonrakerDevice(PrinterConfig("x", "X", "127.0.0.1", fake.port, api_key="secret"))
        await dev.connect()
        await asyncio.wait_for(fake.subscribed.wait(), 3)
        assert fake.calls[0][1]["api_key"] == "secret"
        await wait_for(lambda: fake.thumbnail_requests and fake.snapshot_requests)
        # snapshots can live on other hosts and must not receive the key
        assert set(fake.snapshot_api_keys) == {None}
        await dev.disconnect()
    finally:
        await fake.stop()


async def test_probe_unreachable():
    with pytest.raises(ConnectionError):
        await MoonrakerDevice.probe("127.0.0.1", 1)


async def test_layer_after_completion(server, device):
    await server.push_status({"print_stats": {"state": "complete", "info": {"current_layer": 0, "total_layer": 100}}})
    await wait_for(lambda: device.print_state == "complete")
    assert device.layer_text == "100 / 100"
    await server.push_status({"print_stats": {"state": "standby"}})
    await wait_for(lambda: device.print_state == "standby")
    assert device.layer_text == ""


async def test_metadata_retried_after_failure(server, device, monkeypatch):
    monkeypatch.setattr(device_module, "METADATA_RETRY", 0)
    server.metadata_fails = 1
    await server.push_status({"print_stats": {"filename": "other.gcode"}})
    await wait_for(lambda: device._metadata_file == "other.gcode" and device._metadata_failed_at)
    await server.push_status({"print_stats": {"filename": "parts/benchy.gcode"}})
    await wait_for(lambda: device._metadata is not None)
    server.metadata_fails = 1
    device._metadata_file = None
    await server.push_status({"print_stats": {"print_duration": 601.0}})
    await wait_for(lambda: device._metadata_failed_at is not None)
    assert device._metadata is None
    await server.push_status({"print_stats": {"print_duration": 602.0}})
    await wait_for(lambda: device._metadata is not None and device._metadata_failed_at is None)


async def test_disconnect_pushes_state(device):
    pushed = []
    device.push_update = lambda: pushed.append(device.klippy_state)
    await device.close_websocket()
    assert pushed and pushed[-1] is None


async def test_unknown_macro_is_not_sent(server, device):
    for _ in range(2):
        assert await device.run_macro("MISSING_MACRO") is False
        await asyncio.sleep(CONFIRM_MIN_GAP)
    assert all(m != "printer.gcode.script" for m, _ in server.calls)
