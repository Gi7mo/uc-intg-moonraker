"""Tests for the double-press confirmation guard."""

import asyncio

from confirm import ConfirmGuard


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def make_guard(window=3.0):
    clock = FakeClock()
    changes = []
    guard = ConfirmGuard(lambda: changes.append(guard.hint), window=window, clock=clock)
    return guard, clock, changes


def test_second_press_within_window_confirms():
    guard, clock, changes = make_guard()
    assert guard.press("estop", "E-Stop") is False
    assert guard.hint == "Press E-Stop again to confirm"
    clock.now += 2.9
    assert guard.press("estop", "E-Stop") is True
    assert guard.hint == ""
    assert changes == ["Press E-Stop again to confirm", ""]


def test_second_press_after_window_rearms():
    guard, clock, _ = make_guard()
    guard.press("cancel", "Cancel")
    clock.now += 3.5
    assert guard.press("cancel", "Cancel") is False
    clock.now += 1
    assert guard.press("cancel", "Cancel") is True


def test_different_action_rearms():
    guard, clock, _ = make_guard()
    guard.press("cancel", "Cancel")
    assert guard.press("estop", "E-Stop") is False
    assert "E-Stop" in guard.hint
    clock.now += 1
    assert guard.press("estop", "E-Stop") is True


def test_repeat_faster_than_double_press_is_ignored():
    guard, clock, changes = make_guard()
    guard.press("estop", "E-Stop")
    clock.now += 0.1
    assert guard.press("estop", "E-Stop") is False  # key repeat or command sequence
    assert guard.hint == "Press E-Stop again to confirm"
    clock.now += 0.5
    assert guard.press("estop", "E-Stop") is True
    assert changes == ["Press E-Stop again to confirm", ""]


async def test_hint_expires_automatically():
    changes = []
    guard = ConfirmGuard(lambda: changes.append(guard.hint), window=0.05)
    guard.press("macro:X", "X")
    await asyncio.sleep(0.1)
    assert guard.hint == ""
    assert changes == ["Press X again to confirm", ""]
