"""Entity wiring against the real framework driver and a fake Moonraker."""

import asyncio

import pytest
import ucapi
from button import create_buttons
from const import Commands, PrinterConfig, macro_command
from device import MoonrakerDevice
from driver import MoonrakerDriver
from fake_moonraker import FakeMoonraker
from media_player import PrinterMediaPlayer
from remote import PrinterRemote, build_pages
from sensor import create_sensors
from test_device import wait_for
from ucapi import button, media_player, remote, sensor

ENTITY_CLASSES = [PrinterMediaPlayer, PrinterRemote, create_sensors, create_buttons]


@pytest.fixture
async def setup():
    fake = FakeMoonraker()
    await fake.start()
    config = PrinterConfig(
        identifier="voron_7125",
        name="Voron",
        host="127.0.0.1",
        port=fake.port,
        cam_interval=1,
        heaters=["extruder", "temperature_sensor chamber"],
        macros=["LOAD_FILAMENT", "MISSING_MACRO"],
    )
    driver = MoonrakerDriver(device_class=MoonrakerDevice, entity_classes=ENTITY_CLASSES)
    device = MoonrakerDevice(config, driver=driver)
    entities = {e.id: e for e in driver.create_entities(config, device)}
    for entity in entities.values():
        driver.api.available_entities.add(entity)
        driver.api.configured_entities.add(entity)
    yield fake, device, entities, driver
    await device.disconnect()
    await fake.stop()


def attrs(driver, entity_id):
    return driver.api.configured_entities.get(entity_id).attributes


def test_entity_ids(setup):
    _, _, entities, driver = setup
    assert sorted(entities) == sorted(
        [
            "media_player.voron_7125",
            "remote.voron_7125",
            "sensor.voron_7125.status",
            "sensor.voron_7125.progress",
            "sensor.voron_7125.remaining",
            "sensor.voron_7125.file",
            "sensor.voron_7125.layer",
            "sensor.voron_7125.finish",
            "sensor.voron_7125.temp_extruder",
            "sensor.voron_7125.temp_temperature_sensor_chamber",
            "button.voron_7125.pause_resume",
            "button.voron_7125.cancel",
            "button.voron_7125.emergency_stop",
            "button.voron_7125.firmware_restart",
            "button.voron_7125.macro_load_filament",
            "button.voron_7125.macro_missing_macro",
        ]
    )
    for entity_id in entities:
        assert driver.device_from_entity_id(entity_id) == "voron_7125"


async def test_entities_follow_device_state(setup):
    fake, device, _, driver = setup
    await device.connect()
    await asyncio.wait_for(fake.subscribed.wait(), 3)
    await wait_for(lambda: device._camera_image is not None)
    await wait_for(
        lambda: attrs(driver, "media_player.voron_7125").get(media_player.Attributes.MEDIA_IMAGE_URL)
        == device._camera_image
    )

    mp = attrs(driver, "media_player.voron_7125")
    assert mp[media_player.Attributes.STATE] == media_player.States.PLAYING
    assert mp[media_player.Attributes.MEDIA_TITLE] == "benchy.gcode"
    assert mp[media_player.Attributes.SOURCE_LIST] == ["Nozzle", "Room"]

    assert attrs(driver, "sensor.voron_7125.status")[sensor.Attributes.VALUE] == "Printing"
    assert attrs(driver, "sensor.voron_7125.progress")[sensor.Attributes.VALUE] == 25
    assert attrs(driver, "sensor.voron_7125.remaining")[sensor.Attributes.VALUE] == "30m"
    assert attrs(driver, "sensor.voron_7125.layer")[sensor.Attributes.VALUE] == "10 / 100"
    assert attrs(driver, "sensor.voron_7125.temp_extruder")[sensor.Attributes.VALUE] == "215 / 215 °C"
    assert (
        attrs(driver, "sensor.voron_7125.temp_temperature_sensor_chamber")[sensor.Attributes.VALUE] == "35 °C"
    )
    assert attrs(driver, "remote.voron_7125")[remote.Attributes.STATE] == remote.States.ON
    assert attrs(driver, "button.voron_7125.cancel")[button.Attributes.STATE] == button.States.AVAILABLE
    assert (
        attrs(driver, "button.voron_7125.macro_missing_macro")[button.Attributes.STATE]
        == button.States.UNAVAILABLE
    )

    await fake.push_status({"extruder": {"temperature": 230.2, "target": 240}})
    await wait_for(
        lambda: attrs(driver, "sensor.voron_7125.temp_extruder")[sensor.Attributes.VALUE] == "230 / 240 °C"
    )


async def test_refresh_resends_unchanged_state(setup, monkeypatch):
    """Regression: an entity removed and added again on the remote needs its full state."""
    fake, device, _, driver = setup
    await device.connect()
    await asyncio.wait_for(fake.subscribed.wait(), 3)
    await wait_for(
        lambda: attrs(driver, "media_player.voron_7125").get(media_player.Attributes.MEDIA_TITLE) == "benchy.gcode"
    )

    sent = {}
    monkeypatch.setattr(driver.api.configured_entities, "update_attributes", lambda eid, a: sent.update({eid: a}))
    for entity_id in ("media_player.voron_7125", "sensor.voron_7125.file", "remote.voron_7125"):
        await driver.refresh_entity_state(entity_id)

    assert sent["media_player.voron_7125"][media_player.Attributes.MEDIA_TITLE] == "benchy.gcode"
    assert sent["media_player.voron_7125"][media_player.Attributes.STATE] == media_player.States.PLAYING
    assert sent["sensor.voron_7125.file"][sensor.Attributes.VALUE] == "benchy.gcode"
    assert sent["remote.voron_7125"][remote.Attributes.STATE] == remote.States.ON


async def test_commands_through_entities(setup):
    fake, device, entities, driver = setup
    await device.connect()
    await asyncio.wait_for(fake.subscribed.wait(), 3)
    await wait_for(lambda: device.print_state == "printing")

    mp = entities["media_player.voron_7125"]
    assert await mp.command(media_player.Commands.STOP, websocket=None) == ucapi.StatusCodes.OK
    await wait_for(
        lambda: "again to confirm" in attrs(driver, "sensor.voron_7125.status")[sensor.Attributes.VALUE]
    )
    assert (
        "again to confirm" in attrs(driver, "media_player.voron_7125")[media_player.Attributes.MEDIA_ARTIST]
    )
    await mp.command(media_player.Commands.STOP, websocket=None)
    await wait_for(lambda: ("printer.print.cancel", {}) in fake.calls)

    rem = entities["remote.voron_7125"]
    for _ in range(2):
        status = await rem.command(
            remote.Commands.SEND_CMD, {"command": macro_command("LOAD_FILAMENT")}, websocket=None
        )
        assert status == ucapi.StatusCodes.OK
    await wait_for(lambda: ("printer.gcode.script", {"script": "LOAD_FILAMENT"}) in fake.calls)
    assert (
        await rem.command(remote.Commands.SEND_CMD, {"command": "NOPE"}, websocket=None)
        == ucapi.StatusCodes.BAD_REQUEST
    )
    await rem.command(
        remote.Commands.SEND_CMD_SEQUENCE, {"sequence": [Commands.EMERGENCY_STOP] * 2}, websocket=None
    )
    await wait_for(lambda: ("printer.emergency_stop", {}) in fake.calls)

    btn = entities["button.voron_7125.pause_resume"]
    assert await btn.command(button.Commands.PUSH, websocket=None) == ucapi.StatusCodes.OK
    await wait_for(lambda: ("printer.print.pause", {}) in fake.calls)


async def test_disconnected_entities(setup):
    _, _, entities, _ = setup
    mp = entities["media_player.voron_7125"]
    btn = entities["button.voron_7125.emergency_stop"]
    assert (
        await mp.command(media_player.Commands.STOP, websocket=None) == ucapi.StatusCodes.SERVICE_UNAVAILABLE
    )
    assert await btn.command(button.Commands.PUSH, websocket=None) == ucapi.StatusCodes.SERVICE_UNAVAILABLE


def test_remote_pages_and_commands():
    macros = {macro_command(f"MACRO_{i}"): f"MACRO_{i}" for i in range(14)}
    pages = build_pages(macros)
    assert [p.page_id for p in pages] == ["control", "macros_1", "macros_2"]
    assert len(pages[1].items) == 12 and len(pages[2].items) == 2
    for page in pages:
        for item in page.items:
            assert 0 <= item.location.x < 4 and 0 <= item.location.y < 6
            assert len(item.command.cmd_id) <= 20


def test_remote_simple_commands_are_valid(setup):
    _, _, entities, _ = setup
    commands = entities["remote.voron_7125"].options["simple_commands"]
    assert "EMERGENCY_STOP" in commands and "M_LOAD_FILAMENT" in commands
    assert all(len(c) <= 20 and " " not in c for c in commands)
