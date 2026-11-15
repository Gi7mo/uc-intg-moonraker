"""Tests for image processing."""

import base64
import io

import aiohttp
import camera
import pytest
from camera import Webcam
from fake_moonraker import FakeMoonraker, jpeg_bytes, png_bytes
from PIL import Image, UnidentifiedImageError


def decode(uri: str) -> Image.Image:
    assert uri.startswith("data:image/jpeg;base64,")
    return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))


def test_snapshot_is_downsized_to_fit():
    img = decode(camera.encode_artwork(jpeg_bytes((1920, 1080))))
    assert img.format == "JPEG"
    assert max(img.size) == camera.MAX_SIZE
    assert img.size == (420, 236)


def test_small_image_is_not_upscaled():
    assert decode(camera.encode_artwork(jpeg_bytes((200, 100)))).size == (200, 100)


def test_rotation_and_transparency():
    cam = Webcam("cam", "/snap", rotation=90)
    assert decode(camera.encode_artwork(jpeg_bytes((400, 200)), cam)).size == (200, 400)
    assert decode(camera.encode_artwork(png_bytes((300, 300)))).mode == "RGB"


def test_payload_stays_small():
    uri = camera.encode_artwork(jpeg_bytes((1920, 1080)))
    assert len(uri) < 60_000


def test_placeholder():
    assert decode(camera.placeholder_artwork()).size == (240, 240)


def test_webcam_from_moonraker():
    assert Webcam.from_moonraker({"name": "x", "snapshot_url": ""}) is None
    assert Webcam.from_moonraker({"name": "x", "snapshot_url": "/s", "enabled": False}) is None
    cam = Webcam.from_moonraker({"name": "x", "snapshot_url": "/s", "rotation": 180})
    assert cam.rotation == 180


def test_resolve_snapshot_url():
    assert camera.resolve_snapshot_url("http://cam/snap", "printer", False) == "http://cam/snap"
    assert (
        camera.resolve_snapshot_url("/webcam/?action=snapshot", "printer", False)
        == "http://printer/webcam/?action=snapshot"
    )
    assert camera.resolve_snapshot_url("webcam/snap", "printer", True) == "https://printer/webcam/snap"


def test_rejects_huge_and_unknown_images(monkeypatch):
    monkeypatch.setattr(camera, "MAX_PIXELS", 100 * 100)
    with pytest.raises(ValueError):
        camera.encode_artwork(jpeg_bytes((200, 200)))
    with pytest.raises(UnidentifiedImageError):
        camera.encode_artwork(b"%PDF-1.4 not an image")


def test_redact_url():
    assert camera.redact_url("http://user:pw@cam.local:8080/snap?x=1") == "http://cam.local:8080/snap?x=1"
    assert camera.redact_url("http://cam.local/snap") == "http://cam.local/snap"


async def test_download_size_limit(monkeypatch):
    fake = FakeMoonraker()
    await fake.start()
    monkeypatch.setattr(camera, "MAX_DOWNLOAD", 1000)
    try:
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ValueError):
                await camera.fetch_image(session, f"{fake.base_url}/snapshot")
    finally:
        await fake.stop()
