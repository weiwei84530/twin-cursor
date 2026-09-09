"""Cursor pixel rendering shared by the ghost overlay and the colour tint.

Windows hands out cursors as opaque HCURSOR handles and offers no way to
read their pixels back. Both the ghost overlay (which blits them into a
layered window) and the colour tint (which rebuilds them into new cursors)
need those pixels, so they are recovered here by drawing the cursor twice —
once over black, once over white — and deriving per-pixel alpha from the
difference. That works for modern alpha cursors and classic masked ones
alike, and the black-background result is already premultiplied, which is
what UpdateLayeredWindow wants.

Because the tint replaces the system cursors with SetSystemCursor, loading
OCR_NORMAL later would hand back our own tinted copy and tint it again.
`snapshot_system_cursors()` therefore takes private copies of the pristine
cursors once at startup, before anything is replaced, and `load_cursor()`
serves every later request from that snapshot.
"""

import ctypes
import logging
import os
import winreg

from . import winapi as w

log = logging.getLogger(__name__)

BASE_CURSOR_SIZE = 32  # pixels at 96 DPI when no CursorBaseSize is set

# Pristine copies of the system cursors, taken before the first tint.
_originals: dict[int, int] = {}


# -- system cursor access -------------------------------------------------

def snapshot_system_cursors(ocr_ids) -> None:
    """Take private copies of the given system cursors (call once, early)."""
    for ocr_id in ocr_ids:
        if ocr_id in _originals:
            continue
        shared = w.user32.LoadImageW(
            None, ctypes.c_void_p(ocr_id), w.IMAGE_CURSOR, 0, 0,
            w.LR_SHARED | w.LR_DEFAULTSIZE,
        )
        if not shared:
            continue
        copy = w.user32.CopyImage(
            shared, w.IMAGE_CURSOR, 0, 0, w.LR_COPYFROMRESOURCE
        ) or w.user32.CopyImage(shared, w.IMAGE_CURSOR, 0, 0, 0)
        if copy:
            _originals[ocr_id] = copy
    log.debug("Snapshotted %d system cursors", len(_originals))


def load_cursor(ocr_id: int, size: int):
    """Load a system cursor at `size`, from the pristine snapshot if there
    is one. Returns (handle, is_shared); the caller destroys non-shared
    handles. Returns (None, False) on failure."""
    original = _originals.get(ocr_id)
    if original:
        # LR_COPYFROMRESOURCE re-reads the original resource at the wanted
        # size instead of stretching the snapshot.
        handle = w.user32.CopyImage(
            original, w.IMAGE_CURSOR, size, size, w.LR_COPYFROMRESOURCE
        ) or w.user32.CopyImage(original, w.IMAGE_CURSOR, size, size, 0)
        if handle:
            return handle, False
    handle = w.user32.LoadImageW(
        None, ctypes.c_void_p(ocr_id), w.IMAGE_CURSOR, size, size, w.LR_SHARED
    )
    return (handle, True) if handle else (None, False)


def load_arrow(size: int):
    """Load the user's arrow cursor at the given size.

    Returns (handle, is_shared). The cursor file from the user's active
    scheme comes first (it is never affected by our own tinting), then the
    stock OEM arrow. Returns (None, False) on failure.
    """
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors"
        ) as key:
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

    return load_cursor(w.OCR_NORMAL, size)


def read_cursor_base_size() -> int:
    """Read the user's cursor size (accessibility setting) in 96-DPI pixels."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors"
        ) as key:
            value, kind = winreg.QueryValueEx(key, "CursorBaseSize")
        if kind == winreg.REG_DWORD and int(value) >= 8:
            return int(value)
    except OSError:
        pass
    return BASE_CURSOR_SIZE


def system_cursor_size() -> int:
    """The size Windows uses for system cursors on the primary monitor."""
    metric = w.user32.GetSystemMetrics(w.SM_CXCURSOR)
    return metric if metric >= 8 else read_cursor_base_size()


def get_hotspot(hcursor) -> tuple[int, int]:
    info = w.ICONINFO()
    if not w.user32.GetIconInfo(hcursor, ctypes.byref(info)):
        return (0, 0)
    # GetIconInfo hands out copies of the bitmaps that we must free.
    if info.hbmMask:
        w.gdi32.DeleteObject(info.hbmMask)
    if info.hbmColor:
        w.gdi32.DeleteObject(info.hbmColor)
    return (int(info.xHotspot), int(info.yHotspot))


# -- pixel plumbing -------------------------------------------------------

def create_dib(size: int):
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


def render_premultiplied(hcursor, size: int):
    """Return the cursor as premultiplied BGRA, or None if it came out empty."""
    black_dc, black_bmp, black_bits = create_dib(size)
    white_dc, white_bmp, white_bits = create_dib(size)
    byte_count = size * size * 4
    try:
        ctypes.memset(black_bits, 0x00, byte_count)
        ctypes.memset(white_bits, 0xFF, byte_count)
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
        if not drew:
            return None
        return _recover_alpha(
            ctypes.string_at(black_bits, byte_count),
            ctypes.string_at(white_bits, byte_count),
        )
    finally:
        w.gdi32.DeleteDC(black_dc)
        w.gdi32.DeleteObject(black_bmp)
        w.gdi32.DeleteDC(white_dc)
        w.gdi32.DeleteObject(white_bmp)


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


def tint(pixels: bytearray, color: tuple[int, int, int]) -> None:
    """Recolour premultiplied BGRA pixels in place with an RGB colour.

    Every pixel keeps its brightness but takes the new hue, so the white
    body of the arrow becomes the colour while the dark outline stays dark
    and the cursor keeps its familiar shape. Premultiplied values can be
    scaled directly: the alpha factor survives the multiplication.
    """
    red, green, blue = color
    for i in range(0, len(pixels), 4):
        alpha = pixels[i + 3]
        if not alpha:
            continue
        luma = (
            pixels[i] * 114 + pixels[i + 1] * 587 + pixels[i + 2] * 299
        ) // 1000
        pixels[i] = min(blue * luma // 255, alpha)
        pixels[i + 1] = min(green * luma // 255, alpha)
        pixels[i + 2] = min(red * luma // 255, alpha)


def unpremultiply(pixels: bytearray) -> bytearray:
    """Convert premultiplied BGRA into the straight alpha icons expect."""
    out = bytearray(pixels)
    for i in range(0, len(out), 4):
        alpha = out[i + 3]
        if not alpha or alpha == 255:
            continue
        for channel in range(3):
            out[i + channel] = min(255, out[i + channel] * 255 // alpha)
    return out
