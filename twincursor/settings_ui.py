"""The settings window (tkinter/ttk).

tkinter requires its root and mainloop to live on the thread that created
them, so the window runs on a dedicated daemon thread and other threads
request actions through flags polled by an `after` loop. Closing the window
only hides it; the application keeps running in the tray.

The window shows two slots (Mouse A / Mouse B), each with a device
dropdown, a mirror-buttons checkbox and a hotkey recorder. State is pulled
from the application through `get_state` on every poll tick, so changes
made from other threads (hotkey toggles, device hot-plug) show up without
any push mechanism.
"""

import gc
import logging
import os
import sys
import threading
from pathlib import Path

from . import resource_path
from . import winapi as w

log = logging.getLogger(__name__)

_POLL_INTERVAL_MS = 150
_SLOT_TITLES = ("First Mouse", "Second Mouse")
_NONE_LABEL = "None"
# Sizes offered to Windows for the title-bar/taskbar icon. The source
# image is large, so pre-scaling with a proper filter avoids the blocky
# nearest-neighbour artifacts Tk produces when it scales down itself.
_ICON_SIZES = (16, 20, 24, 32, 48, 64)
_RECORDING_TEXT = "Press keys… (Esc = none)"

_MODIFIER_KEYSYMS = {
    "Control_L": w.MOD_CONTROL, "Control_R": w.MOD_CONTROL,
    "Shift_L": w.MOD_SHIFT, "Shift_R": w.MOD_SHIFT,
    "Alt_L": w.MOD_ALT, "Alt_R": w.MOD_ALT,
    "Win_L": w.MOD_WIN, "Win_R": w.MOD_WIN,
}
_MOD_ORDER = (
    (w.MOD_CONTROL, "Ctrl"), (w.MOD_SHIFT, "Shift"),
    (w.MOD_ALT, "Alt"), (w.MOD_WIN, "Win"),
)

_KEYSYM_LABELS = {
    "Return": "Enter", "Prior": "PageUp", "Next": "PageDown",
    "space": "Space", "Delete": "Del", "Insert": "Ins",
    "BackSpace": "Backspace",
}


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

    def __init__(self, get_state, on_device_selected, on_mirror_toggle,
                 on_hotkey_change, on_shown):
        self._get_state = get_state
        self._on_device_selected = on_device_selected
        self._on_mirror_toggle = on_mirror_toggle
        self._on_hotkey_change = on_hotkey_change
        self._on_shown = on_shown
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
                try:
                    self._on_shown()
                except Exception:
                    log.exception("on_shown callback failed")
                self._present(root)
            self._refresh()
            root.after(_POLL_INTERVAL_MS, poll)

        root.withdraw()
        root.after(_POLL_INTERVAL_MS, poll)
        root.mainloop()
        try:
            root.destroy()
        except tk.TclError:
            pass
        # Drop every Tcl-backed object (PhotoImage, Variables, widgets) on
        # this thread. Left to the garbage collector, their finalizers would
        # run on the main thread at interpreter shutdown and crash Tcl
        # ("Tcl_AsyncDelete: async handler deleted by the wrong thread").
        self._slots = None
        self._icon_images = None
        del root
        gc.collect()

    def _build(self, root: tk.Tk) -> None:
        root.title("TwinCursor")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        self._icon_images = None
        try:
            from PIL import Image, ImageTk

            source = Image.open(resource_path("icon.png")).convert("RGBA")
            side = max(source.size)
            square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            square.paste(
                source,
                ((side - source.width) // 2, (side - source.height) // 2),
            )
            # Keep references so tkinter does not garbage-collect the images.
            self._icon_images = [
                ImageTk.PhotoImage(square.resize((s, s), Image.LANCZOS))
                for s in _ICON_SIZES
            ]
            root.iconphoto(True, *self._icon_images)
        except Exception:
            log.debug("Could not load icon.png for the settings window")

        self._recording: int | None = None  # slot index while capturing keys
        self._held_mods = 0
        self._last_state = None
        self._slots: list[dict] = []

        frame = ttk.Frame(root, padding=12)
        frame.grid(sticky="nsew")

        for slot in range(2):
            box = ttk.LabelFrame(frame, text=_SLOT_TITLES[slot], padding=(10, 6))
            box.grid(row=slot, column=0, sticky="ew",
                     pady=(0 if slot == 0 else 8, 0))
            box.columnconfigure(1, weight=1)

            ttk.Label(box, text="Device:").grid(row=0, column=0, sticky="w")
            device_var = tk.StringVar(value=_NONE_LABEL)
            combo = ttk.Combobox(
                box, textvariable=device_var, state="readonly", width=36
            )
            combo.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(6, 0))
            combo.bind(
                "<<ComboboxSelected>>",
                lambda _e, s=slot: self._device_selected(s),
            )

            mirror_var = tk.BooleanVar(value=False)
            check = ttk.Checkbutton(
                box,
                text="Mirror buttons (swap left / right)",
                variable=mirror_var,
                command=lambda s=slot: self._mirror_toggled(s),
            )
            check.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

            hotkey_button = ttk.Button(
                box, text=f"Hotkey: {_NONE_LABEL.lower()}", width=24,
                command=lambda s=slot: self._start_recording(s),
            )
            hotkey_button.grid(row=1, column=2, sticky="e", pady=(8, 0))

            hwid_label = ttk.Label(box, text="", foreground="gray")
            hwid_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

            self._slots.append({
                "combo": combo,
                "device_var": device_var,
                "mirror_var": mirror_var,
                "check": check,
                "hotkey_button": hotkey_button,
                "hwid_label": hwid_label,
                "keys": [],  # device keys parallel to the dropdown entries
            })

        self._refresh(force=True)

    # -- state sync ---------------------------------------------------------

    def _refresh(self, force: bool = False) -> None:
        if self._recording is not None:
            return  # do not fight the user while a hotkey is being captured
        try:
            state = self._get_state()
        except Exception:
            log.exception("Failed to read application state")
            return
        if not force and state == self._last_state:
            return
        self._last_state = state

        keys = [key for key, _ in state["devices"]]
        labels = [label for _, label in state["devices"]]
        for slot, slot_state in enumerate(state["slots"]):
            widgets = self._slots[slot]
            widgets["keys"] = keys
            # The first slot always holds a device, so no "None" entry.
            widgets["combo"]["values"] = (
                labels if slot == 0 else [_NONE_LABEL] + labels
            )
            widgets["device_var"].set(
                slot_state["label"] if slot_state["key"] else _NONE_LABEL
            )
            widgets["mirror_var"].set(slot_state["mirrored"])
            hotkey_label = slot_state["hotkey_label"] or _NONE_LABEL.lower()
            widgets["hotkey_button"].config(text=f"Hotkey: {hotkey_label}")
            widgets["hwid_label"].config(
                text=_shorten(slot_state["hwid"]) if slot_state["hwid"]
                else "No device assigned"
            )
            enabled = slot_state["key"] is not None
            for name in ("check", "hotkey_button"):
                widgets[name].state(["!disabled" if enabled else "disabled"])

    # -- widget callbacks ---------------------------------------------------

    def _device_selected(self, slot: int) -> None:
        widgets = self._slots[slot]
        index = widgets["combo"].current()
        if slot == 0:
            if not 0 <= index < len(widgets["keys"]):
                return
            key = widgets["keys"][index]
        else:
            key = None if index <= 0 else widgets["keys"][index - 1]
        self._last_state = None  # the other slot may change too; force resync
        try:
            self._on_device_selected(slot, key)
        except Exception:
            log.exception("Device selection failed")
        self._refresh(force=True)

    def _mirror_toggled(self, slot: int) -> None:
        try:
            self._on_mirror_toggle(slot, self._slots[slot]["mirror_var"].get())
        except Exception:
            log.exception("Mirror toggle failed")

    # -- hotkey recording ---------------------------------------------------

    def _start_recording(self, slot: int) -> None:
        if self._recording is not None:
            return
        self._recording = slot
        self._held_mods = 0
        button = self._slots[slot]["hotkey_button"]
        button.config(text=_RECORDING_TEXT)
        button.focus_set()
        button.bind("<KeyPress>", self._record_key_press)
        button.bind("<KeyRelease>", self._record_key_release)
        button.bind("<FocusOut>", self._record_cancelled)

    def _record_key_press(self, event):
        keysym = event.keysym
        if keysym in _MODIFIER_KEYSYMS:
            self._held_mods |= _MODIFIER_KEYSYMS[keysym]
            return "break"
        if keysym == "Escape":
            self._finish_recording(None)
            return "break"
        mods = self._held_mods
        parts = [name for flag, name in _MOD_ORDER if mods & flag]
        parts.append(_pretty_keysym(keysym))
        # On Windows, tkinter's keycode is the Win32 virtual-key code.
        self._finish_recording({
            "mods": mods, "vk": int(event.keycode), "label": "+".join(parts),
        })
        return "break"

    def _record_key_release(self, event):
        if event.keysym in _MODIFIER_KEYSYMS:
            self._held_mods &= ~_MODIFIER_KEYSYMS[event.keysym]
        return "break"

    def _record_cancelled(self, _event) -> None:
        if self._recording is None:
            return
        self._end_recording()
        self._last_state = None  # let the next poll restore the button text

    def _finish_recording(self, hotkey) -> None:
        slot = self._recording
        self._end_recording()
        self._last_state = None
        try:
            self._on_hotkey_change(slot, hotkey)
        except Exception:
            log.exception("Hotkey change failed")
        self._refresh(force=True)

    def _end_recording(self) -> None:
        slot, self._recording = self._recording, None
        button = self._slots[slot]["hotkey_button"]
        for sequence in ("<KeyPress>", "<KeyRelease>", "<FocusOut>"):
            button.unbind(sequence)

    # -- window management --------------------------------------------------

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


def _pretty_keysym(keysym: str) -> str:
    if len(keysym) == 1:
        return keysym.upper()
    return _KEYSYM_LABELS.get(keysym, keysym)


def _shorten(hwid: str, limit: int = 46) -> str:
    return hwid if len(hwid) <= limit else hwid[: limit - 1] + "…"
