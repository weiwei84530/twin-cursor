"""Ghost-cursor overlay: a tiny per-pixel-alpha layered window.

The window is only as large as the cursor bitmap and is moved around with
SetWindowPos, which avoids every problem the old full-screen pygame overlay
had (ghost windows from an unpumped message queue, DWM/MPO interference,
DPI stretching). It lives on its own thread with a standard message pump so
it is always responsive.

The bitmap is the user's actual arrow cursor (theme, accessibility size and
per-monitor DPI all respected), with the bundled cursor.png as a fallback,
optionally recoloured with the slot colour of the mouse the ghost currently
stands for.
"""

import ctypes
import logging
import threading
from ctypes import wintypes

from . import cursor_render
from . import resource_path
from . import winapi as w

log = logging.getLogger(__name__)

_CLASS_NAME = "TwinCursorOverlay"
_WM_APP_MOVE = w.WM_APP + 1
_WM_APP_QUIT = w.WM_APP + 2
_WM_APP_SHOW = w.WM_APP + 3
_WM_APP_HIDE = w.WM_APP + 4
_WM_APP_COLOR = w.WM_APP + 5


class _CursorBitmap:
    """A premultiplied BGRA DIB of the arrow cursor for one DPI and colour."""

    def __init__(self, dpi: int, color):
        self.dpi = dpi
        self.color = color
        self.size = max(8, round(cursor_render.read_cursor_base_size() * dpi / 96.0))
        self.hotspot = (0, 0)
        self.hdc = None
        self._hbitmap = None
        self._render()

    def _render(self) -> None:
        size = self.size
        pixels = None
        hcursor, shared = cursor_render.load_arrow(size)
        if hcursor:
            self.hotspot = cursor_render.get_hotspot(hcursor)
            pixels = cursor_render.render_premultiplied(hcursor, size)
            if not shared:
                w.user32.DestroyCursor(hcursor)
        if pixels is None:
            log.debug("System cursor unavailable, falling back to cursor.png")
            self.hotspot = (0, 0)
            pixels = _render_fallback_png(size)
        if self.color is not None:
            cursor_render.tint(pixels, self.color)

        hdc, hbitmap, bits = cursor_render.create_dib(size)
        ctypes.memmove(bits, bytes(pixels), size * size * 4)
        w.gdi32.GdiFlush()
        self.hdc = hdc
        self._hbitmap = hbitmap

    def destroy(self) -> None:
        if self.hdc:
            w.gdi32.DeleteDC(self.hdc)
            self.hdc = None
        if self._hbitmap:
            w.gdi32.DeleteObject(self._hbitmap)
            self._hbitmap = None


def _render_fallback_png(size: int):
    """Render the bundled cursor.png as premultiplied BGRA at the given size."""
    from PIL import Image

    image = Image.open(resource_path("cursor.png")).convert("RGBA")
    scale = size / (
        cursor_render.read_cursor_base_size() or cursor_render.BASE_CURSOR_SIZE
    )
    target = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    image = image.resize(target, Image.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(image, (0, 0))
    rgba = canvas.tobytes()

    out = bytearray(size * size * 4)
    for i in range(0, len(rgba), 4):
        r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
        out[i] = b * a // 255
        out[i + 1] = g * a // 255
        out[i + 2] = r * a // 255
        out[i + 3] = a
    return out


class Overlay:
    """The ghost-cursor window. Public methods are safe to call from any thread."""

    def __init__(self):
        self._thread = None
        self._ready = threading.Event()
        self._hwnd = None
        self._wndproc_ref = None  # keep the callback alive for the window's lifetime
        self._position = (0, 0)  # latest requested ghost position (hotspot point)
        self._move_pending = False
        self._visible = False
        self._color = None  # colour applied on the window thread
        self._requested_color = None  # latest colour asked for by any thread
        self._bitmaps: dict[tuple, _CursorBitmap] = {}
        self._current: _CursorBitmap | None = None

    # -- public API (any thread) ------------------------------------------

    def start(self) -> None:
        """Create the (hidden) overlay window and its message pump."""
        self._thread = threading.Thread(
            target=self._run, name="overlay", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("Overlay window failed to start")

    def show_at(self, x: int, y: int) -> None:
        """Show the ghost cursor at the given position."""
        self._position = (x, y)
        if self._hwnd:
            w.user32.PostMessageW(self._hwnd, _WM_APP_SHOW, 0, 0)

    def hide(self) -> None:
        if self._hwnd:
            w.user32.PostMessageW(self._hwnd, _WM_APP_HIDE, 0, 0)

    def move_to(self, x: int, y: int) -> None:
        """Move the ghost cursor. High-frequency calls are coalesced."""
        self._position = (x, y)
        if self._hwnd and not self._move_pending:
            self._move_pending = True
            w.user32.PostMessageW(self._hwnd, _WM_APP_MOVE, 0, 0)

    def set_color(self, color) -> None:
        """Recolour the ghost cursor ((r, g, b), or None for no tint).

        Called from the input hot path on every switch, so it does nothing
        but post a message when the colour actually changed; the bitmaps
        for each colour are cached on the window thread.
        """
        if color == self._requested_color:
            return
        self._requested_color = color
        if self._hwnd:
            w.user32.PostMessageW(self._hwnd, _WM_APP_COLOR, 0, 0)

    def stop(self) -> None:
        if self._hwnd:
            w.user32.PostMessageW(self._hwnd, _WM_APP_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    # -- window thread ------------------------------------------------------

    def _run(self) -> None:
        try:
            self._create_window()
        except Exception:
            log.exception("Failed to create overlay window")
            self._ready.set()
            return

        # The window stays hidden until show_at() is called (dual mode).
        self._ready.set()

        msg = wintypes.MSG()
        while True:
            result = w.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:  # WM_QUIT or error
                break
            w.user32.TranslateMessage(ctypes.byref(msg))
            w.user32.DispatchMessageW(ctypes.byref(msg))

        for bitmap in self._bitmaps.values():
            bitmap.destroy()
        self._bitmaps.clear()
        self._hwnd = None

    def _create_window(self) -> None:
        hinstance = w.kernel32.GetModuleHandleW(None)
        self._wndproc_ref = w.WNDPROC(self._wndproc)

        wndclass = w.WNDCLASSW()
        wndclass.lpfnWndProc = self._wndproc_ref
        wndclass.hInstance = hinstance
        wndclass.lpszClassName = _CLASS_NAME
        if not w.user32.RegisterClassW(ctypes.byref(wndclass)):
            raise ctypes.WinError(ctypes.get_last_error())

        x, y = self._position
        self._hwnd = w.user32.CreateWindowExW(
            w.WS_EX_LAYERED | w.WS_EX_TRANSPARENT | w.WS_EX_TOPMOST
            | w.WS_EX_TOOLWINDOW | w.WS_EX_NOACTIVATE,
            _CLASS_NAME, "TwinCursor", w.WS_POPUP,
            x, y, 1, 1, None, None, hinstance, None,
        )
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

    def _wndproc(self, hwnd, message, wparam, lparam):
        try:
            if message == _WM_APP_MOVE:
                self._move_pending = False
                self._apply_position()
                return 0
            if message == _WM_APP_SHOW:
                self._visible = True
                # Picks up a colour set before the window existed.
                self._color = self._requested_color
                self._apply_position(force_repaint=True)
                w.user32.ShowWindow(hwnd, w.SW_SHOWNOACTIVATE)
                return 0
            if message == _WM_APP_HIDE:
                self._visible = False
                w.user32.ShowWindow(hwnd, w.SW_HIDE)
                return 0
            if message == _WM_APP_COLOR:
                color = self._requested_color
                if color != self._color:
                    self._color = color
                    if self._visible:
                        self._apply_position(force_repaint=True)
                return 0
            if message == _WM_APP_QUIT:
                w.user32.DestroyWindow(hwnd)
                return 0
            if message in (w.WM_SETTINGCHANGE, w.WM_DISPLAYCHANGE):
                # Cursor theme, size or display layout changed: rebuild.
                for bitmap in self._bitmaps.values():
                    bitmap.destroy()
                self._bitmaps.clear()
                self._current = None
                if self._visible:
                    self._apply_position(force_repaint=True)
                return 0
            if message == w.WM_DESTROY:
                w.user32.PostQuitMessage(0)
                return 0
        except Exception:
            log.exception("Overlay wndproc error")
        return w.user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _apply_position(self, force_repaint: bool = False) -> None:
        x, y = self._position
        dpi = w.get_dpi_for_point(x, y)

        key = (dpi, self._color)
        bitmap = self._bitmaps.get(key)
        if bitmap is None:
            bitmap = _CursorBitmap(dpi, self._color)
            self._bitmaps[key] = bitmap

        window_x = x - bitmap.hotspot[0]
        window_y = y - bitmap.hotspot[1]

        if bitmap is not self._current or force_repaint:
            self._current = bitmap
            position = wintypes.POINT(window_x, window_y)
            size = wintypes.SIZE(bitmap.size, bitmap.size)
            origin = wintypes.POINT(0, 0)
            blend = w.BLENDFUNCTION(w.AC_SRC_OVER, 0, 255, w.AC_SRC_ALPHA)
            w.user32.UpdateLayeredWindow(
                self._hwnd, None, ctypes.byref(position), ctypes.byref(size),
                bitmap.hdc, ctypes.byref(origin), 0, ctypes.byref(blend),
                w.ULW_ALPHA,
            )
        else:
            w.user32.SetWindowPos(
                self._hwnd, w.HWND_TOPMOST, window_x, window_y, 0, 0,
                w.SWP_NOSIZE | w.SWP_NOACTIVATE,
            )
