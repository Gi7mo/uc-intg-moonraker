"""
Double-press confirmation for destructive actions.

The remote has no confirmation dialog, so dangerous actions (emergency stop,
cancel, firmware restart, macros) must be pressed twice within a short window.
The first press arms the action and exposes a hint text; the second press
within the window executes it.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import time
from collections.abc import Callable

from const import CONFIRM_WINDOW


class ConfirmGuard:
    """Track one armed action at a time and confirm it on the second press."""

    def __init__(
        self,
        on_change: Callable[[], None],
        window: float = CONFIRM_WINDOW,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Create a guard.

        :param on_change: called whenever the hint text changes (arm, confirm, expire)
        :param window: seconds within which the second press must follow
        :param clock: monotonic time source, injectable for tests
        """
        self._on_change = on_change
        self._window = window
        self._clock = clock
        self._key: str | None = None
        self._label = ""
        self._armed_at = 0.0
        self._expiry: asyncio.TimerHandle | None = None

    @property
    def hint(self) -> str:
        """Return the hint to display while an action is armed, else empty."""
        if self._key is None:
            return ""
        return f"Press {self._label} again to confirm"

    def press(self, key: str, label: str) -> bool:
        """
        Register a press of the guarded action ``key``.

        :return: True if this press confirms the action and it should run now
        """
        now = self._clock()
        if self._key == key and now - self._armed_at <= self._window:
            self._reset()
            return True

        self._key = key
        self._label = label
        self._armed_at = now
        self._schedule_expiry()
        self._on_change()
        return False

    def _schedule_expiry(self) -> None:
        if self._expiry:
            self._expiry.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._expiry = None
            return
        self._expiry = loop.call_later(self._window, self._expire)

    def _expire(self) -> None:
        self._expiry = None
        if self._key is not None:
            self._key = None
            self._on_change()

    def _reset(self) -> None:
        if self._expiry:
            self._expiry.cancel()
            self._expiry = None
        self._key = None
        self._on_change()
