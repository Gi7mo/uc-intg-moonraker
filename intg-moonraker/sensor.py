"""
Sensor entities.

Status sensors (state, progress, remaining time, file, layer, finish time)
and one temperature sensor per heater selected during setup. Temperatures are
shown as text ``current / target °C`` so heaters fit in a single widget.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass

import printer_state
from const import PrinterConfig, heater_label, slug
from device import MoonrakerDevice
from ucapi import EntityTypes, sensor
from ucapi_framework import SensorEntity, create_entity_id


@dataclass(frozen=True)
class StatusSensor:
    """Definition of a status sensor."""

    key: str
    name: str
    value: Callable[[MoonrakerDevice], str | float]
    unit: str = ""


STATUS_SENSORS = [
    StatusSensor("status", "Status", lambda d: d.status_text),
    StatusSensor("progress", "Progress", lambda d: round(d.progress * 100), "%"),
    StatusSensor("remaining", "Time remaining", lambda d: printer_state.format_duration(d.remaining)),
    StatusSensor("file", "File", lambda d: os.path.basename(d.filename)),
    StatusSensor("layer", "Layer", lambda d: d.layer_text),
    StatusSensor("finish", "Finish time", lambda d: d.finish_time),
]


class PrinterSensor(SensorEntity):
    """A sensor whose value is derived from the printer state."""

    def __init__(
        self,
        config: PrinterConfig,
        device: MoonrakerDevice,
        key: str,
        name: str,
        value: Callable[[MoonrakerDevice], str | float],
        unit: str = "",
    ):
        """Create a sensor reading ``value(device)`` on every update."""
        self._device = device
        self._value = value
        options = {sensor.Options.CUSTOM_UNIT: unit} if unit else None
        super().__init__(
            create_entity_id(EntityTypes.SENSOR, config.identifier, key),
            f"{config.name} {name}",
            [],
            attributes={
                sensor.Attributes.STATE: sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: "",
            },
            device_class=sensor.DeviceClasses.CUSTOM,
            options=options,
        )
        self.subscribe_to_device(device)

    async def sync_state(self, force: bool = False) -> None:
        """Push the current value to the remote."""
        if not self._device.is_connected:
            self.set_unavailable()
            return
        self.update(
            {
                sensor.Attributes.STATE: sensor.States.ON,
                sensor.Attributes.VALUE: self._value(self._device),
            },
            force=force,
        )


def create_sensors(config: PrinterConfig, device: MoonrakerDevice) -> list[PrinterSensor]:
    """Create the status sensors and the selected temperature sensors."""
    sensors = [PrinterSensor(config, device, s.key, s.name, s.value, s.unit) for s in STATUS_SENSORS]
    for heater in config.heaters:
        sensors.append(
            PrinterSensor(
                config,
                device,
                f"temp_{slug(heater)}",
                heater_label(heater),
                lambda d, h=heater: d.temperature_text(h),
            )
        )
    return sensors
