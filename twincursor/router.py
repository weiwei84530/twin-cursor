"""Input routing: the hot path between the Interception driver and Windows.

Every stroke of the two tracked mice is filtered by the driver and must be
re-sent by this loop, so the handler does as little work as possible and
forwards immediately. Anything slow (drawing, registry writes, tray/UI) is
kept on other threads.
"""

import logging
import threading
import time

from interception import Interception, MouseStroke
from interception.constants import (
    FilterMouseButtonFlag,
    MouseButtonFlag,
    MouseFlag,
)

from . import winapi as w

log = logging.getLogger(__name__)

_MOUSE_FILTER = (
    FilterMouseButtonFlag.FILTER_MOUSE_ALL | FilterMouseButtonFlag.FILTER_MOUSE_MOVE
)

# Left/right swap map used when a device has "Mirror Buttons" enabled.
_MIRROR_MAP = {
    MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN: MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN,
    MouseButtonFlag.MOUSE_LEFT_BUTTON_UP: MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP,
    MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN: MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN,
    MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP: MouseButtonFlag.MOUSE_LEFT_BUTTON_UP,
}
_MIRROR_MASK = 0
for _flag in _MIRROR_MAP:
    _MIRROR_MASK |= _flag

_BUTTON_DOWN_FLAGS = {
    MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN: "left",
    MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN: "right",
    MouseButtonFlag.MOUSE_MIDDLE_BUTTON_DOWN: "middle",
    MouseButtonFlag.MOUSE_BUTTON_4_DOWN: "x1",
    MouseButtonFlag.MOUSE_BUTTON_5_DOWN: "x2",
}
_BUTTON_UP_FLAGS = {
    MouseButtonFlag.MOUSE_LEFT_BUTTON_UP: "left",
    MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP: "right",
    MouseButtonFlag.MOUSE_MIDDLE_BUTTON_UP: "middle",
    MouseButtonFlag.MOUSE_BUTTON_4_UP: "x1",
    MouseButtonFlag.MOUSE_BUTTON_5_UP: "x2",
}

# Injected strokes are applied to the OS cursor asynchronously, so right
# after the active mouse moved, GetCursorPos may not include its in-flight
# deltas yet — and those deltas would land on top of a SetCursorPos done
# while switching, teleporting one cursor onto the other. Switching is
# therefore only allowed once the active mouse has been quiet long enough
# for its strokes to have settled; until then the other mouse moves its
# ghost cursor instead.
_SWITCH_COOLDOWN = 0.05  # seconds

# Windows pointer-speed setting (1-20) to raw-delta multiplier, without
# "enhance pointer precision". 10 is the 1.0 default.
_SPEED_MULTIPLIERS = {
    1: 1 / 32, 2: 1 / 16, 3: 1 / 8, 4: 2 / 8, 5: 3 / 8,
    6: 4 / 8, 7: 5 / 8, 8: 6 / 8, 9: 7 / 8, 10: 1.0,
    11: 1.25, 12: 1.5, 13: 1.75, 14: 2.0, 15: 2.25,
    16: 2.5, 17: 2.75, 18: 3.0, 19: 3.25, 20: 3.5,
}


class MouseDevice:
    """State of one tracked physical mouse."""

    def __init__(self, device_num: int, hwid: str, name: str, settings_key: str):
        self.device_num = device_num
        self.hwid = hwid
        self.name = name
        self.settings_key = settings_key
        self.is_mirrored = False
        # Remembered cursor position; authoritative only while inactive.
        # Kept as floats so slow ghost movements can accumulate fractions.
        self.x = 0.0
        self.y = 0.0
        self.buttons_down: set[str] = set()


def connect(timeout: float = 30.0, interval: float = 1.0):
    """Create the Interception context and find two connected mice.

    Devices are identified by hardware ID (a populated HWID means a device
    is actually present in that slot). Retries for a while because at logon
    the driver may not have enumerated the devices yet.

    Returns (interception, mice) where mice maps device number to MouseDevice.
    Raises RuntimeError when the driver is missing or fewer than two mice are
    found within the timeout.
    """
    try:
        interception = Interception()
    except Exception as exc:
        raise RuntimeError(
            "Could not open the Interception driver. Is it installed? "
            "(https://github.com/oblitum/Interception)"
        ) from exc

    deadline = time.monotonic() + timeout
    while True:
        found: list[tuple[int, str]] = []
        for num, device in enumerate(interception.devices):
            if not Interception.is_mouse(num):
                continue
            try:
                hwid = device.get_HWID()
            except OSError:
                hwid = None
            if hwid:
                # The driver returns a NUL-separated list of hardware IDs;
                # the first entry is the most specific one.
                found.append((num, hwid.split("\x00", 1)[0].strip()))

        if len(found) >= 2:
            break
        if time.monotonic() >= deadline:
            interception.destroy()
            raise RuntimeError(
                f"Found {len(found)} mouse device(s), but two are required. "
                "Please connect two mice."
            )
        log.debug("Waiting for mice: %d found so far", len(found))
        time.sleep(interval)

    for num, hwid in found:
        log.debug("Mouse device %d: %s", num, hwid)

    mice: dict[int, MouseDevice] = {}
    used_keys: set[str] = set()
    for index, (num, hwid) in enumerate(found[:2]):
        # Two identical mice share a HWID; disambiguate the settings key.
        key = hwid if hwid not in used_keys else f"{hwid}#2"
        used_keys.add(key)
        name = f"Mouse {chr(65 + index)}"
        mice[num] = MouseDevice(num, hwid, name, key)
        log.info("Using %s: device %d (%s)", name, num, hwid)
    return interception, mice


class Router:
    """Routes strokes between the two mice, the OS cursor and the overlay."""

    def __init__(self, interception: Interception, mice: dict[int, MouseDevice], overlay):
        self._interception = interception
        self._mice = mice
        self._overlay = overlay
        self._active = next(iter(mice.values()))
        self._speed_factor = _SPEED_MULTIPLIERS.get(w.get_mouse_speed(), 1.0)
        self._last_active_time = 0.0

        x, y = w.get_cursor_pos()
        for mouse in mice.values():
            mouse.x, mouse.y = float(x), float(y)

    @property
    def inactive_position(self) -> tuple[int, int]:
        for mouse in self._mice.values():
            if mouse is not self._active:
                return int(mouse.x), int(mouse.y)
        return w.get_cursor_pos()

    def set_mirrored(self, mouse: MouseDevice, value: bool) -> None:
        """Called from the settings UI thread; a bool flip is race-free."""
        mouse.is_mirrored = value
        log.info("%s button mirror: %s", mouse.name, "on" if value else "off")

    def run(self, stop: threading.Event) -> None:
        """Process strokes until the stop event is set. Runs on the caller."""
        interception = self._interception
        for mouse in self._mice.values():
            interception.devices[mouse.device_num].set_filter(_MOUSE_FILTER)
        try:
            while not stop.is_set():
                device_num = interception.await_input(500)
                if device_num is None:
                    continue
                stroke = interception.devices[device_num].receive()
                if stroke is None:
                    continue
                if device_num not in self._mice or not isinstance(stroke, MouseStroke):
                    # Never swallow input we do not manage.
                    interception.send(device_num, stroke)
                    continue
                try:
                    self._handle(device_num, stroke)
                except Exception:
                    # Forward the raw stroke rather than dropping the input.
                    log.exception("Error handling stroke, forwarding as-is")
                    interception.send(device_num, stroke)
        finally:
            for mouse in self._mice.values():
                interception.devices[mouse.device_num].set_filter(
                    FilterMouseButtonFlag.FILTER_MOUSE_NONE
                )

    # -- stroke handling (hot path) ----------------------------------------

    def _handle(self, device_num: int, stroke: MouseStroke) -> None:
        mouse = self._mice[device_num]

        if mouse is not self._active:
            if self._active.buttons_down or (
                time.monotonic() - self._last_active_time < _SWITCH_COOLDOWN
            ):
                # The active mouse is dragging or still moving: let the other
                # mouse move its ghost cursor, but swallow everything else.
                self._ghost_move(mouse, stroke)
                return
            self._switch_to(mouse)

        if stroke.button_flags and mouse.is_mirrored and (
            stroke.button_flags & _MIRROR_MASK
        ):
            stroke.button_flags = self._mirror(stroke.button_flags)

        self._interception.send(device_num, stroke)
        self._last_active_time = time.monotonic()

        if stroke.button_flags:
            self._track_buttons(mouse, stroke.button_flags)
            if log.isEnabledFor(logging.DEBUG):
                log.debug(
                    "%s buttons=%#x data=%d held=%s",
                    mouse.name, stroke.button_flags, stroke.button_data,
                    mouse.buttons_down,
                )

    @staticmethod
    def _mirror(button_flags: int) -> int:
        result = button_flags & ~_MIRROR_MASK
        for source, target in _MIRROR_MAP.items():
            if button_flags & source:
                result |= target
        return result

    @staticmethod
    def _track_buttons(mouse: MouseDevice, button_flags: int) -> None:
        for flag, button in _BUTTON_DOWN_FLAGS.items():
            if button_flags & flag:
                mouse.buttons_down.add(button)
        for flag, button in _BUTTON_UP_FLAGS.items():
            if button_flags & flag:
                mouse.buttons_down.discard(button)

    def _switch_to(self, mouse: MouseDevice) -> None:
        previous = self._active
        x, y = w.get_cursor_pos()
        previous.x, previous.y = float(x), float(y)
        previous.buttons_down.clear()

        self._active = mouse
        w.user32.SetCursorPos(int(mouse.x), int(mouse.y))
        self._overlay.move_to(int(previous.x), int(previous.y))
        self._speed_factor = _SPEED_MULTIPLIERS.get(w.get_mouse_speed(), 1.0)
        log.debug(
            "Switched to %s at (%d, %d)", mouse.name, int(mouse.x), int(mouse.y)
        )

    def _ghost_move(self, mouse: MouseDevice, stroke: MouseStroke) -> None:
        if stroke.flags & MouseFlag.MOUSE_MOVE_ABSOLUTE:
            return  # absolute devices (tablets, touchpads) are not ghost-moved
        if stroke.x == 0 and stroke.y == 0:
            return  # buttons/wheel from the idle mouse stay swallowed

        left, top, width, height = w.get_virtual_screen_rect()
        mouse.x = min(max(mouse.x + stroke.x * self._speed_factor, left),
                      left + width - 1)
        mouse.y = min(max(mouse.y + stroke.y * self._speed_factor, top),
                      top + height - 1)
        self._overlay.move_to(int(mouse.x), int(mouse.y))
