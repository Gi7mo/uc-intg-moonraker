"""
Camera and thumbnail images.

Fetches webcam snapshots and gcode thumbnails, downsizes them to fit the
remote's artwork limit and returns them as base64 data URIs. Decoding and
encoding run in a worker thread to keep the event loop responsive.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import base64
import io
import logging
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urljoin

import aiohttp
from PIL import Image, ImageDraw, ImageOps

_LOG = logging.getLogger(__name__)

MAX_SIZE = 420
"""Largest artwork edge the remote accepts for full screen display."""

JPEG_QUALITY = 70
FETCH_TIMEOUT = aiohttp.ClientTimeout(total=8)


@dataclass
class Webcam:
    """A webcam as configured in Moonraker (``server.webcams.list``)."""

    name: str
    snapshot_url: str
    rotation: int = 0
    flip_horizontal: bool = False
    flip_vertical: bool = False

    @classmethod
    def from_moonraker(cls, data: dict) -> "Webcam | None":
        """Create a webcam from a Moonraker webcam entry, None if unusable."""
        url = data.get("snapshot_url") or ""
        if not url or data.get("enabled") is False:
            return None
        return cls(
            name=data.get("name") or "Camera",
            snapshot_url=url,
            rotation=int(data.get("rotation") or 0),
            flip_horizontal=bool(data.get("flip_horizontal")),
            flip_vertical=bool(data.get("flip_vertical")),
        )


def resolve_snapshot_url(snapshot_url: str, host: str, ssl: bool) -> str:
    """
    Resolve a possibly relative snapshot URL.

    Relative URLs (``/webcam/?action=snapshot``) are served by the web frontend
    (nginx on port 80/443), not by Moonraker itself.
    """
    if snapshot_url.startswith(("http://", "https://")):
        return snapshot_url
    base = f"{'https' if ssl else 'http'}://{host}/"
    return urljoin(base, snapshot_url)


async def fetch_image(
    session: aiohttp.ClientSession, url: str, headers: dict[str, str] | None = None
) -> bytes:
    """Download an image and return the raw bytes."""
    async with session.get(url, headers=headers, timeout=FETCH_TIMEOUT) as resp:
        resp.raise_for_status()
        return await resp.read()


async def to_artwork(raw: bytes, webcam: Webcam | None = None) -> str:
    """Convert raw image bytes into a downsized JPEG data URI."""
    return await asyncio.to_thread(encode_artwork, raw, webcam)


def encode_artwork(raw: bytes, webcam: Webcam | None = None) -> str:
    """Synchronous variant of :func:`to_artwork`."""
    with Image.open(io.BytesIO(raw)) as img:
        img = ImageOps.exif_transpose(img)
        if webcam:
            if webcam.flip_horizontal:
                img = ImageOps.mirror(img)
            if webcam.flip_vertical:
                img = ImageOps.flip(img)
            if webcam.rotation:
                img = img.rotate(-webcam.rotation, expand=True)
        img.thumbnail((MAX_SIZE, MAX_SIZE))
        if img.mode in ("RGBA", "LA", "P"):
            img = _flatten(img)
        return _data_uri(img)


@lru_cache(maxsize=1)
def placeholder_artwork() -> str:
    """Return a simple printer pictogram used when no image is available."""
    size = 240
    img = Image.new("RGB", (size, size), (32, 34, 38))
    draw = ImageDraw.Draw(img)
    fg = (200, 204, 210)
    draw.rectangle((40, 40, 200, 200), outline=fg, width=8)  # frame
    draw.line((40, 80, 200, 80), fill=fg, width=6)  # gantry
    draw.rectangle((105, 70, 135, 95), fill=fg)  # toolhead
    draw.polygon([(112, 95), (128, 95), (120, 110)], fill=fg)  # nozzle
    draw.rectangle((70, 170, 170, 180), fill=fg)  # bed
    draw.rectangle((95, 145, 145, 170), outline=fg, width=4)  # part
    return _data_uri(img)


def _flatten(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    background = Image.new("RGB", rgba.size, (32, 34, 38))
    background.paste(rgba, mask=rgba.split()[-1])
    return background


def _data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
