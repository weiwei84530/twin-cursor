"""Global hotkeys via RegisterHotKey on a dedicated message-loop thread.

RegisterHotKey binds a hotkey to the registering thread's message queue, so
every (un)registration is marshalled onto the hotkey thread with thread
messages. Public methods are safe to call from any thread. A hotkey is a
dict with "mods" (MOD_* flags) and "vk" (virtual-key code).
"""

import ctypes
import logging
import threading
from ctypes import wintypes

from . import winapi as w

log = logging.getLogger(__name__)

_WM_APP_APPLY = w.WM_APP + 1


class HotkeyManager:
    """Owns the hotkey thread and the registered hotkey set."""

    def __init__(self, on_hotkey):
        self._on_hotkey = on_hotkey  # on_hotkey(hotkey_id), hotkey thread
        self._lock = threading.Lock()
        self._pending: dict = {}  # hotkey_id -> hotkey dict or None
        self._registered: set = set()
        self._thread_id = None
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="hotkeys", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5)

    # -- public API (any thread) ------------------------------------------

    def set_hotkey(self, hotkey_id: int, hotkey) -> None:
        """Register (or replace) a hotkey; None unregisters it."""
        with self._lock:
            self._pending[hotkey_id] = dict(hotkey) if hotkey else None
        self._post(_WM_APP_APPLY)

    def stop(self) -> None:
        self._post(w.WM_QUIT)
        if self._thread.is_alive():
            self._thread.join(timeout=3)

    def _post(self, message: int) -> None:
        if self._thread_id is not None:
            w.user32.PostThreadMessageW(self._thread_id, message, 0, 0)

    # -- hotkey thread ------------------------------------------------------

    def _run(self) -> None:
        msg = wintypes.MSG()
        # Force the message queue into existence before publishing the
        # thread id, so early PostThreadMessage calls are not lost.
        w.user32.PeekMessageW(
            ctypes.byref(msg), None, w.WM_USER, w.WM_USER, w.PM_NOREMOVE
        )
        self._thread_id = w.kernel32.GetCurrentThreadId()
        self._ready.set()

        while True:
            result = w.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:  # WM_QUIT or error
                break
            if msg.message == w.WM_HOTKEY:
                try:
                    self._on_hotkey(int(msg.wParam))
                except Exception:
                    log.exception("Hotkey callback failed")
            elif msg.message == _WM_APP_APPLY:
                self._apply_pending()

        for hotkey_id in self._registered:
            w.user32.UnregisterHotKey(None, hotkey_id)
        self._registered.clear()

    def _apply_pending(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
        for hotkey_id, hotkey in pending.items():
            if hotkey_id in self._registered:
                w.user32.UnregisterHotKey(None, hotkey_id)
                self._registered.discard(hotkey_id)
            if not hotkey:
                continue
            if w.user32.RegisterHotKey(
                None, hotkey_id, hotkey["mods"] | w.MOD_NOREPEAT, hotkey["vk"]
            ):
                self._registered.add(hotkey_id)
                log.debug("Registered hotkey %d: %s", hotkey_id, hotkey)
            else:
                log.warning(
                    "Could not register hotkey %s (already in use by "
                    "another application?)", hotkey.get("label", hotkey),
                )
