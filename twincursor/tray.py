"""System tray icon.

The tray is deliberately minimal: left-clicking the icon opens the settings
window (the pystray default item), and the right-click menu only offers
"Open Settings" and "Exit". All configuration lives in the settings window.
"""

import logging
import threading

import pystray
from PIL import Image

from . import resource_path

log = logging.getLogger(__name__)


class Tray:
    def __init__(self, on_open_settings, on_exit):
        menu = pystray.Menu(
            pystray.MenuItem(
                "Open Settings",
                lambda icon, item: on_open_settings(),
                default=True,
            ),
            pystray.MenuItem("Exit", lambda icon, item: on_exit()),
        )
        self._icon = pystray.Icon(
            "TwinCursor",
            Image.open(resource_path("icon.png")),
            "TwinCursor",
            menu=menu,
        )
        self._thread = threading.Thread(
            target=self._icon.run, name="tray", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            log.debug("Tray icon already stopped")
        if self._thread.is_alive():
            self._thread.join(timeout=3)
