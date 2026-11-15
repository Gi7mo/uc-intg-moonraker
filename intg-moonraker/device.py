"""
Moonraker device.

Maintains the JSON-RPC websocket connection to one Moonraker instance,
subscribes to Klipper status objects, keeps the merged printer state and
derives everything the entities display. Camera snapshots are refreshed in a
background task while a print is active.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import itertools
import json
import logging
import os
import time
from asyncio import AbstractEventLoop
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import aiohttp
import camera
import printer_state
from camera import Webcam
from confirm import ConfirmGuard
from const import MACRO_NAME, MACRO_PREFIX, Commands, PrinterConfig, is_heater
from ucapi import EntityTypes
from ucapi_framework import BaseConfigManager, WebSocketDevice, create_entity_id
from ucapi_framework.helpers import MediaPlayerAttributes

_LOG = logging.getLogger(__name__)

CLIENT_NAME = "uc-intg-moonraker"
RPC_TIMEOUT = 10
COMMAND_TIMEOUT = 120
PUSH_DELAY = 0.5
"""Coalesce bursts of status updates (Klipper reports ~4x per second)."""
METADATA_RETRY = 30
"""Seconds before loading file metadata again after a failure."""


class MoonrakerError(Exception):
    """Error returned by a Moonraker JSON-RPC call."""


class MoonrakerDevice(WebSocketDevice):
    """One Klipper printer reached through Moonraker."""

    def __init__(
        self,
        device_config: PrinterConfig,
        loop: AbstractEventLoop | None = None,
        config_manager: BaseConfigManager | None = None,
        driver=None,
    ) -> None:
        """Create the device; the connection is opened by ``connect()``."""
        # aiohttp's heartbeat keeps the socket alive, no protocol-level ping needed.
        super().__init__(
            device_config,
            loop,
            ping_interval=0,
            config_manager=config_manager,
            driver=driver,
        )
        self._device_config: PrinterConfig = device_config
        self._session: aiohttp.ClientSession | None = None
        self._rpc_ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._init_task: asyncio.Task | None = None
        self._camera_task: asyncio.Task | None = None
        self._push_handle: asyncio.TimerHandle | None = None

        self._klippy_state: str | None = None
        self._status: dict[str, dict[str, Any]] = {}
        self._available_macros: dict[str, str] = {}
        self._metadata: dict[str, Any] | None = None
        self._metadata_file: str | None = None
        self._metadata_failed_at: float | None = None
        self._thumbnail: str | None = None
        self._webcams: list[Webcam] = []
        self._cam_index = 0
        self._camera_image: str | None = None
        self._shown_temps: dict[str, int] = {}
        self._position_stamp: tuple[int, str] = (-1, "")
        self._progress_step: tuple[float, float] = (-1.0, 0.0)

        self.confirm = ConfirmGuard(self.push_update)

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def identifier(self) -> str:
        """Return the device identifier."""
        return self._device_config.identifier

    @property
    def name(self) -> str:
        """Return the printer name."""
        return self._device_config.name

    @property
    def address(self) -> str | None:
        """Return the Moonraker host."""
        return self._device_config.host

    @property
    def log_id(self) -> str:
        """Return a log identifier."""
        return self.name or self.identifier

    @property
    def config(self) -> PrinterConfig:
        """Return the printer configuration."""
        return self._device_config

    @property
    def base_url(self) -> str:
        """Return the Moonraker HTTP base URL."""
        scheme = "https" if self.config.ssl else "http"
        return f"{scheme}://{self.config.host}:{self.config.port}"

    @property
    def klippy_state(self) -> str | None:
        """Return Klipper's state (ready, startup, shutdown, error, disconnected)."""
        return self._klippy_state

    @property
    def print_state(self) -> str | None:
        """Return the print_stats state (standby, printing, paused, ...)."""
        return self._status.get("print_stats", {}).get("state")

    @property
    def is_active(self) -> bool:
        """Return True while a print is running or paused."""
        return self._klippy_state == "ready" and self.print_state in printer_state.ACTIVE_STATES

    @property
    def filename(self) -> str:
        """Return the current or last printed file."""
        return self._status.get("print_stats", {}).get("filename") or ""

    @property
    def progress(self) -> float:
        """Return the print progress as fraction 0..1."""
        if self.print_state == "complete":
            return 1.0
        return printer_state.compute_progress(self._status, self._metadata)

    @property
    def print_duration(self) -> float:
        """Return the effective print time in seconds (excluding pauses)."""
        return self._status.get("print_stats", {}).get("print_duration") or 0.0

    @property
    def total_duration(self) -> float:
        """Return the elapsed time since the print started in seconds (including pauses)."""
        stats = self._status.get("print_stats", {})
        return stats.get("total_duration") or stats.get("print_duration") or 0.0

    @property
    def remaining(self) -> float | None:
        """Return the estimated remaining time in seconds, None if unknown."""
        if not self.is_active:
            return None
        estimate = (self._metadata or {}).get("estimated_time")
        progress = self.progress
        if progress != self._progress_step[0]:
            self._progress_step = (progress, self.print_duration)
        progress = printer_state.interpolate_progress(progress, self.print_duration - self._progress_step[1], estimate)
        remaining = printer_state.compute_remaining(self.print_duration, progress, estimate)
        # Whole minutes, so the summary and the progress bar show the same time.
        return None if remaining is None else round(remaining / 60) * 60

    @property
    def webcams(self) -> list[Webcam]:
        """Return the configured webcams."""
        return self._webcams

    @property
    def current_webcam(self) -> Webcam | None:
        """Return the selected webcam, None if there is none."""
        if not self._webcams:
            return None
        return self._webcams[self._cam_index % len(self._webcams)]

    @property
    def artwork(self) -> str:
        """Return the image to show: camera while printing, else thumbnail or icon."""
        if self.is_active and self._camera_image:
            return self._camera_image
        return self._thumbnail or camera.placeholder_artwork()

    @property
    def status_text(self) -> str:
        """Return the state label, or the confirmation hint while one is armed."""
        return self.confirm.hint or printer_state.state_label(self._klippy_state, self.print_state)

    @property
    def summary(self) -> str:
        """Return a one-line summary: ``Printing · 42% · 1h 05m left``."""
        if self.confirm.hint:
            return self.confirm.hint
        parts = [printer_state.state_label(self._klippy_state, self.print_state)]
        if self.is_active:
            parts.append(f"{round(self.progress * 100)}%")
            remaining = self.remaining
            if remaining is not None:
                parts.append(f"{printer_state.format_duration(remaining)} left")
        return " · ".join(parts)

    @property
    def finish_time(self) -> str:
        """Return the estimated wall clock finish time as ``HH:MM``."""
        remaining = self.remaining
        if remaining is None:
            return ""
        return time.strftime("%H:%M", time.localtime(time.time() + remaining))

    @property
    def layer_text(self) -> str:
        """Return ``current / total`` layers; Klipper resets the layer when a print ends."""
        if self.print_state == "complete":
            total = self._status.get("print_stats", {}).get("info", {}).get("total_layer")
            total = total or (self._metadata or {}).get("layer_count")
            return f"{total} / {total}" if total else ""
        if not self.is_active:
            return ""
        return printer_state.format_layer(self._status, self._metadata)

    def temperature_text(self, obj_name: str) -> str:
        """Return the formatted temperature of a heater or sensor."""
        status = self._status.get(obj_name)
        current = None
        if status and status.get("temperature") is not None:
            current = printer_state.stable_round(status["temperature"], self._shown_temps.get(obj_name))
            self._shown_temps[obj_name] = current
        return printer_state.format_temperature(obj_name, status, current)

    def has_macro(self, macro: str) -> bool:
        """Return True if the macro exists in the current Klipper config."""
        return macro.lower() in self._available_macros

    def get_media_player_attributes(self) -> MediaPlayerAttributes:
        """Return the print view attributes for the media player entity."""
        active = self.is_active
        position = duration = 0
        if active:
            position = int(self.total_duration) // 5 * 5
            remaining = self.remaining
            if remaining is not None:
                duration = position + int(remaining)
        elif self.print_state == "complete":
            position = duration = int(self.total_duration)

        # Like other media players, report when the position was last valid.
        if position != self._position_stamp[0]:
            self._position_stamp = (position, datetime.now(timezone.utc).isoformat())

        cam = self.current_webcam
        return MediaPlayerAttributes(
            STATE=printer_state.media_state(self._klippy_state, self.print_state),
            MEDIA_TITLE=os.path.basename(self.filename) or self.name,
            MEDIA_ARTIST=self.summary,
            MEDIA_ALBUM=self.layer_text,
            MEDIA_IMAGE_URL=self.artwork,
            MEDIA_POSITION=position,
            MEDIA_DURATION=duration,
            MEDIA_POSITION_UPDATED_AT=self._position_stamp[1],
            MEDIA_TYPE="video",
            SOURCE=cam.name if cam else "",
            SOURCE_LIST=[w.name for w in self._webcams],
        )

    # =========================================================================
    # Connection (WebSocketDevice hooks)
    # =========================================================================

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.config.api_key} if self.config.api_key else {}

    async def create_websocket(self) -> aiohttp.ClientWebSocketResponse:
        """Open the Moonraker websocket and start the initial handshake."""
        scheme = "wss" if self.config.ssl else "ws"
        url = f"{scheme}://{self.config.host}:{self.config.port}/websocket"
        # No default headers: the session also fetches webcam snapshots from other hosts,
        # which must not receive the API key.
        self._session = aiohttp.ClientSession()
        try:
            ws = await self._session.ws_connect(
                url,
                headers=self._headers(),
                heartbeat=30,
                timeout=aiohttp.ClientWSTimeout(ws_close=10),
            )
        except Exception:
            await self._session.close()
            self._session = None
            raise
        # Runs once the base class has stored the socket and started reading.
        self._init_task = asyncio.create_task(self._initialize())
        return ws

    async def close_websocket(self) -> None:
        """Close the websocket and stop all background work."""
        for task in (self._init_task, self._camera_task, *self._tasks):
            if task and not task.done():
                task.cancel()
        self._init_task = self._camera_task = None
        self._tasks.clear()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("Connection closed"))
                future.exception()  # mark retrieved: callers being cancelled never read it
        self._pending.clear()
        if self._push_handle is not None:
            self._push_handle.cancel()
            self._push_handle = None
        self._klippy_state = None
        self._camera_image = None
        self._metadata_file = None
        if self._ws is not None:
            await self._ws.close()
        if self._session is not None:
            await self._session.close()
            self._session = None
        # The base class reports nothing for a clean close; don't leave entities on the last state.
        self.push_update()

    async def receive_message(self) -> dict[str, Any] | None:
        """Return the next JSON-RPC message, None when the socket closed."""
        msg = await self._ws.receive()
        if msg.type == aiohttp.WSMsgType.TEXT:
            return json.loads(msg.data)
        if msg.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSING,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
        ):
            return None
        return {}

    async def handle_message(self, message: dict[str, Any]) -> None:
        """Dispatch RPC responses and notifications."""
        if not message:
            return
        msg_id = message.get("id")
        if msg_id is not None:
            future = self._pending.get(msg_id)
            if future and not future.done():
                if "error" in message:
                    error = message["error"] or {}
                    future.set_exception(MoonrakerError(error.get("message", error)))
                else:
                    future.set_result(message.get("result"))
            return

        match message.get("method"):
            case "notify_status_update":
                params = message.get("params") or [{}]
                self._merge_status(params[0])
            case "notify_klippy_ready":
                self._spawn(self._subscribe())
            case "notify_klippy_shutdown":
                self._set_klippy_state("shutdown")
            case "notify_klippy_disconnected":
                self._set_klippy_state("disconnected")
            case "notify_gcode_response" | "notify_proc_stat_update":
                pass
            case "notify_webcams_changed":
                self._spawn(self._load_webcams())

    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = RPC_TIMEOUT) -> Any:
        """Send a JSON-RPC request and return its result."""
        if self._ws is None or self._ws.closed:
            raise ConnectionError("Not connected to Moonraker")
        msg_id = next(self._rpc_ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._ws.send_json({"jsonrpc": "2.0", "method": method, "params": params or {}, "id": msg_id})
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(msg_id, None)

    async def _initialize(self) -> None:
        """Identify, read the server state and subscribe if Klipper is ready."""
        try:
            info = _driver_info()
            identify = {
                "client_name": CLIENT_NAME,
                "version": info.get("version", "0.0.0"),
                "type": "other",
                "url": info.get("home_page", ""),
            }
            if self.config.api_key:
                identify["api_key"] = self.config.api_key
            await self.call("server.connection.identify", identify)
            server = await self.call("server.info")
            await self._load_webcams()
            if server.get("klippy_state") == "ready":
                await self._subscribe()
            else:
                self._set_klippy_state(server.get("klippy_state") or "disconnected")
        except ConnectionError as err:
            _LOG.debug("[%s] Connection closed during initialization: %s", self.log_id, err)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Initialization failed: %s", self.log_id, err)
            # Closing makes the base class reconnect with backoff.
            if self._ws is not None:
                await self._ws.close()

    async def _subscribe(self) -> None:
        """Subscribe to the Klipper objects we display."""
        objects = (await self.call("printer.objects.list")).get("objects", [])
        self._available_macros = {
            obj[len(MACRO_PREFIX) :].lower(): obj[len(MACRO_PREFIX) :]
            for obj in objects
            if obj.startswith(MACRO_PREFIX)
        }
        missing = [m for m in self.config.macros if not self.has_macro(m)]
        if missing:
            _LOG.warning("[%s] Configured macros not found: %s", self.log_id, missing)

        wanted: dict[str, list[str] | None] = {
            "webhooks": ["state", "state_message"],
            "print_stats": None,
            "virtual_sdcard": ["progress", "file_position", "is_active"],
            "display_status": ["progress"],
        }
        for heater in self.config.heaters:
            if heater in objects:
                wanted[heater] = ["temperature", "target"]

        result = await self.call("printer.objects.subscribe", {"objects": wanted})
        self._status = result.get("status", {})
        self._klippy_state = self._status.get("webhooks", {}).get("state", "ready")
        await self._refresh_metadata()
        self._after_status_change(immediate=True)

    def _merge_status(self, update: dict[str, dict[str, Any]]) -> None:
        for obj_name, fields in update.items():
            self._status.setdefault(obj_name, {}).update(fields)
        webhooks = update.get("webhooks", {})
        if "state" in webhooks:
            self._klippy_state = webhooks["state"]
        if self.filename != (self._metadata_file or "") or self._metadata_retry_due():
            self._spawn(self._refresh_metadata(), "Metadata refresh")
        self._after_status_change()

    def _metadata_retry_due(self) -> bool:
        failed_at = self._metadata_failed_at
        if failed_at is None or time.monotonic() - failed_at < METADATA_RETRY:
            return False
        self._metadata_failed_at = None
        self._metadata_file = None
        return True

    def _set_klippy_state(self, state: str) -> None:
        self._klippy_state = state
        self._after_status_change(immediate=True)

    def _after_status_change(self, immediate: bool = False) -> None:
        self._update_camera_loop()
        if immediate:
            self.push_update()
        else:
            self._schedule_push()

    def _schedule_push(self) -> None:
        if self._push_handle is None:
            self._push_handle = asyncio.get_running_loop().call_later(PUSH_DELAY, self._delayed_push)

    def _delayed_push(self) -> None:
        self._push_handle = None
        self.push_update()

    def _spawn(self, coro, name: str | None = None) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            _LOG.warning("[%s] %s failed: %r", self.log_id, task.get_name(), task.exception())

    # =========================================================================
    # Metadata, thumbnails and camera
    # =========================================================================

    async def _refresh_metadata(self) -> None:
        """Load file metadata and thumbnail of the current/last file."""
        filename = self.filename
        if filename == self._metadata_file:
            return
        self._metadata_file = filename
        self._metadata_failed_at = None
        self._metadata = None
        self._thumbnail = None
        if not filename:
            return
        try:
            metadata = await self.call("server.files.metadata", {"filename": filename})
        except (MoonrakerError, asyncio.TimeoutError, ConnectionError) as err:
            _LOG.debug("[%s] No metadata for %s: %s", self.log_id, filename, err)
            if self._metadata_file == filename:
                self._metadata_failed_at = time.monotonic()
            return
        if self._metadata_file != filename:
            return  # the file changed while loading
        self._metadata = metadata
        thumb = printer_state.pick_thumbnail(self._metadata, camera.MAX_SIZE)
        if thumb and self._session is not None:
            folder = os.path.dirname(filename)
            path = quote(os.path.join(folder, thumb) if folder else thumb)
            try:
                raw = await camera.fetch_image(
                    self._session,
                    f"{self.base_url}/server/files/gcodes/{path}",
                    headers=self._headers(),
                )
                thumbnail = await camera.to_artwork(raw)
                if self._metadata_file == filename:
                    self._thumbnail = thumbnail
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOG.debug("[%s] Thumbnail failed: %s", self.log_id, err)
        self.push_update()

    async def _load_webcams(self) -> None:
        try:
            result = await self.call("server.webcams.list")
        except (MoonrakerError, asyncio.TimeoutError) as err:
            _LOG.debug("[%s] Webcam list unavailable: %s", self.log_id, err)
            return
        self._webcams = [cam for data in result.get("webcams", []) if (cam := Webcam.from_moonraker(data)) is not None]
        _LOG.debug("[%s] Webcams: %s", self.log_id, [w.name for w in self._webcams])
        self._after_status_change(immediate=True)

    def _artwork_wanted(self) -> bool:
        """Return True if the media player entity is configured on the remote."""
        if self.driver is None:
            return True
        entity_id = create_entity_id(EntityTypes.MEDIA_PLAYER, self.identifier)
        return self.driver.api.configured_entities.contains(entity_id)

    def _update_camera_loop(self) -> None:
        wanted = self.is_connected and self.is_active and self.current_webcam is not None and self._artwork_wanted()
        running = self._camera_task is not None and not self._camera_task.done()
        if wanted and not running:
            self._camera_task = asyncio.create_task(self._camera_loop())
        elif not wanted and running:
            self._camera_task.cancel()
            self._camera_task = None
            self._camera_image = None

    async def _camera_loop(self) -> None:
        failures = 0
        while True:
            cam = self.current_webcam
            if cam is None or self._session is None:
                return
            url = camera.resolve_snapshot_url(cam.snapshot_url, self.config.host, self.config.ssl)
            try:
                raw = await camera.fetch_image(self._session, url)
                self._camera_image = await camera.to_artwork(raw, cam)
                failures = 0
                self.push_update()
            except Exception as err:  # pylint: disable=broad-exception-caught
                failures += 1
                log = _LOG.warning if failures == 1 else _LOG.debug
                log("[%s] Snapshot from %s failed: %s", self.log_id, camera.redact_url(url), err)
            await asyncio.sleep(max(1, int(self.config.cam_interval)))

    def _switch_camera(self, index: int) -> None:
        if not self._webcams:
            return
        self._cam_index = index % len(self._webcams)
        self._camera_image = None
        if self._camera_task is not None:
            self._camera_task.cancel()
            self._camera_task = None
        self._update_camera_loop()
        self.push_update()

    # =========================================================================
    # Commands
    # =========================================================================

    def _require_connection(self) -> None:
        if not self.is_connected:
            raise ConnectionError("Not connected to Moonraker")

    def _run_command(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a printer command without blocking the remote on its completion."""
        self._require_connection()
        _LOG.info("[%s] %s %s", self.log_id, method, params or "")
        self._spawn(self.call(method, params, timeout=COMMAND_TIMEOUT), method)

    def _guarded(self, key: str, label: str, method: str, params: dict[str, Any] | None = None) -> bool:
        """Run a command on the second press within the confirm window."""
        self._require_connection()
        if not self.confirm.press(key, label):
            return False
        self._run_command(method, params)
        return True

    async def pause_resume(self) -> None:
        """Pause a running print or resume a paused one."""
        match self.print_state:
            case "printing":
                self._run_command("printer.print.pause")
            case "paused":
                self._run_command("printer.print.resume")
            case _:
                _LOG.debug("[%s] Pause/resume ignored, not printing", self.log_id)

    async def cancel(self) -> bool:
        """Cancel the print (double press)."""
        return self._guarded("cancel", "Cancel", "printer.print.cancel")

    async def emergency_stop(self) -> bool:
        """Emergency stop / M112 (double press)."""
        return self._guarded("estop", "E-Stop", "printer.emergency_stop")

    async def firmware_restart(self) -> bool:
        """Klipper FIRMWARE_RESTART (double press)."""
        return self._guarded("fw_restart", "Firmware Restart", "printer.firmware_restart")

    async def run_macro(self, macro: str) -> bool:
        """Run a gcode macro (double press)."""
        name = self._available_macros.get(macro.lower())
        if name is None or not MACRO_NAME.fullmatch(name):
            _LOG.warning("[%s] Macro %r not available", self.log_id, macro)
            return False
        return self._guarded(f"macro:{name}", name, "printer.gcode.script", {"script": name})

    async def execute(self, command: str) -> None:
        """
        Run a printer command (see :class:`const.Commands`).

        :raises ValueError: for unknown commands
        :raises ConnectionError: if Moonraker is not connected
        """
        match command:
            case Commands.PAUSE_RESUME:
                await self.pause_resume()
            case Commands.CANCEL:
                await self.cancel()
            case Commands.EMERGENCY_STOP:
                await self.emergency_stop()
            case Commands.FIRMWARE_RESTART:
                await self.firmware_restart()
            case Commands.NEXT_CAMERA:
                await self.next_camera()
            case Commands.PREVIOUS_CAMERA:
                await self.previous_camera()
            case _:
                raise ValueError(f"Unknown command: {command}")

    async def next_camera(self) -> None:
        """Switch to the next webcam."""
        self._switch_camera(self._cam_index + 1)

    async def previous_camera(self) -> None:
        """Switch to the previous webcam."""
        self._switch_camera(self._cam_index - 1)

    async def select_camera(self, name: str) -> None:
        """Switch to the webcam with the given name."""
        for index, cam in enumerate(self._webcams):
            if cam.name == name:
                self._switch_camera(index)
                return

    # =========================================================================
    # Setup helpers
    # =========================================================================

    @classmethod
    async def probe(cls, host: str, port: int, api_key: str = "", ssl: bool = False) -> dict[str, Any]:
        """
        Query a Moonraker instance over HTTP for the setup flow.

        :return: dict with ``hostname``, ``heaters`` and ``macros``
        :raises PermissionError: on HTTP 401/403
        :raises ConnectionError: if Moonraker is unreachable
        """
        scheme = "https" if ssl else "http"
        base = f"{scheme}://{host}:{port}"
        headers = {"X-Api-Key": api_key} if api_key else {}
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                server = await _get_json(session, f"{base}/server/info")
                hostname = ""
                objects: list[str] = []
                if server.get("klippy_state") == "ready":
                    printer = await _get_json(session, f"{base}/printer/info")
                    hostname = printer.get("hostname", "")
                    listing = await _get_json(session, f"{base}/printer/objects/list")
                    objects = listing.get("objects", [])
        except aiohttp.ClientResponseError as err:
            if err.status in (401, 403):
                raise PermissionError(str(err)) from err
            raise ConnectionError(str(err)) from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise ConnectionError(str(err)) from err

        return {
            "hostname": hostname,
            "klippy_state": server.get("klippy_state"),
            "heaters": [obj for obj in objects if is_heater(obj)],
            "macros": sorted(obj[len(MACRO_PREFIX) :] for obj in objects if obj.startswith(MACRO_PREFIX)),
        }


async def _get_json(session: aiohttp.ClientSession, url: str) -> dict[str, Any]:
    async with session.get(url) as resp:
        resp.raise_for_status()
        return (await resp.json()).get("result", {})


def _driver_info() -> dict[str, Any]:
    try:
        with open("driver.json", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}
