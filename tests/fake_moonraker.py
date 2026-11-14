"""A minimal in-process Moonraker server for tests."""

import asyncio
import io
from typing import Any

from aiohttp import WSMsgType, web
from PIL import Image


def jpeg_bytes(size=(1280, 720), color=(200, 30, 30)) -> bytes:
    """Return a JPEG image of the given size."""
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def png_bytes(size=(300, 300)) -> bytes:
    """Return a transparent PNG image."""
    buf = io.BytesIO()
    Image.new("RGBA", size, (0, 200, 0, 128)).save(buf, format="PNG")
    return buf.getvalue()


class FakeMoonraker:
    """Fake Moonraker: JSON-RPC websocket, snapshot and thumbnail endpoints."""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key
        self.klippy_state = "ready"
        self.objects = [
            "webhooks",
            "print_stats",
            "virtual_sdcard",
            "extruder",
            "heater_bed",
            "temperature_sensor chamber",
            "extruder_stepper belt",
            "gcode_macro LOAD_FILAMENT",
            "gcode_macro _HELPER",
        ]
        self.status: dict[str, dict[str, Any]] = {
            "webhooks": {"state": "ready", "state_message": ""},
            "print_stats": {
                "state": "printing",
                "filename": "parts/benchy.gcode",
                "print_duration": 600.0,
                "info": {"current_layer": 10, "total_layer": 100},
            },
            "virtual_sdcard": {"progress": 0.25, "file_position": 1100, "is_active": True},
            "extruder": {"temperature": 214.6, "target": 215.0},
            "heater_bed": {"temperature": 60.2, "target": 60.0},
            "temperature_sensor chamber": {"temperature": 35.4},
        }
        self.metadata = {
            "estimated_time": 2400,
            "gcode_start_byte": 100,
            "gcode_end_byte": 4100,
            "layer_count": 100,
            "thumbnails": [
                {"width": 32, "height": 32, "relative_path": ".thumbs/benchy-32x32.png"},
                {"width": 300, "height": 300, "relative_path": ".thumbs/benchy-300x300.png"},
            ],
        }
        self.calls: list[tuple[str, dict]] = []
        self.snapshot_requests = 0
        self.thumbnail_requests: list[str] = []
        self.connections = 0
        self.subscribed = asyncio.Event()
        self._sockets: list[web.WebSocketResponse] = []
        self._runner: web.AppRunner | None = None
        self.port = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/websocket", self._ws_handler)
        app.router.add_get("/snapshot", self._snapshot)
        app.router.add_get("/server/files/gcodes/{path:.*}", self._thumbnail)
        app.router.add_get("/server/info", self._http_server_info)
        app.router.add_get("/printer/info", self._http_printer_info)
        app.router.add_get("/printer/objects/list", self._http_objects)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # pylint: disable=protected-access

    async def stop(self) -> None:
        await self.drop_connections()
        if self._runner:
            await self._runner.cleanup()

    async def drop_connections(self) -> None:
        for ws in list(self._sockets):
            await ws.close()
        self._sockets.clear()

    async def notify(self, method: str, params: list | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        for ws in self._sockets:
            await ws.send_json(msg)

    async def push_status(self, update: dict[str, dict[str, Any]]) -> None:
        for name, fields in update.items():
            self.status.setdefault(name, {}).update(fields)
        await self.notify("notify_status_update", [update, 1234.5])

    def _authorized(self, request: web.Request) -> bool:
        return not self.api_key or request.headers.get("X-Api-Key") == self.api_key

    async def _ws_handler(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        self._sockets.append(ws)
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            req = msg.json()
            method, params = req["method"], req.get("params", {})
            self.calls.append((method, params))
            try:
                result = self._rpc(method, params)
            except KeyError as err:
                await ws.send_json(
                    {"jsonrpc": "2.0", "id": req["id"], "error": {"code": 404, "message": str(err)}}
                )
                continue
            await ws.send_json({"jsonrpc": "2.0", "id": req["id"], "result": result})
            if method == "printer.objects.subscribe":
                self.subscribed.set()
        if ws in self._sockets:
            self._sockets.remove(ws)
        return ws

    def _rpc(self, method: str, params: dict) -> Any:
        match method:
            case "server.connection.identify":
                return {"connection_id": 42}
            case "server.info":
                return {"klippy_state": self.klippy_state}
            case "server.webcams.list":
                return {
                    "webcams": [
                        {"name": "Nozzle", "snapshot_url": f"{self.base_url}/snapshot", "enabled": True},
                        {"name": "Room", "snapshot_url": f"{self.base_url}/snapshot", "enabled": True},
                        {"name": "Off", "snapshot_url": f"{self.base_url}/snapshot", "enabled": False},
                    ]
                }
            case "printer.objects.list":
                return {"objects": self.objects}
            case "printer.objects.subscribe":
                return {
                    "eventtime": 1.0,
                    "status": {k: dict(self.status[k]) for k in params["objects"] if k in self.status},
                }
            case "server.files.metadata":
                if params["filename"] != "parts/benchy.gcode":
                    raise KeyError(params["filename"])
                return self.metadata
            case _:
                return "ok"

    async def _snapshot(self, _request: web.Request) -> web.Response:
        self.snapshot_requests += 1
        return web.Response(body=jpeg_bytes(), content_type="image/jpeg")

    async def _thumbnail(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        self.thumbnail_requests.append(request.match_info["path"])
        return web.Response(body=png_bytes(), content_type="image/png")

    async def _http_server_info(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        return web.json_response({"result": {"klippy_state": self.klippy_state}})

    async def _http_printer_info(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        return web.json_response({"result": {"hostname": "voron"}})

    async def _http_objects(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        return web.json_response({"result": {"objects": self.objects}})
