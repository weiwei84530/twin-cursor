"""Ghost-cursor overlay: a tiny per-pixel-alpha layered window.

The window is only as large as the cursor bitmap and is moved around with
SetWindowPos, which avoids every problem the old full-screen pygame overlay
had (ghost windows from an unpumped message queue, DWM/MPO interference,
DPI stretching). It lives on its own thread with a standard message pump so
it is always responsive.

The bitmap is the user's actual arrow cursor (theme, accessibility size and
per-monitor DPI all respected), with the bundled cursor.png as a fallback.
"""

import ctypes
import logging
import os
import threading
import winreg
from ctypes import wintypes

from . import resource_path
from . import winapi as w

log = logging.getLogger(__name__)

_CLASS_NAME = "TwinCursorOverlay"
_WM_APP_MOVE = w.WM_APP + 1
_WM_APP_QUIT = w.WM_APP + 2
_WM_APP_SHOW = w.WM_APP + 3
_WM_APP_HIDE = w.WM_APP + 4

_BASE_CURSOR_SIZE = 32  # pixels at 96 DPI when no CursorBaseSize is set


class _CursorBitmap:
    """A premultiplied BGRA DIB of the arrow cursor for a specific DPI."""

    def __init__(self, dpi: int):
        self.dpi = dpi
        self.size = max(8, round(_read_cursor_base_size() * dpi / 96.0))
        self.hotspot = (0, 0)
        self.hdc = None
        self._hbitmap = None
        self._old_bitmap = None
        self._render()

    def _render(self) -> None:
        size = self.size
        hcursor, shared = _load_arrow_cursor(size)
        if hcursor:
            self.hotspot = _get_hotspot(hcursor)

        # Draw the cursor over a black and a white background and recover
        # per-pixel alpha from the difference. This works for both modern
        # alpha cursors and classic masked cursors, and the black-background
        # result is already premultiplied as UpdateLayeredWindow expects.
        black_dc, black_bmp, black_bits = _create_dib(size)
        white_dc, white_bmp, white_bits = _create_dib(size)
        byte_count = size * size * 4
        drew = False
        try:
            ctypes.memset(black_bits, 0x00, byte_count)
            ctypes.memset(white_bits, 0xFF, byte_count)
            if hcursor:
                drew = bool(
                    w.user32.DrawIconEx(
                        black_dc, 0, 0, hcursor, size, size, 0, None, w.DI_NORMAL
                    )
                ) and bool(
                    w.user32.DrawIconEx(
                        white_dc, 0, 0, hcursor, size, size, 0, None, w.DI_NORMAL
                    )
                )
            w.gdi32.GdiFlush()

            pixels = None
            if drew:
                pixels = _recover_alpha(
                    ctypes.string_at(black_bits, byte_count),
                    ctypes.string_at(white_bits, byte_count),
                )
            if pixels is None:
                log.debug("System cursor unavailable, falling back to cursor.png")
                self.hotspot = (0, 0)
                pixels = _render_fallback_png(size)

            ctypes.memmove(black_bits, bytes(pixels), byte_count)
            w.gdi32.GdiFlush()
        finally:
            w.gdi32.DeleteDC(white_dc)
            w.gdi32.DeleteObject(white_bmp)
            if hcursor and not shared:
                w.user32.DestroyCursor(hcursor)

        # The black DIB now holds the final image; keep it selected for ULW.
        self.hdc = black_dc
        self._hbitmap = black_bmp

    def destroy(self) -> None:
        if self.hdc:
            w.gdi32.DeleteDC(self.hdc)
            self.hdc = None
        if self._hbitmap:
            w.gdi32.DeleteObject(self._hbitmap)
            self._hbitmap = None


def _create_dib(size: int):
    """Create a top-down 32bpp DIB selected into a memory DC."""
    info = w.BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(w.BITMAPINFOHEADER)
    info.bmiHeader.biWidth = size
    info.bmiHeader.biHeight = -size
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = w.BI_RGB

    bits = ctypes.c_void_p()
    hdc = w.gdi32.CreateCompatibleDC(None)
    hbitmap = w.gdi32.CreateDIBSection(
        hdc, ctypes.byref(info), w.DIB_RGB_COLORS, ctypes.byref(bits), None, 0
    )
    if not hbitmap:
        w.gdi32.DeleteDC(hdc)
        raise OSError("CreateDIBSection failed")
    w.gdi32.SelectObject(hdc, hbitmap)
    return hdc, hbitmap, bits


def _read_cursor_base_size() -> int:
    """Read the user's cursor size (accessibility setting) in 96-DPI pixels."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as key:
            value, kind = winreg.QueryValueEx(key, "CursorBaseSize")
        if kind == winreg.REG_DWORD and int(value) >= 8:
            return int(value)
    except OSError:
        pass
    return _BASE_CURSOR_SIZE


def _load_arrow_cursor(size: int):
    """Load the user's arrow cursor at the given size.

    Returns (handle, is_shared). Tries the cursor file from the user's active
    scheme first, then the stock OEM arrow. Returns (None, False) on failure.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as key:
            path, _ = winreg.QueryValueEx(key, "Arrow")
        path = os.path.expandvars(path).strip()
    except OSError:
        path = ""

    if path:
        handle = w.user32.LoadImageW(
            None,
            ctypes.cast(ctypes.c_wchar_p(path), ctypes.c_void_p),
            w.IMAGE_CURSOR, size, size, w.LR_LOADFROMFILE,
        )
        if handle:
            return handle, False
        log.debug("LoadImage from %s failed, trying OEM arrow", path)

    handle = w.user32.LoadImageW(
        None, ctypes.c_void_p(w.OCR_NORMAL), w.IMAGE_CURSOR, size, size, w.LR_SHARED
    )
    return (handle, True) if handle else (None, False)


def _get_hotspot(hcursor) -> tuple[int, int]:
    info = w.ICONINFO()
    if not w.user32.GetIconInfo(hcursor, ctypes.byref(info)):
        return (0, 0)
    # GetIconInfo hands out copies of the bitmaps that we must free.
    if info.hbmMask:
        w.gdi32.DeleteObject(info.hbmMask)
    if info.hbmColor:
        w.gdi32.DeleteObject(info.hbmColor)
    return (int(info.xHotspot), int(info.yHotspot))


def _recover_alpha(black: bytes, white: bytes):
    """Combine black/white renders into premultiplied BGRA, or None if empty."""
    out = bytearray(len(black))
    opaque = False
    for i in range(0, len(black), 4):
        b, g, r = black[i], black[i + 1], black[i + 2]
        # Alpha from the white render: white_channel = color + (255 - a)
        a = 255 - min(
            max(white[i] - b, 0), max(white[i + 1] - g, 0), max(white[i + 2] - r, 0)
        )
        if a <= 0:
            continue
        opaque = True
        out[i] = min(b, a)
        out[i + 1] = min(g, a)
        out[i + 2] = min(r, a)
        out[i + 3] = a
    return out if opaque else None


def _render_fallback_png(size: int):
    """Render the bundled cursor.png as premultiplied BGRA at the given size."""
    from PIL import Image

    image = Image.open(resource_path("cursor.png")).convert("RGBA")
    scale = size / (_read_cursor_base_size() or _BASE_CURSOR_SIZE)
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
        self._bitmaps: dict[int, _CursorBitmap] = {}
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
                self._apply_position(force_repaint=True)
                w.user32.ShowWindow(hwnd, w.SW_SHOWNOACTIVATE)
                return 0
            if message == _WM_APP_HIDE:
                self._visible = False
                w.user32.ShowWindow(hwnd, w.SW_HIDE)
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

        bitmap = self._bitmaps.get(dpi)
        if bitmap is None:
            bitmap = _CursorBitmap(dpi)
            self._bitmaps[dpi] = bitmap

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
