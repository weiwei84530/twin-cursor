"""Input routing: the hot path between the Interception driver and Windows.

The router runs in one of two modes, switchable at runtime from the
settings UI:

- Dual-mouse mode (two devices assigned): every stroke of both mice is
  filtered by the driver and re-sent by this loop, so the handler does as
  little work as possible and forwards immediately. Anything slow (drawing,
  registry writes, tray/UI) is kept on other threads.
- Single-mouse / idle mode (one or zero devices assigned): no driver
  filters are set on the assigned mouse, so its input flows through Windows
  untouched with no real-time processing at all. Button mirroring is done
  with the standard SwapMouseButton API instead, and the ghost overlay is
  hidden.

In both modes, mice that are present but not assigned to a slot are
blocked: their strokes are filtered and swallowed, so setting a slot to
"None" actually disables the leftover mouse instead of letting it drive
the cursor alongside the assigned one.
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
from .device_names import find_keyboard_hwids

log = logging.getLogger(__name__)


class DriverUnavailableError(RuntimeError):
    """The Interception driver could not be opened (missing or inactive)."""


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
    """State of one physical mouse known to the app (assigned to a slot or not)."""

    def __init__(self, device_num: int, hwid: str, settings_key: str, label: str):
        self.device_num = device_num
        self.hwid = hwid
        self.settings_key = settings_key
        self.label = label
        self.is_mirrored = False
        # Remembered cursor position; authoritative only while inactive.
        # Kept as floats so slow ghost movements can accumulate fractions.
        self.x = 0.0
        self.y = 0.0
        self.buttons_down: set[str] = set()


# Hardware IDs already reported (once, at info level) as keyboard-backed.
_logged_keyboard_hwids: set[str] = set()


def enumerate_mice(interception) -> list[tuple[int, str]]:
    """Return (device_num, hardware_id) for every present mouse device.

    A populated HWID means a device is actually present in that slot. The
    driver returns a NUL-separated list of hardware IDs; the first entry is
    the most specific one. Some keyboards register an extra mouse-class HID
    collection that the driver then lists as a mouse; those entries are
    filtered out so a keyboard never shows up as a selectable mouse.
    """
    found: list[tuple[int, str]] = []
    for num, device in enumerate(interception.devices):
        if not Interception.is_mouse(num):
            continue
        try:
            hwid = device.get_HWID()
        except OSError:
            hwid = None
        if hwid:
            found.append((num, hwid.split("\x00", 1)[0].strip()))

    keyboards = find_keyboard_hwids(hwid for _, hwid in found)
    if keyboards:
        for _, hwid in found:
            if hwid in keyboards and hwid not in _logged_keyboard_hwids:
                _logged_keyboard_hwids.add(hwid)
                log.info("Ignoring keyboard device listed as a mouse: %s", hwid)
        found = [(num, hwid) for num, hwid in found if hwid not in keyboards]
    return found


def assign_keys(found) -> list[tuple[str, int, str]]:
    """Give each (device_num, hwid) a unique settings key.

    Two identical mice share a HWID; later duplicates get a #2/#3 suffix in
    enumeration order.
    """
    result = []
    used: set[str] = set()
    for num, hwid in found:
        key, n = hwid, 2
        while key in used:
            key = f"{hwid}#{n}"
            n += 1
        used.add(key)
        result.append((key, num, hwid))
    return result


def connect(wanted_keys=(), timeout: float = 30.0, interval: float = 1.0,
            settle: float = 3.0):
    """Create the Interception context and enumerate connected mice.

    Waits up to `timeout` for the first mouse, because at logon the driver
    may not have enumerated the devices yet. Once at least one mouse is
    present, it keeps polling a little longer: until every key in
    `wanted_keys` (the stored device selection) is present, or — with no
    stored selection — up to `settle` seconds for a second mouse, so a
    dual-mouse setup coming up at logon is not misdetected as single-mouse.

    Returns (interception, found) where found is [(device_num, hwid)].
    Raises RuntimeError when the driver is missing or no mouse shows up.
    """
    try:
        interception = Interception()
    except Exception as exc:
        raise DriverUnavailableError(
            "Could not open the Interception driver. Is it installed? "
            "(https://github.com/oblitum/Interception)"
        ) from exc

    wanted = [key for key in wanted_keys if isinstance(key, str)]
    deadline = time.monotonic() + timeout
    settle_deadline = None
    while True:
        found = enumerate_mice(interception)
        now = time.monotonic()
        if found:
            keys = {key for key, _, _ in assign_keys(found)}
            complete = (
                all(key in keys for key in wanted) if wanted else len(found) >= 2
            )
            if complete:
                return interception, found
            if settle_deadline is None:
                settle_deadline = now + settle
            if now >= settle_deadline or now >= deadline:
                return interception, found
        elif now >= deadline:
            interception.destroy()
            raise RuntimeError(
                "No mouse device found. Please connect a mouse."
            )
        log.debug("Waiting for mice: %d found so far", len(found))
        time.sleep(interval)


class Router:
    """Routes strokes between the assigned mice, the OS cursor and the overlay."""

    def __init__(self, interception, overlay):
        self._interception = interception
        self._overlay = overlay
        self._mice: dict[int, MouseDevice] = {}  # routed devices (dual mode)
        self._blocked: dict[int, MouseDevice] = {}  # present but unassigned
        self._active: MouseDevice | None = None
        self._single: MouseDevice | None = None  # the device in single mode
        self._speed_factor = _SPEED_MULTIPLIERS.get(w.get_mouse_speed(), 1.0)
        self._last_active_time = 0.0
        # Control-path requests from other threads, applied on the router
        # thread between strokes.
        self._control_lock = threading.Lock()
        self._pending: tuple[list, list] | None = None
        self._refresh_cb = None
        # The user's own system-wide swap setting (left-handed users);
        # single-mode mirroring toggles relative to it.
        self._baseline_swap = bool(w.user32.GetSystemMetrics(w.SM_SWAPBUTTON))

    # -- public API (any thread) ------------------------------------------

    def configure(self, devices, blocked=()) -> None:
        """Request routing of `devices` (0-2) and blocking of `blocked`
        (present but unassigned mice); applied on the router thread within
        one loop iteration."""
        with self._control_lock:
            self._pending = (list(devices), list(blocked))

    def request_refresh(self, callback) -> None:
        """Ask the router thread to re-enumerate mice; callback(found) runs
        on the router thread with the enumerate_mice result."""
        with self._control_lock:
            self._refresh_cb = callback

    def set_mirrored(self, mouse: MouseDevice, value: bool) -> None:
        """Called from the settings-UI/hotkey threads; a bool flip is race-free."""
        with self._control_lock:
            mouse.is_mirrored = value
            if mouse is self._single:
                w.user32.SwapMouseButton(self._baseline_swap != value)
        log.info("%s button mirror: %s", mouse.label, "on" if value else "off")

    def restore_system_swap(self) -> None:
        """Put the system-wide button swap back the way we found it."""
        w.user32.SwapMouseButton(self._baseline_swap)

    def run(self, stop: threading.Event) -> None:
        """Process strokes until the stop event is set. Runs on the caller."""
        interception = self._interception
        try:
            while not stop.is_set():
                if self._pending is not None or self._refresh_cb is not None:
                    self._apply_control_requests()
                device_num = interception.await_input(500)
                if device_num is None:
                    continue
                stroke = interception.devices[device_num].receive()
                if stroke is None:
                    continue
                if device_num in self._blocked and isinstance(stroke, MouseStroke):
                    continue  # unassigned mice are disabled entirely
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
            for mouse in (*self._mice.values(), *self._blocked.values()):
                interception.devices[mouse.device_num].set_filter(
                    FilterMouseButtonFlag.FILTER_MOUSE_NONE
                )

    # -- control path (router thread) ---------------------------------------

    def _apply_control_requests(self) -> None:
        with self._control_lock:
            pending, self._pending = self._pending, None
            refresh_cb, self._refresh_cb = self._refresh_cb, None
        if refresh_cb is not None:
            try:
                refresh_cb(enumerate_mice(self._interception))
            except Exception:
                log.exception("Device refresh failed")
            # The callback may have queued a fresh configuration.
            with self._control_lock:
                if self._pending is not None:
                    pending, self._pending = self._pending, None
        if pending is not None:
            self._apply_assignment(*pending)

    def _apply_assignment(self, devices: list[MouseDevice],
                          blocked: list[MouseDevice]) -> None:
        for mouse in (*self._mice.values(), *self._blocked.values()):
            self._interception.devices[mouse.device_num].set_filter(
                FilterMouseButtonFlag.FILTER_MOUSE_NONE
            )
        self._mice = {}
        self._active = None
        with self._control_lock:
            self._single = None

        self._blocked = {mouse.device_num: mouse for mouse in blocked}
        for mouse in blocked:
            self._interception.devices[mouse.device_num].set_filter(_MOUSE_FILTER)
            log.info("Blocking unassigned mouse: %s", mouse.label)

        if len(devices) == 2:
            w.user32.SwapMouseButton(self._baseline_swap)
            x, y = w.get_cursor_pos()
            for mouse in devices:
                mouse.x, mouse.y = float(x), float(y)
                mouse.buttons_down.clear()
                self._interception.devices[mouse.device_num].set_filter(
                    _MOUSE_FILTER
                )
            self._mice = {mouse.device_num: mouse for mouse in devices}
            self._active = devices[0]
            self._speed_factor = _SPEED_MULTIPLIERS.get(w.get_mouse_speed(), 1.0)
            self._last_active_time = 0.0
            self._overlay.show_at(x, y)
            log.info(
                "Dual-mouse mode: %s + %s", devices[0].label, devices[1].label
            )
        else:
            self._overlay.hide()
            single = devices[0] if devices else None
            with self._control_lock:
                self._single = single
            w.user32.SwapMouseButton(
                self._baseline_swap != (single.is_mirrored if single else False)
            )
            if single is not None:
                log.info(
                    "Single-mouse mode: %s (input passes through untouched)",
                    single.label,
                )
            else:
                log.info("Idle mode: no mouse assigned")

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
                    mouse.label, stroke.button_flags, stroke.button_data,
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
            "Switched to %s at (%d, %d)", mouse.label, int(mouse.x), int(mouse.y)
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
