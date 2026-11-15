"""
Remote entity with ready-made control pages.

Provides the printer commands as simple commands and ships default UI pages:
a "Control" page (pause/resume, cancel, camera switch, firmware restart,
emergency stop) and one or more "Macros" pages for the selected macros.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

import ucapi
from const import Commands, PrinterConfig, macro_command
from device import MoonrakerDevice
from ucapi import EntityTypes, remote
from ucapi.ui import Size, UiPage, create_ui_text
from ucapi_framework import RemoteEntity, create_entity_id

_LOG = logging.getLogger(__name__)

MACROS_PER_PAGE = 12
"""Two columns of 2x1 buttons on the default 4x6 grid."""


def build_pages(macro_commands: dict[str, str]) -> list[UiPage]:
    """Build the default UI pages for the remote entity."""
    control = UiPage("control", "Control")
    wide = Size(2, 1)
    full = Size(4, 1)
    control.add(create_ui_text("Pause / Resume", 0, 0, wide, Commands.PAUSE_RESUME))
    control.add(create_ui_text("Cancel (2×)", 2, 0, wide, Commands.CANCEL))
    control.add(create_ui_text("◀ Camera", 0, 1, wide, Commands.PREVIOUS_CAMERA))
    control.add(create_ui_text("Camera ▶", 2, 1, wide, Commands.NEXT_CAMERA))
    control.add(create_ui_text("Firmware restart (2×)", 0, 4, full, Commands.FIRMWARE_RESTART))
    control.add(create_ui_text("EMERGENCY STOP (2×)", 0, 5, full, Commands.EMERGENCY_STOP))
    pages = [control]

    macros = list(macro_commands.items())
    for start in range(0, len(macros), MACROS_PER_PAGE):
        number = start // MACROS_PER_PAGE + 1
        suffix = f" {number}" if len(macros) > MACROS_PER_PAGE else ""
        page = UiPage(f"macros_{number}", f"Macros{suffix}")
        for offset, (command, macro) in enumerate(macros[start : start + MACROS_PER_PAGE]):
            x, y = (offset % 2) * 2, offset // 2
            page.add(create_ui_text(macro, x, y, wide, command))
        pages.append(page)
    return pages


class PrinterRemote(RemoteEntity):
    """Remote entity bundling all printer controls."""

    def __init__(self, config: PrinterConfig, device: MoonrakerDevice):
        """Create the remote entity for one printer."""
        self._device = device
        self._macros = {macro_command(m): m for m in config.macros}
        super().__init__(
            create_entity_id(EntityTypes.REMOTE, config.identifier),
            f"{config.name} Controls",
            [remote.Features.SEND_CMD],
            attributes={remote.Attributes.STATE: remote.States.UNKNOWN},
            simple_commands=[c.value for c in Commands] + list(self._macros),
            ui_pages=build_pages(self._macros),
            cmd_handler=self.handle_command,
        )
        self.subscribe_to_device(device)

    async def sync_state(self, force: bool = False) -> None:
        """Remote is ON while Moonraker is connected."""
        if not self._device.is_connected:
            self.set_unavailable()
            return
        self.update({remote.Attributes.STATE: remote.States.ON}, force=force)

    async def handle_command(
        self,
        _entity: RemoteEntity,
        cmd_id: str,
        params: dict[str, Any] | None,
        _: Any | None = None,
    ) -> ucapi.StatusCodes:
        """Handle remote entity commands."""
        params = params or {}
        match cmd_id:
            case remote.Commands.ON | remote.Commands.OFF | remote.Commands.TOGGLE:
                return ucapi.StatusCodes.OK
            case remote.Commands.SEND_CMD:
                commands = [params.get("command", "")]
            case remote.Commands.SEND_CMD_SEQUENCE:
                commands = params.get("sequence", [])
            case _:
                return ucapi.StatusCodes.NOT_IMPLEMENTED

        try:
            for command in commands:
                if command in self._macros:
                    await self._device.run_macro(self._macros[command])
                else:
                    await self._device.execute(command)
        except ValueError as err:
            _LOG.warning("[%s] %s", self._device.log_id, err)
            return ucapi.StatusCodes.BAD_REQUEST
        except ConnectionError:
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE
        return ucapi.StatusCodes.OK
