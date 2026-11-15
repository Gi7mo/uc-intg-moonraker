"""
Constants for the Moonraker integration.

Configuration dataclass and command identifiers shared by the device,
the entities and the setup flow.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum

DEFAULT_PORT = 7125
DEFAULT_CAM_INTERVAL = 5
CONFIRM_WINDOW = 3.0
"""Seconds within which a second press confirms a guarded action."""
CONFIRM_MIN_GAP = 0.4
"""Repeats faster than this (key repeat, command sequences) do not confirm."""

_HEATER_RE = re.compile(r"^(extruder\d*|heater_bed|(heater_generic|temperature_sensor|temperature_fan) \S+)$")
"""Klipper object names that carry a temperature (not ``extruder_stepper``)."""

MACRO_PREFIX = "gcode_macro "
MACRO_NAME = re.compile(r"[A-Za-z0-9_]+")
"""Macro names that are safe to send as a gcode script."""


@dataclass
class PrinterConfig:
    """Configuration of one Moonraker printer."""

    identifier: str
    """Unique identifier, derived from host and port. Must not contain dots."""

    name: str
    """Friendly name of the printer."""

    host: str
    """IP address or hostname of the Moonraker instance."""

    port: int = DEFAULT_PORT
    """Moonraker port."""

    api_key: str = ""
    """Optional Moonraker API key. Empty for trusted clients."""

    ssl: bool = False
    """Use https/wss instead of http/ws."""

    cam_interval: int = DEFAULT_CAM_INTERVAL
    """Camera snapshot refresh interval in seconds."""

    heaters: list[str] = field(default_factory=list)
    """Klipper objects shown as temperature sensors, e.g. ``extruder``."""

    macros: list[str] = field(default_factory=list)
    """Klipper gcode macros exposed as buttons, e.g. ``LOAD_FILAMENT``."""

    def __setattr__(self, name: str, value) -> None:
        """
        Normalize field types on every assignment, including ``__init__``.

        The remote sends every setup value as a string and re-sends earlier
        fields on later setup pages, which the framework copies onto the config
        unconverted (``"false"`` would be truthy, ``"7125"`` a string).
        """
        if name == "port":
            value = _to_int(value, DEFAULT_PORT)
        elif name == "cam_interval":
            value = _to_int(value, DEFAULT_CAM_INTERVAL)
        elif name == "ssl":
            value = to_bool(value)
        elif name == "api_key":
            value = str(value or "").strip()
        super().__setattr__(name, value)


def to_bool(value) -> bool:
    """Interpret setup values: bools or the strings "true"/"false"."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _to_int(value, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


class Commands(StrEnum):
    """Simple commands of the remote entity and the button entities."""

    PAUSE_RESUME = "PAUSE_RESUME"
    CANCEL = "CANCEL"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    FIRMWARE_RESTART = "FIRMWARE_RESTART"
    NEXT_CAMERA = "NEXT_CAMERA"
    PREVIOUS_CAMERA = "PREVIOUS_CAMERA"


MAX_COMMAND_LENGTH = 20
"""Remote entity simple command names: max 20 characters, no whitespace."""


def macro_command(macro: str) -> str:
    """Return a valid remote simple command name for a macro."""
    name = "M_" + re.sub(r"\s", "_", macro.upper())
    if len(name) <= MAX_COMMAND_LENGTH:
        return name
    digest = hashlib.sha1(macro.encode()).hexdigest()[:4].upper()
    return f"{name[: MAX_COMMAND_LENGTH - 5]}_{digest}"


def make_identifier(host: str, port: int) -> str:
    """Build an entity-safe device identifier from host and port."""
    return slug(f"{host}_{port}")


def slug(value: str) -> str:
    """Return ``value`` reduced to characters that are safe in entity ids."""
    return re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_").lower()


def heater_label(obj_name: str) -> str:
    """Return a human readable label for a Klipper heater/sensor object."""
    base = obj_name.split(" ", 1)[-1]
    if obj_name == "heater_bed":
        return "Bed"
    if obj_name.startswith("extruder"):
        suffix = obj_name[len("extruder") :]
        return f"Extruder {suffix}" if suffix else "Extruder"
    return base.replace("_", " ").title()


def is_heater(obj_name: str) -> bool:
    """Return True if the Klipper object reports a temperature."""
    return _HEATER_RE.match(obj_name) is not None


def has_target(obj_name: str) -> bool:
    """Return True if the Klipper object has a target temperature."""
    return not obj_name.startswith("temperature_sensor ")
