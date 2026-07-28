"""The settings window (tkinter/ttk).

tkinter requires its root and mainloop to live on the thread that created
them, so the window runs on a dedicated daemon thread and other threads
request actions through flags polled by an `after` loop. Closing the window
only hides it; the application keeps running in the tray.
"""

import logging
import os
import sys
import threading
from pathlib import Path

from . import resource_path
from . import winapi as w

log = logging.getLogger(__name__)

_POLL_INTERVAL_MS = 150


def _ensure_tcl_env() -> None:
    """Point Tcl/Tk at the base interpreter's runtime files.

    Virtual environments created by uv do not copy the Tcl runtime and, on
    Windows, tkinter then fails with "Can't find a usable init.tcl". The
    files live under <base_prefix>\\tcl in a standard CPython install.
    """
    tcl_root = Path(sys.base_prefix) / "tcl"
    if not tcl_root.is_dir():
        return
    for marker, variable in (("init.tcl", "TCL_LIBRARY"), ("tk.tcl", "TK_LIBRARY")):
        if os.environ.get(variable):
            continue
        for candidate in sorted(tcl_root.iterdir()):
            if candidate.is_dir() and (candidate / marker).is_file():
                os.environ[variable] = str(candidate)
                break


_ensure_tcl_env()

import tkinter as tk  # noqa: E402  (needs the Tcl environment set up first)
from tkinter import ttk  # noqa: E402


class SettingsWindow:
    """Owns the tkinter thread. Public methods are safe from any thread."""

    def __init__(self, mice, on_toggle):
        self._mice = list(mice)
        self._on_toggle = on_toggle
        self._show_requested = threading.Event()
        self._stop_requested = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="settings-ui", daemon=True
        )
        self._thread.start()

    # -- public API (any thread) ------------------------------------------

    def show(self) -> None:
        self._show_requested.set()

    def stop(self) -> None:
        self._stop_requested.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3)

    # -- tkinter thread -----------------------------------------------------

    def _run(self) -> None:
        # System-DPI awareness keeps tkinter crisp on the primary monitor and
        # lets DWM scale it elsewhere; tkinter cannot handle per-monitor DPI.
        w.set_thread_dpi_awareness(w.DPI_AWARENESS_CONTEXT_SYSTEM_AWARE)
        try:
            root = tk.Tk()
            self._build(root)
        except Exception:
            log.exception("Failed to create the settings window")
            return

        def poll():
            if self._stop_requested.is_set():
                root.quit()
                return
            if self._show_requested.is_set():
                self._show_requested.clear()
                self._present(root)
            root.after(_POLL_INTERVAL_MS, poll)

        root.withdraw()
        root.after(_POLL_INTERVAL_MS, poll)
        root.mainloop()
        try:
            root.destroy()
        except tk.TclError:
            pass

    def _build(self, root: tk.Tk) -> None:
        root.title("TwinCursor")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        try:
            # Keep a reference so tkinter does not garbage-collect the image.
            self._icon_image = tk.PhotoImage(file=resource_path("icon.png"))
            root.iconphoto(True, self._icon_image)
        except tk.TclError:
            log.debug("Could not load icon.png for the settings window")

        frame = ttk.Frame(root, padding=12)
        frame.grid(sticky="nsew")

        for row, mouse in enumerate(self._mice):
            box = ttk.LabelFrame(frame, text=mouse.name, padding=(10, 6))
            box.grid(row=row, column=0, sticky="ew", pady=(0 if row == 0 else 8, 0))
            box.columnconfigure(0, weight=1)

            variable = tk.BooleanVar(value=mouse.is_mirrored)
            check = ttk.Checkbutton(
                box,
                text="Mirror buttons (swap left / right)",
                variable=variable,
                command=lambda m=mouse, v=variable: self._on_toggle(m, v.get()),
            )
            check.grid(row=0, column=0, sticky="w")

            hwid = ttk.Label(
                box, text=_shorten(mouse.hwid), foreground="gray"
            )
            hwid.grid(row=1, column=0, sticky="w", pady=(4, 0))

    @staticmethod
    def _present(root: tk.Tk) -> None:
        if not root.winfo_viewable():
            root.update_idletasks()
            x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
            y = (root.winfo_screenheight() - root.winfo_reqheight()) // 2
            root.geometry(f"+{x}+{y}")
        root.deiconify()
        root.lift()
        # Briefly toggling topmost reliably brings the window to the front
        # even though our process is not the foreground process.
        root.attributes("-topmost", True)
        root.attributes("-topmost", False)
        root.focus_force()


def _shorten(hwid: str, limit: int = 46) -> str:
    return hwid if len(hwid) <= limit else hwid[: limit - 1] + "…"
