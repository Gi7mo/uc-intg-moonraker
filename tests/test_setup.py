"""Setup flow: manual entry, option screen and update of an existing printer."""

import pytest
from const import PrinterConfig
from device import MoonrakerDevice
from fake_moonraker import FakeMoonraker
from setup import MoonrakerSetupFlow
from ucapi import IntegrationSetupError, RequestUserInput, SetupComplete, SetupError, UserDataResponse
from ucapi_framework import BaseConfigManager, SetupSteps


@pytest.fixture
async def fake():
    server = FakeMoonraker()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
def flow(tmp_path):
    config = BaseConfigManager(str(tmp_path), None, None, config_class=PrinterConfig)
    return MoonrakerSetupFlow(config, driver=None, device_class=MoonrakerDevice)


def field_ids(screen: RequestUserInput) -> dict[str, dict]:
    return {f["id"]: f for f in screen.settings}


async def submit(flow, values):
    return await flow._handle_user_data_response(UserDataResponse(input_values=values))


async def test_add_printer(flow, fake):
    flow._add_mode = True
    flow._setup_step = SetupSteps.MANUAL_ENTRY
    screen = await submit(
        flow, {"name": "", "host": "127.0.0.1", "port": str(fake.port), "api_key": "", "ssl": "false"}
    )
    assert isinstance(screen, RequestUserInput)
    fields = field_ids(screen)
    assert fields["cam_interval"]["field"]["number"]["value"] == 5
    labels = {
        f["label"]["en"]: f["field"]["checkbox"]["value"] for f in screen.settings if "checkbox" in f["field"]
    }
    assert labels == {
        "Extruder (extruder)": True,
        "Bed (heater_bed)": True,
        "Chamber (temperature_sensor chamber)": True,
        "LOAD_FILAMENT": False,
    }

    result = await submit(
        flow,
        {
            "cam_interval": "200",
            "heater_0": "true",
            "heater_1": "false",
            "heater_2": "true",
            "macro_0": "true",
        },
    )
    assert isinstance(result, SetupComplete)
    saved = flow.config.get(f"127_0_0_1_{fake.port}")
    assert saved.name == "voron"
    assert saved.port == fake.port
    assert saved.cam_interval == 60
    assert saved.heaters == ["extruder", "temperature_sensor chamber"]
    assert saved.macros == ["LOAD_FILAMENT"]


async def test_update_keeps_identifier_and_prefills(flow, fake):
    existing = PrinterConfig(
        "my_printer",
        "Voron",
        "127.0.0.1",
        fake.port,
        cam_interval=3,
        heaters=["heater_bed"],
        macros=["_HELPER"],
    )
    flow.config.add_or_update(existing)
    flow._selected_config_id = "my_printer"
    assert await flow.discover_devices() == []

    form = field_ids(flow.get_manual_entry_form())
    assert form["name"]["field"]["text"]["value"] == "Voron"
    assert form["port"]["field"]["number"]["value"] == fake.port

    flow._setup_step = SetupSteps.MANUAL_ENTRY
    screen = await submit(
        flow, {"name": "Voron 2.4", "host": "127.0.0.1", "port": fake.port, "api_key": "", "ssl": False}
    )
    checks = {
        f["label"]["en"]: f["field"]["checkbox"]["value"] for f in screen.settings if "checkbox" in f["field"]
    }
    assert checks["Bed (heater_bed)"] is True
    assert checks["Extruder (extruder)"] is False
    assert checks["_HELPER"] is True
    assert checks["LOAD_FILAMENT"] is False
    assert field_ids(screen)["cam_interval"]["field"]["number"]["value"] == 3

    await submit(flow, {"cam_interval": 3, "heater_1": True, "macro_0": True, "macro_1": True})
    saved = flow.config.get("my_printer")
    assert saved.name == "Voron 2.4"
    assert saved.heaters == ["heater_bed"]
    assert saved.macros == ["LOAD_FILAMENT", "_HELPER"]
    assert len(list(flow.config.all())) == 1


async def test_klippy_not_ready_shows_hint(flow, fake):
    fake.klippy_state = "shutdown"
    flow._add_mode = True
    flow._setup_step = SetupSteps.MANUAL_ENTRY
    screen = await submit(flow, {"host": "127.0.0.1", "port": fake.port})
    assert "klippy_info" in field_ids(screen)


async def test_errors(flow, fake):
    flow._setup_step = SetupSteps.MANUAL_ENTRY
    result = await submit(flow, {"host": "127.0.0.1", "port": 1})
    assert isinstance(result, SetupError)
    assert result.error_type == IntegrationSetupError.CONNECTION_REFUSED

    fake.api_key = "secret"
    result = await submit(flow, {"host": "127.0.0.1", "port": fake.port, "api_key": "wrong"})
    assert result.error_type == IntegrationSetupError.AUTHORIZATION_ERROR

    assert isinstance(await submit(flow, {"host": " "}), RequestUserInput)


async def test_discovery_input_mapping(flow):
    from ucapi_framework import DiscoveredDevice

    found = DiscoveredDevice("x", "voron", "10.0.0.5", extra_data={"port": 7126})
    values = await flow.prepare_input_from_discovery(found, {"choice": "x", "api_key": "k"})
    assert values == {"name": "voron", "host": "10.0.0.5", "port": 7126, "api_key": "k", "ssl": False}
