"""Tests for the pure printer state helpers and constants."""

import printer_state as ps
import pytest
from const import has_target, heater_label, is_heater, macro_command, make_identifier, slug
from ucapi import media_player


def test_progress_uses_gcode_byte_range():
    status = {"virtual_sdcard": {"progress": 0.9, "file_position": 600}}
    metadata = {"gcode_start_byte": 100, "gcode_end_byte": 1100}
    assert ps.compute_progress(status, metadata) == pytest.approx(0.5)


def test_progress_prefers_slicer_m73():
    status = {"display_status": {"progress": 0.21}, "virtual_sdcard": {"file_position": 141149}}
    metadata = {"gcode_start_byte": 15681, "gcode_end_byte": 5638863}
    assert ps.compute_progress(status, metadata) == pytest.approx(0.21)
    status["display_status"]["progress"] = 0
    assert ps.compute_progress(status, metadata) == pytest.approx(0.0223, abs=1e-3)


def test_progress_without_m73_uses_byte_range():
    # Klipper reports the file progress as display_status.progress when there is no M73
    status = {"display_status": {"progress": 0.05}, "virtual_sdcard": {"progress": 0.05, "file_position": 600}}
    metadata = {"gcode_start_byte": 100, "gcode_end_byte": 1100}
    assert ps.compute_progress(status, metadata) == pytest.approx(0.5)


def test_progress_falls_back_to_sdcard_and_clamps():
    assert ps.compute_progress({"virtual_sdcard": {"progress": 0.3}}, None) == 0.3
    assert ps.compute_progress({"virtual_sdcard": {"progress": 1.7}}, None) == 1.0
    assert ps.compute_progress({}, None) == 0.0
    status = {"virtual_sdcard": {"file_position": 50}}
    assert ps.compute_progress(status, {"gcode_start_byte": 100, "gcode_end_byte": 200}) == 0.0


def test_remaining_slicer_only_at_start():
    assert ps.compute_remaining(0, 0.0, 3600) == 3600


def test_remaining_blends_slicer_and_progress():
    # 25% after 600s: file based says 1800s left, slicer says 2400-600=1800 too
    assert ps.compute_remaining(600, 0.25, 2400) == pytest.approx(1800)
    # slicer too optimistic: progress weight grows with progress
    blended = ps.compute_remaining(1000, 0.5, 1500)
    assert blended == pytest.approx(500 * 0.5 + 1000 * 0.5)


def test_interpolate_progress_between_slicer_steps():
    # 1 % of a 10000 s print is 100 s: after 50 s we are half way to the next step
    assert ps.interpolate_progress(0.31, 50, 10000) == pytest.approx(0.315)
    # never reaches the next step on its own
    assert ps.interpolate_progress(0.31, 500, 10000) < 0.32
    assert ps.interpolate_progress(0.31, 50, None) == 0.31
    assert ps.interpolate_progress(0.31, 0, 10000) == 0.31
    # the remaining time now counts down between steps
    left = [ps.compute_remaining(4000 + t, ps.interpolate_progress(0.31, t, 10000), 10000) for t in (0, 30, 60)]
    assert left[0] > left[1] > left[2]


def test_remaining_without_slicer_estimate():
    assert ps.compute_remaining(0, 0.0, None) is None
    assert ps.compute_remaining(300, 0.5, None) == pytest.approx(300)


@pytest.mark.parametrize(
    ("klippy", "state", "expected"),
    [
        ("ready", "printing", media_player.States.PLAYING),
        ("ready", "paused", media_player.States.PAUSED),
        ("ready", "standby", media_player.States.ON),
        ("ready", "complete", media_player.States.ON),
        ("shutdown", "printing", media_player.States.OFF),
        (None, None, media_player.States.OFF),
    ],
)
def test_media_state(klippy, state, expected):
    assert ps.media_state(klippy, state) == expected


def test_state_label():
    assert ps.state_label("ready", "printing") == "Printing"
    assert ps.state_label("ready", None) == "Ready"
    assert ps.state_label("shutdown", "printing") == "Klipper shutdown"
    assert ps.state_label(None, None) == "Connecting"


def test_format_duration():
    assert ps.format_duration(None) == ""
    assert ps.format_duration(42) == "42s"
    assert ps.format_duration(125) == "2m"
    assert ps.format_duration(3900) == "1h 05m"


def test_format_temperature():
    assert ps.format_temperature("extruder", {"temperature": 214.6, "target": 215}) == "215 / 215 °C"
    assert ps.format_temperature("heater_bed", {"temperature": 22.1, "target": 0}) == "22 / 0 °C"
    assert ps.format_temperature("temperature_sensor chamber", {"temperature": 35.4}) == "35 °C"
    assert ps.format_temperature("extruder", None) == "–"


def test_format_layer():
    status = {"print_stats": {"info": {"current_layer": 5, "total_layer": None}}}
    assert ps.format_layer(status, {"layer_count": 80}) == "5 / 80"
    assert ps.format_layer({"print_stats": {}}, None) == ""


def test_pick_thumbnail():
    meta = {
        "thumbnails": [
            {"width": 32, "relative_path": "a.png"},
            {"width": 300, "relative_path": "b.png"},
            {"width": 600, "relative_path": "c.png"},
        ]
    }
    assert ps.pick_thumbnail(meta) == "b.png"
    assert ps.pick_thumbnail({"thumbnails": [{"width": 800, "relative_path": "x.png"}]}) == "x.png"
    assert ps.pick_thumbnail(None) is None


def test_heater_detection():
    assert is_heater("extruder")
    assert is_heater("extruder1")
    assert is_heater("heater_bed")
    assert is_heater("temperature_sensor chamber")
    assert is_heater("temperature_fan pi")
    assert is_heater("heater_generic chamber")
    assert not is_heater("extruder_stepper belt")
    assert not is_heater("gcode_macro PRINT_START")
    assert not has_target("temperature_sensor chamber")
    assert has_target("temperature_fan pi")
    assert heater_label("heater_bed") == "Bed"
    assert heater_label("extruder1") == "Extruder 1"
    assert heater_label("temperature_sensor raspberry_pi") == "Raspberry Pi"


def test_identifiers_are_entity_safe():
    assert make_identifier("192.168.1.5", 7125) == "192_168_1_5_7125"
    assert "." not in slug("temperature_sensor my.chamber")


def test_macro_command_limits():
    assert macro_command("load_filament") == "M_LOAD_FILAMENT"
    long_a = macro_command("PRINT_START_WITH_CHAMBER_HEATSOAK")
    long_b = macro_command("PRINT_START_WITH_CHAMBER_HEATSOAK_2")
    assert len(long_a) <= 20 and len(long_b) <= 20
    assert long_a != long_b
    assert " " not in macro_command("MY MACRO")


def test_stable_round_suppresses_flapping():
    shown = None
    for reading in (31.4, 31.6, 31.45, 31.7, 31.5):
        shown = ps.stable_round(reading, shown)
        assert shown == 31
    assert ps.stable_round(31.8, 31) == 32
    assert ps.stable_round(214.2, 31) == 214
    assert ps.format_temperature("extruder", {"temperature": 31.6, "target": 0}, current=31) == "31 / 0 °C"
