"""
Print view media player entity.

Shows the printer as a media player: artwork is the camera snapshot (or the
gcode thumbnail when idle), title the file, artist state and ETA, the
progress bar the print progress. Play/pause pauses and resumes, stop cancels
(double press), next/previous and source switch between webcams.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

import camera
import ucapi
from const import PrinterConfig
from device import MoonrakerDevice
from ucapi import EntityTypes, media_player
from ucapi.media_player import Attributes, DeviceClasses
from ucapi_framework import MediaPlayerEntity, create_entity_id

_LOG = logging.getLogger(__name__)

FEATURES = [
    # ON_OFF has no printer function, but the remote's media widget needs it to render.
    media_player.Features.ON_OFF,
    media_player.Features.PLAY_PAUSE,
    media_player.Features.STOP,
    media_player.Features.NEXT,
    media_player.Features.PREVIOUS,
    media_player.Features.SELECT_SOURCE,
    media_player.Features.MEDIA_TITLE,
    media_player.Features.MEDIA_ARTIST,
    media_player.Features.MEDIA_ALBUM,
    media_player.Features.MEDIA_IMAGE_URL,
    media_player.Features.MEDIA_POSITION,
    media_player.Features.MEDIA_DURATION,
]


class PrinterMediaPlayer(MediaPlayerEntity):
    """Media player entity showing camera and print progress."""

    def __init__(self, config: PrinterConfig, device: MoonrakerDevice):
        """Create the print view entity for one printer."""
        self._device = device
        entity_id = create_entity_id(EntityTypes.MEDIA_PLAYER, config.identifier)
        # The remote takes the supported attributes from the entity definition;
        # attributes not declared here are not shown when updated later.
        super().__init__(
            entity_id,
            config.name,
            FEATURES,
            attributes={
                Attributes.STATE: media_player.States.UNKNOWN,
                Attributes.MEDIA_TYPE: media_player.MediaContentType.VIDEO,
                Attributes.MEDIA_TITLE: config.name,
                Attributes.MEDIA_ARTIST: "",
                Attributes.MEDIA_ALBUM: "",
                Attributes.MEDIA_IMAGE_URL: camera.placeholder_artwork(),
                Attributes.MEDIA_POSITION: 0,
                Attributes.MEDIA_DURATION: 0,
                Attributes.SOURCE: "",
                Attributes.SOURCE_LIST: [],
            },
            device_class=DeviceClasses.STREAMING_BOX,
            cmd_handler=self.handle_command,
        )
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Push the current print view to the remote."""
        if not self._device.is_connected:
            self.set_unavailable()
            return
        self.update(self._device.get_media_player_attributes())

    async def handle_command(
        self,
        _entity: MediaPlayerEntity,
        cmd_id: str,
        params: dict[str, Any] | None,
        _: Any | None = None,
    ) -> ucapi.StatusCodes:
        """Handle media player commands from the remote."""
        _LOG.info("[%s] Command %s %s", self._device.log_id, cmd_id, params or "")
        try:
            match cmd_id:
                case media_player.Commands.ON | media_player.Commands.OFF | media_player.Commands.TOGGLE:
                    pass
                case media_player.Commands.PLAY_PAUSE:
                    await self._device.pause_resume()
                case media_player.Commands.STOP:
                    await self._device.cancel()
                case media_player.Commands.NEXT:
                    await self._device.next_camera()
                case media_player.Commands.PREVIOUS:
                    await self._device.previous_camera()
                case media_player.Commands.SELECT_SOURCE:
                    await self._device.select_camera((params or {}).get("source", ""))
                case _:
                    return ucapi.StatusCodes.NOT_IMPLEMENTED
        except ConnectionError:
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE
        return ucapi.StatusCodes.OK
