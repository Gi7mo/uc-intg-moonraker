"""
Printer state helpers.

Pure functions that turn Klipper/Moonraker status objects into the values
shown on the remote: progress, time remaining, state labels and temperatures.
Kept free of I/O so they can be unit tested in isolation.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

from typing import Any

from const import has_target
from ucapi import media_player

ACTIVE_STATES = ("printing", "paused")
TEMP_HYSTERESIS = 0.75
"""Degrees a reading must move away from the displayed value before it changes."""

_STATE_LABELS = {
    "standby": "Ready",
    "printing": "Printing",
    "paused": "Paused",
    "complete": "Complete",
    "cancelled": "Cancelled",
    "error": "Error",
}

_KLIPPY_LABELS = {
    "startup": "Klipper starting",
    "shutdown": "Klipper shutdown",
    "error": "Klipper error",
    "disconnected": "Klipper disconnected",
}


def compute_progress(status: dict[str, Any], metadata: dict[str, Any] | None) -> float:
    """
    Return the print progress as a fraction between 0 and 1.

    Prefers the slicer's progress (``M73``, reported as ``display_status.progress``):
    it is time based, while the file position lags badly in slow first layers.
    Otherwise uses the gcode byte range from the file metadata (ignoring the
    slicer header and footer), then ``virtual_sdcard.progress``. Without ``M73``,
    Klipper reports the file progress as ``display_status.progress``, so a value
    equal to ``virtual_sdcard.progress`` is not taken as slicer progress.
    """
    sdcard = status.get("virtual_sdcard", {})
    slicer = status.get("display_status", {}).get("progress")
    if slicer and slicer != sdcard.get("progress"):
        return _clamp(slicer)
    position = sdcard.get("file_position")
    if metadata and position:
        start = metadata.get("gcode_start_byte")
        end = metadata.get("gcode_end_byte")
        if start is not None and end and end > start:
            return _clamp((position - start) / (end - start))
    return _clamp(sdcard.get("progress") or 0.0)


def interpolate_progress(progress: float, since_step: float, estimated_time: float | None, step: float = 0.01) -> float:
    """
    Advance a coarse progress value by the time spent since it last changed.

    The slicer reports progress in whole percent, minutes apart on long prints.
    Without interpolation the remaining time would only change in those steps.
    The result stays below the next step so it never runs ahead of the printer.

    :param since_step: print seconds since ``progress`` last changed
    :param estimated_time: slicer estimate of the whole print in seconds
    """
    if not estimated_time or since_step <= 0:
        return progress
    return min(progress + since_step / estimated_time, progress + step * 0.99, 1.0)


def compute_remaining(print_duration: float, progress: float, estimated_time: float | None) -> float | None:
    """
    Return the estimated remaining print time in seconds.

    Blends the slicer estimate with a progress-based extrapolation: early in a
    print the slicer estimate dominates, later the measured rate takes over.
    Returns None when no estimate is possible yet.
    """
    file_based = None
    if progress > 0.001 and print_duration > 0:
        file_based = max(print_duration / progress - print_duration, 0.0)

    if estimated_time:
        slicer = max(estimated_time - print_duration, 0.0)
        if file_based is None:
            return slicer
        return slicer * (1 - progress) + file_based * progress

    return file_based


def media_state(klippy_state: str | None, print_state: str | None) -> media_player.States:
    """Map Klipper and print state onto a media player state."""
    if klippy_state != "ready":
        return media_player.States.OFF
    if print_state == "printing":
        return media_player.States.PLAYING
    if print_state == "paused":
        return media_player.States.PAUSED
    return media_player.States.ON


def state_label(klippy_state: str | None, print_state: str | None) -> str:
    """Return a short human readable printer state."""
    if klippy_state != "ready":
        return _KLIPPY_LABELS.get(klippy_state or "", "Connecting")
    return _STATE_LABELS.get(print_state or "", "Ready")


def format_duration(seconds: float | None) -> str:
    """Format seconds as ``1h 05m`` / ``12m`` / ``45s``."""
    if seconds is None:
        return ""
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def format_temperature(obj_name: str, obj_status: dict[str, Any] | None, current: int | None = None) -> str:
    """
    Format a heater as ``215 / 220 °C`` or a passive sensor as ``45 °C``.

    :param current: displayed current temperature, overriding plain rounding
    """
    if not obj_status or obj_status.get("temperature") is None:
        return "–"
    if current is None:
        current = round(obj_status["temperature"])
    if has_target(obj_name) and "target" in obj_status:
        return f"{current} / {round(obj_status['target'] or 0)} °C"
    return f"{current} °C"


def stable_round(value: float, shown: int | None, hysteresis: float = TEMP_HYSTERESIS) -> int:
    """
    Round ``value`` but keep ``shown`` until the value moved far enough away.

    Prevents a reading hovering around x.5 from flipping the display (and
    sending an update to the remote) several times per second.
    """
    if shown is None or abs(value - shown) >= hysteresis:
        return round(value)
    return shown


def format_layer(status: dict[str, Any], metadata: dict[str, Any] | None) -> str:
    """Return ``current / total`` layers, empty if unknown."""
    info = status.get("print_stats", {}).get("info") or {}
    current = info.get("current_layer")
    total = info.get("total_layer") or (metadata or {}).get("layer_count")
    if current is None and total is None:
        return ""
    return f"{current or 0} / {total or '?'}"


def pick_thumbnail(metadata: dict[str, Any] | None, max_size: int = 420) -> str | None:
    """Return the relative path of the largest thumbnail not wider than ``max_size``."""
    thumbs = (metadata or {}).get("thumbnails") or []
    candidates = [t for t in thumbs if t.get("relative_path")]
    if not candidates:
        return None
    fitting = [t for t in candidates if t.get("width", 0) <= max_size]
    pool = fitting or candidates
    best = max(pool, key=lambda t: t.get("width", 0)) if fitting else min(pool, key=lambda t: t.get("width", 0))
    return best["relative_path"]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
