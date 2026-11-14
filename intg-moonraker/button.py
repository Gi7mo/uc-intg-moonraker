"""
Button entities.

Printer controls (pause/resume, cancel, emergency stop, firmware restart)
and one button per macro selected during setup. All but pause/resume need a
second press within the confirm window.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

import ucapi
from const import Commands, PrinterConfig, slug
from device import MoonrakerDevice
from ucapi import EntityTypes, button
from ucapi_framework import ButtonEntity, create_entity_id

_LOG = logging.getLogger(__name__)

CONTROL_BUTTONS = [
    (Commands.PAUSE_RESUME, "pause_resume", "Pause / Resume"),
    (Commands.CANCEL, "cancel", "Cancel print"),
    (Commands.EMERGENCY_STOP, "emergency_stop", "Emergency stop"),
    (Commands.FIRMWARE_RESTART, "firmware_restart", "Firmware restart"),
]


class PrinterButton(ButtonEntity):
    """A button that sends one printer command."""

    def __init__(
        self,
        config: PrinterConfig,
        device: MoonrakerDevice,
        key: str,
        name: str,
        command: str | None = None,
        macro: str | None = None,
    ):
        """Create a button sending ``command`` or running ``macro``."""
        self._device = device
        self._command = command
        self._macro = macro
        super().__init__(
            create_entity_id(EntityTypes.BUTTON, config.identifier, key),
            f"{config.name} {name}",
            cmd_handler=self.handle_command,
        )
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Mark the button unavailable while disconnected or its macro is missing."""
        available = self._device.is_connected and (
            self._macro is None
            or self._device.klippy_state != "ready"
            or self._device.has_macro(self._macro)
        )
        self.update(
            {
                button.Attributes.STATE: (
                    button.States.AVAILABLE if available else button.States.UNAVAILABLE
                )
            }
        )

    async def handle_command(
        self,
        _entity: ButtonEntity,
        cmd_id: str,
        _params: dict[str, Any] | None,
        _: Any | None = None,
    ) -> ucapi.StatusCodes:
        """Handle a button press."""
        if cmd_id != button.Commands.PUSH:
            return ucapi.StatusCodes.NOT_IMPLEMENTED
        try:
            if self._macro is not None:
                await self._device.run_macro(self._macro)
            else:
                await self._device.execute(self._command)
        except ConnectionError:
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE
        return ucapi.StatusCodes.OK


def create_buttons(config: PrinterConfig, device: MoonrakerDevice) -> list[PrinterButton]:
    """Create the control buttons and one button per selected macro."""
    buttons = [
        PrinterButton(config, device, key, name, command)
        for command, key, name in CONTROL_BUTTONS
    ]
    for macro in config.macros:
        buttons.append(
            PrinterButton(config, device, f"macro_{slug(macro)}", macro, macro=macro)
        )
    return buttons
