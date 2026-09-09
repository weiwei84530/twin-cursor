"""System-wide cursor tinting: gives the real cursor the active mouse's colour.

The ghost cursor is ours to draw, but the real one belongs to Windows, so
the only way to colour it is to hand Windows replacement cursors with
SetSystemCursor. That is a system-wide, comparatively slow call, so it is
never made from the input hot path: the router only records the colour it
wants and this worker thread applies it.

Replacements are undone with SystemParametersInfo(SPI_SETCURSORS), which
reloads the user's own cursor scheme from the registry. If the process is
killed instead of exiting normally, the tinted cursors stay until the user
logs off or touches their pointer settings.
"""

import ctypes
import logging
import threading

from . import cursor_render
from . import winapi as w

log = logging.getLogger(__name__)

# The cursors that get tinted. Animated ones (wait, app-starting) are left
# alone: only their first frame could be reproduced.
_TINTED_CURSORS = (
    w.OCR_NORMAL, w.OCR_IBEAM, w.OCR_CROSS, w.OCR_UP, w.OCR_SIZENWSE,
    w.OCR_SIZENESW, w.OCR_SIZEWE, w.OCR_SIZENS, w.OCR_SIZEALL, w.OCR_NO,
    w.OCR_HAND,
)


class SystemCursorTint:
    """Applies a colour to the real (system) cursor. Safe from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._wanted: tuple[int, int, int] | None = None
        self._applied: tuple[int, int, int] | None = None
        self._wake = threading.Event()
        self._running = False
        self._thread: threading.Thread | None = None

    # -- public API (any thread) ------------------------------------------

    def start(self) -> None:
        """Snapshot the pristine cursors and start the worker thread.

        Must run before anything replaces a system cursor, so that later
        loads never pick up an already tinted copy.
        """
        cursor_render.snapshot_system_cursors(_TINTED_CURSORS)
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="system-cursor", daemon=True
        )
        self._thread.start()

    def set_color(self, color) -> None:
        """Request a colour (an (r, g, b) tuple, or None for no tint)."""
        with self._lock:
            if color == self._wanted:
                return
            self._wanted = color
        self._wake.set()

    def stop(self) -> None:
        """Restore the user's own cursors and stop the worker."""
        self._running = False
        self._wake.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        if self._applied is not None:
            self._restore()

    # -- worker thread ------------------------------------------------------

    def _run(self) -> None:
        while self._running:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                wanted = self._wanted
            if not self._running or wanted == self._applied:
                continue
            try:
                self._apply(wanted)
            except Exception:
                log.exception("Failed to apply the system cursor colour")
        if self._applied is not None:
            self._restore()

    def _apply(self, color) -> None:
        if color is None:
            self._restore()
            return
        size = cursor_render.system_cursor_size()
        replaced = 0
        for ocr_id in _TINTED_CURSORS:
            cursor = _build_tinted(ocr_id, size, color)
            if not cursor:
                continue
            # SetSystemCursor takes ownership of the handle either way.
            if w.user32.SetSystemCursor(cursor, ocr_id):
                replaced += 1
            else:
                w.user32.DestroyCursor(cursor)
        if not replaced:
            log.warning("Could not tint any system cursor")
            return
        self._applied = color
        log.debug("System cursor tinted #%02x%02x%02x (%d cursors)",
                  *color, replaced)

    def _restore(self) -> None:
        # Reloads the user's cursor scheme from the registry, undoing every
        # SetSystemCursor we did.
        w.user32.SystemParametersInfoW(w.SPI_SETCURSORS, 0, None, 0)
        self._applied = None
        log.debug("System cursors restored")


def _build_tinted(ocr_id: int, size: int, color):
    """Build a tinted copy of one system cursor, or None if it cannot be read."""
    source, shared = cursor_render.load_cursor(ocr_id, size)
    if not source:
        return None
    try:
        hotspot = cursor_render.get_hotspot(source)
        pixels = cursor_render.render_premultiplied(source, size)
    finally:
        if not shared:
            w.user32.DestroyCursor(source)
    if pixels is None:
        return None
    cursor_render.tint(pixels, color)
    return _create_cursor(cursor_render.unpremultiply(pixels), size, hotspot)


def _create_cursor(straight_bgra: bytearray, size: int, hotspot):
    """Turn straight-alpha BGRA pixels into an HCURSOR."""
    hdc, color_bitmap, bits = cursor_render.create_dib(size)
    # The colour bitmap carries the alpha channel, so the AND mask is left
    # fully zero (opaque); Windows blends with the alpha instead.
    stride = ((size + 15) // 16) * 2
    mask_bitmap = w.gdi32.CreateBitmap(
        size, size, 1, 1, ctypes.create_string_buffer(stride * size)
    )
    try:
        ctypes.memmove(bits, bytes(straight_bgra), size * size * 4)
        w.gdi32.GdiFlush()
        if not mask_bitmap:
            return None
        info = w.ICONINFO()
        info.fIcon = False
        info.xHotspot = hotspot[0]
        info.yHotspot = hotspot[1]
        info.hbmMask = mask_bitmap
        info.hbmColor = color_bitmap
        return w.user32.CreateIconIndirect(ctypes.byref(info)) or None
    finally:
        w.gdi32.DeleteDC(hdc)
        w.gdi32.DeleteObject(color_bitmap)
        if mask_bitmap:
            w.gdi32.DeleteObject(mask_bitmap)
