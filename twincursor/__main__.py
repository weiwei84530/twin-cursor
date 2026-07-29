"""Application entry point.

Run with `python -m twincursor` (add --debug for verbose logging).

Thread layout:
- main thread:      Interception event loop and mode switching
- overlay thread:   ghost-cursor layered window and its message pump
- settings thread:  tkinter settings window
- tray thread:      pystray icon
- hotkey thread:    RegisterHotKey message loop
"""

import ctypes
import logging
import sys
import threading

from . import driver_setup
from . import settings
from . import winapi as w
from .device_names import get_display_names
from .hotkeys import HotkeyManager
from .overlay import Overlay
from .settings_ui import SettingsWindow
from .tray import Tray

# The vendored interception package opens a driver context at import time
# (inputs.py module level), so on a machine without the driver importing
# the router raises immediately. Defer that failure to main() so it can
# offer to install the driver instead of dying on import.
try:
    from .router import (
        DriverUnavailableError, MouseDevice, Router, assign_keys, connect,
    )
    _ROUTER_IMPORT_ERROR = None
except Exception as exc:
    _ROUTER_IMPORT_ERROR = exc
    DriverUnavailableError = MouseDevice = Router = None
    assign_keys = connect = None

log = logging.getLogger(__name__)

_MUTEX_NAME = "Local\\TwinCursor.SingleInstance"

# The first slot is the single-mouse slot, so a never-configured device
# dropped into it gets a swap hotkey by default; the second slot defaults
# to no hotkey.
_DEFAULT_HOTKEY_FIRST = {
    "mods": w.MOD_CONTROL | w.MOD_ALT, "vk": 0x4D, "label": "Ctrl+Alt+M"
}

_AUTO = object()  # slot marker: pick a device automatically


class App:
    """Owns the device list and slot assignment; bridges UI, hotkeys, router.

    All state is guarded by a re-entrant lock because callbacks arrive from
    the tkinter, hotkey and router threads.
    """

    def __init__(self, router: Router, hotkeys: HotkeyManager):
        self._router = router
        self._hotkeys = hotkeys
        self._lock = threading.RLock()
        self._mice: dict[str, MouseDevice] = {}  # settings key -> device
        self._assignment: list = [None, None]  # settings key or None per slot

    # -- device list --------------------------------------------------------

    def merge_devices(self, found) -> None:
        """Update the device list from an enumerate_mice result.

        Called at startup (main thread) and on refresh (router thread).
        Devices that disappeared are dropped; an assigned one empties its
        slot for the session (the stored selection is left untouched so the
        device is picked up again after a restart).
        """
        with self._lock:
            present = {key: (num, hwid) for key, num, hwid in assign_keys(found)}

            new_keys = [key for key in present if key not in self._mice]
            names = get_display_names(
                [present[key][1] for key in new_keys]
            ) if new_keys else {}
            for key in new_keys:
                num, hwid = present[key]
                device = MouseDevice(num, hwid, key, names.get(hwid, hwid))
                settings.apply([device])
                self._mice[key] = device
                log.info("Mouse found: %s (%s)", device.label, hwid)
            for key, (num, _hwid) in present.items():
                self._mice[key].device_num = num

            for key in list(self._mice):
                if key not in present:
                    log.info("Mouse removed: %s", self._mice[key].label)
                    if key in self._assignment:
                        self._assignment[self._assignment.index(key)] = None
                    del self._mice[key]

            self._ensure_unique_labels()

    def _ensure_unique_labels(self) -> None:
        seen: dict[str, int] = {}
        for device in self._mice.values():
            base = device.label.split(" #", 1)[0]
            seen[base] = seen.get(base, 0) + 1
            device.label = base if seen[base] == 1 else f"{base} #{seen[base]}"

    def resolve_initial_assignment(self) -> None:
        """Fill the slots from the stored selection (or automatically)."""
        with self._lock:
            stored = settings.load_selection() or {}
            slots: list = []
            used: set[str] = set()
            for name in ("a", "b"):
                if name not in stored:
                    slots.append(_AUTO)
                    continue
                key = stored[name]
                if key is not None and key in self._mice and key not in used:
                    used.add(key)
                    slots.append(key)
                else:
                    slots.append(None)  # explicit None, or device unplugged
            for index, key in enumerate(slots):
                if key is _AUTO:
                    key = next((k for k in self._mice if k not in used), None)
                    if key is not None:
                        used.add(key)
                    slots[index] = key
            # The first slot must always hold a device when one is
            # available (its dropdown has no "None" entry).
            if slots[0] is None:
                free = next((k for k in self._mice if k not in used), None)
                if free is not None:
                    slots[0] = free
            self._assignment = slots
            self._resolve_hotkey_defaults()
        self._apply_assignment()

    def _resolve_hotkey_defaults(self) -> None:
        for index, key in enumerate(self._assignment):
            if key is None:
                continue
            device = self._mice[key]
            if device.hotkey is settings.HOTKEY_UNSET:
                device.hotkey = (
                    dict(_DEFAULT_HOTKEY_FIRST) if index == 0 else None
                )

    def _apply_assignment(self) -> None:
        with self._lock:
            devices = [self._mice[key] for key in self._assignment if key]
            blocked = [
                device for key, device in self._mice.items()
                if key not in self._assignment
            ]
            hotkeys = []
            for key in self._assignment:
                device = self._mice.get(key) if key else None
                hotkeys.append(
                    device.hotkey
                    if device and isinstance(device.hotkey, dict) else None
                )
        self._router.configure(devices, blocked)
        for index, hotkey in enumerate(hotkeys):
            self._hotkeys.set_hotkey(index + 1, hotkey)

    def _slot_device(self, slot: int):
        with self._lock:
            if not 0 <= slot < len(self._assignment):
                return None
            key = self._assignment[slot]
            return self._mice.get(key) if key else None

    def _routed_snapshot(self):
        with self._lock:
            return [
                (key, self._mice[key].device_num)
                for key in self._assignment if key in self._mice
            ]

    # -- UI state (settings thread) -----------------------------------------

    def get_state(self) -> dict:
        with self._lock:
            devices = [(key, device.label) for key, device in self._mice.items()]
            slots = []
            for key in self._assignment:
                device = self._mice.get(key) if key else None
                slots.append({
                    "key": key,
                    "label": device.label if device else None,
                    "mirrored": bool(device.is_mirrored) if device else False,
                    "hotkey_label": (
                        device.hotkey["label"]
                        if device and isinstance(device.hotkey, dict) else None
                    ),
                    "hwid": device.hwid if device else "",
                })
            return {"devices": devices, "slots": slots}

    # -- callbacks (settings / hotkey / router threads) ---------------------

    def on_device_selected(self, slot: int, key) -> None:
        with self._lock:
            if key is not None and key not in self._mice:
                return
            if slot == 0 and key is None:
                return  # the first slot always holds a device
            other = 1 - slot
            if key is not None and self._assignment[other] == key:
                # Selecting the other slot's device swaps the two slots.
                displaced = self._assignment[slot]
                if other == 0 and displaced is None:
                    # The first slot may not end up empty; hand it another
                    # free device instead, or reject the change.
                    displaced = next(
                        (k for k in self._mice if k != key), None
                    )
                    if displaced is None:
                        return
                self._assignment[other] = displaced
            self._assignment[slot] = key
            self._resolve_hotkey_defaults()
            settings.save_selection(self._assignment)
            settings.save(self._mice.values())  # persist defaulted hotkeys
        self._apply_assignment()

    def on_mirror_toggle(self, slot: int, value: bool) -> None:
        device = self._slot_device(slot)
        if device is None:
            return
        self._router.set_mirrored(device, bool(value))
        with self._lock:
            settings.save(self._mice.values())

    def on_hotkey_change(self, slot: int, hotkey) -> None:
        device = self._slot_device(slot)
        if device is None:
            return
        with self._lock:
            device.hotkey = dict(hotkey) if hotkey else None
            settings.save(self._mice.values())
        self._hotkeys.set_hotkey(slot + 1, device.hotkey)

    def on_hotkey_fired(self, hotkey_id: int) -> None:
        device = self._slot_device(hotkey_id - 1)
        if device is None:
            return
        self._router.set_mirrored(device, not device.is_mirrored)
        with self._lock:
            settings.save(self._mice.values())

    def on_settings_shown(self) -> None:
        # Re-enumerate on the router thread so driver access never races
        # with the input loop; the UI picks up the result on its next poll.
        self._router.request_refresh(self._on_refresh_result)

    def _on_refresh_result(self, found) -> None:  # router thread
        before = self._routed_snapshot()
        self.merge_devices(found)
        if self._routed_snapshot() != before:
            self._apply_assignment()


def _setup_logging(debug: bool) -> None:
    if sys.stderr is None:  # running under pythonw.exe, no console
        logging.disable(logging.CRITICAL)
        return
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("PIL").setLevel(logging.INFO)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    _setup_logging("--debug" in argv)

    # Must happen before any window is created.
    w.set_process_dpi_awareness()

    # A second instance would receive the first instance's re-injected
    # strokes and create an input feedback storm, so refuse to start.
    mutex = w.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not mutex or ctypes.get_last_error() == w.ERROR_ALREADY_EXISTS:
        log.error("TwinCursor is already running.")
        w.user32.MessageBoxW(
            None,
            "TwinCursor is already running — look for its icon in the"
            " system tray.",
            "TwinCursor", w.MB_OK | w.MB_ICONINFORMATION,
        )
        return 1

    if _ROUTER_IMPORT_ERROR is not None:
        log.error("Interception driver unavailable: %s", _ROUTER_IMPORT_ERROR)
        driver_setup.handle_driver_failure()
        return 1

    stored_selection = settings.load_selection() or {}
    wanted = [key for key in stored_selection.values() if isinstance(key, str)]
    try:
        interception, found = connect(wanted_keys=wanted)
    except DriverUnavailableError as exc:
        log.error("%s", exc)
        driver_setup.handle_driver_failure()
        return 1
    except RuntimeError as exc:
        log.error("%s", exc)
        w.user32.MessageBoxW(
            None, str(exc), "TwinCursor", w.MB_OK | w.MB_ICONERROR
        )
        return 1

    overlay = Overlay()
    router = Router(interception, overlay)
    hotkeys = HotkeyManager(lambda hotkey_id: app.on_hotkey_fired(hotkey_id))
    app = App(router, hotkeys)
    app.merge_devices(found)
    app.resolve_initial_assignment()

    stop = threading.Event()
    ui = SettingsWindow(
        get_state=app.get_state,
        on_device_selected=app.on_device_selected,
        on_mirror_toggle=app.on_mirror_toggle,
        on_hotkey_change=app.on_hotkey_change,
        on_shown=app.on_settings_shown,
    )
    tray = Tray(on_open_settings=ui.show, on_exit=stop.set)
    tray.start()

    try:
        overlay.start()

        # The main thread forwards every mouse stroke; keep it responsive.
        w.kernel32.SetThreadPriority(
            w.kernel32.GetCurrentThread(), w.THREAD_PRIORITY_HIGHEST
        )
        log.info("TwinCursor running (Ctrl+C or tray Exit to quit)")
        router.run(stop)
    except KeyboardInterrupt:
        log.info("Interrupted, shutting down")
    finally:
        interception.destroy()
        router.restore_system_swap()
        hotkeys.stop()
        tray.stop()
        ui.stop()
        overlay.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
