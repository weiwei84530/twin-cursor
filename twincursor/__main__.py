"""Application entry point.

Run with `python -m twincursor` (add --debug for verbose logging).

Thread layout:
- main thread:      Interception event loop (the input hot path)
- overlay thread:   ghost-cursor layered window and its message pump
- settings thread:  tkinter settings window
- tray thread:      pystray icon
"""

import ctypes
import logging
import sys
import threading

from . import settings
from . import winapi as w
from .overlay import Overlay
from .router import Router, connect
from .settings_ui import SettingsWindow
from .tray import Tray

log = logging.getLogger(__name__)

_MUTEX_NAME = "Local\\TwinCursor.SingleInstance"


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
        return 1

    try:
        interception, mice = connect()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    settings.apply(mice.values())

    overlay = Overlay()
    router = Router(interception, mice, overlay)

    stop = threading.Event()

    def on_toggle(mouse, value):
        router.set_mirrored(mouse, value)
        settings.save(mice.values())

    ui = SettingsWindow(mice.values(), on_toggle)
    tray = Tray(on_open_settings=ui.show, on_exit=stop.set)
    tray.start()

    try:
        overlay.start(*router.inactive_position)

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
        tray.stop()
        ui.stop()
        overlay.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
