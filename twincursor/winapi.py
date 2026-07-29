"""Centralized ctypes definitions for the Win32 APIs used by TwinCursor.

Keeping every constant, structure and function prototype in one module
avoids scattered, subtly-different ctypes declarations. All prototypes set
explicit argtypes/restype so handles and pointers survive 64-bit truncation.
"""

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
except OSError:  # pre-Windows 8.1
    shcore = None

LRESULT = ctypes.c_ssize_t

# DPI awareness contexts (pseudo handles)
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = ctypes.c_void_p(-2)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
MDT_EFFECTIVE_DPI = 0

# GetSystemMetrics indices
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

# Window styles
WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

# SetWindowPos
HWND_TOPMOST = ctypes.c_void_p(-1)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

# Window messages
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_SETTINGCHANGE = 0x001A
WM_DISPLAYCHANGE = 0x007E
WM_HOTKEY = 0x0312
WM_USER = 0x0400
WM_APP = 0x8000

# PeekMessage
PM_NOREMOVE = 0x0000

# RegisterHotKey modifiers
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# LoadImage
IMAGE_CURSOR = 2
LR_LOADFROMFILE = 0x0010
LR_SHARED = 0x8000
OCR_NORMAL = 32512

# DrawIconEx
DI_NORMAL = 0x0003

# UpdateLayeredWindow
ULW_ALPHA = 0x0002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

# DIB
BI_RGB = 0
DIB_RGB_COLORS = 0

# Monitors
MONITOR_DEFAULTTONEAREST = 2

# SystemParametersInfo
SPI_GETMOUSESPEED = 0x0070

# GetSystemMetrics
SM_SWAPBUTTON = 23

# Errors / misc
ERROR_ALREADY_EXISTS = 183
THREAD_PRIORITY_HIGHEST = 2
INFINITE = 0xFFFFFFFF

# MessageBox
MB_OK = 0x00000000
MB_YESNO = 0x00000004
MB_ICONERROR = 0x00000010
MB_ICONQUESTION = 0x00000020
MB_ICONWARNING = 0x00000030
MB_ICONINFORMATION = 0x00000040
IDYES = 6

# ShellExecuteEx
SEE_MASK_NOCLOSEPROCESS = 0x00000040


# --- Structures ---------------------------------------------------------

class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", wintypes.LPVOID),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


# --- Function prototypes ------------------------------------------------

user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL

user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.DefWindowProcW.restype = LRESULT

user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
]
user32.GetMessageW.restype = ctypes.c_int

user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL

user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT

user32.PostMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.PostMessageW.restype = wintypes.BOOL

user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype = None

user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

user32.SetWindowPos.argtypes = [
    wintypes.HWND, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL

user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
    ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
]
user32.UpdateLayeredWindow.restype = wintypes.BOOL

# lpszName may be either a resource ordinal (int) or a file path (wide str),
# so it is declared as a raw pointer-sized value.
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE, ctypes.c_void_p, wintypes.UINT,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.LoadImageW.restype = wintypes.HANDLE

user32.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wintypes.BOOL

user32.DrawIconEx.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HANDLE,
    ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH, wintypes.UINT,
]
user32.DrawIconEx.restype = wintypes.BOOL

user32.DestroyCursor.argtypes = [wintypes.HANDLE]
user32.DestroyCursor.restype = wintypes.BOOL

user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HMONITOR

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC

user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int

user32.SystemParametersInfoW.argtypes = [
    wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT
]
user32.SystemParametersInfoW.restype = wintypes.BOOL

user32.SwapMouseButton.argtypes = [wintypes.BOOL]
user32.SwapMouseButton.restype = wintypes.BOOL

user32.RegisterHotKey.argtypes = [
    wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
]
user32.RegisterHotKey.restype = wintypes.BOOL

user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL

user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND,
    wintypes.UINT, wintypes.UINT, wintypes.UINT,
]
user32.PeekMessageW.restype = wintypes.BOOL

user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.PostThreadMessageW.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC

gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP

gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ

gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL

gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL

gdi32.GdiFlush.argtypes = []
gdi32.GdiFlush.restype = wintypes.BOOL

# Available on Windows 10 1607+; guarded because older systems lack it.
try:
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
except AttributeError:
    pass

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE

kernel32.GetCurrentThread.argtypes = []
kernel32.GetCurrentThread.restype = wintypes.HANDLE

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

kernel32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
kernel32.SetThreadPriority.restype = wintypes.BOOL

kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD

kernel32.GetExitCodeProcess.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)
]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

user32.MessageBoxW.argtypes = [
    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT
]
user32.MessageBoxW.restype = ctypes.c_int

shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
shell32.ShellExecuteExW.restype = wintypes.BOOL

shell32.ShellExecuteW.argtypes = [
    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
]
shell32.ShellExecuteW.restype = wintypes.HINSTANCE

if shcore is not None:
    shcore.GetDpiForMonitor.argtypes = [
        wintypes.HMONITOR, ctypes.c_int,
        ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
    ]
    shcore.GetDpiForMonitor.restype = ctypes.c_long


# --- SetupAPI (device names) --------------------------------------------

try:
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    cfgmgr32 = ctypes.WinDLL("cfgmgr32", use_last_error=True)
except OSError:
    setupapi = None
    cfgmgr32 = None

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

DIGCF_PRESENT = 0x00000002
DIGCF_ALLCLASSES = 0x00000004

SPDRP_DEVICEDESC = 0x00000000
SPDRP_HARDWAREID = 0x00000001

DEVPROP_TYPE_STRING = 0x00000012


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.ULONG)]


# The USB product string as reported by the device itself.
DEVPKEY_Device_BusReportedDeviceDesc = DEVPROPKEY(
    GUID(
        0x540B947E, 0x8B40, 0x45BC,
        (ctypes.c_ubyte * 8)(0xA8, 0xA2, 0x6A, 0x0B, 0x89, 0x4C, 0xBD, 0xA2),
    ),
    4,
)

if setupapi is not None:
    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD
    ]
    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE

    setupapi.SetupDiEnumDeviceInfo.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)
    ]
    setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL

    setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL

    # Vista+; guarded like the other optional APIs.
    try:
        setupapi.SetupDiGetDevicePropertyW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA),
            ctypes.POINTER(DEVPROPKEY), ctypes.POINTER(wintypes.ULONG),
            ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
        ]
        setupapi.SetupDiGetDevicePropertyW.restype = wintypes.BOOL
    except AttributeError:
        pass

    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    cfgmgr32.CM_Get_Parent.argtypes = [
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.c_ulong
    ]
    cfgmgr32.CM_Get_Parent.restype = ctypes.c_ulong  # CONFIGRET, 0 = success


# --- Small helpers ------------------------------------------------------

def set_process_dpi_awareness() -> None:
    """Opt the process into Per-Monitor V2 DPI awareness (with fallbacks).

    Must be called before any window (or tkinter root) is created, otherwise
    Windows virtualizes all coordinates and DWM-stretches our windows.
    """
    try:
        if user32.SetProcessDpiAwarenessContext(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            return
    except AttributeError:
        pass
    try:
        if shcore is not None:
            shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            return
    except (AttributeError, OSError):
        pass
    user32.SetProcessDPIAware()


def set_thread_dpi_awareness(context) -> None:
    """Set the calling thread's DPI awareness, ignoring missing API support."""
    try:
        user32.SetThreadDpiAwarenessContext(context)
    except AttributeError:
        pass


def get_cursor_pos() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def get_virtual_screen_rect() -> tuple[int, int, int, int]:
    """Return the virtual screen as (left, top, width, height) in pixels."""
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def get_dpi_for_point(x: int, y: int) -> int:
    """Return the effective DPI of the monitor containing the given point."""
    if shcore is None:
        return 96
    monitor = user32.MonitorFromPoint(
        wintypes.POINT(x, y), MONITOR_DEFAULTTONEAREST
    )
    dpi_x = wintypes.UINT(96)
    dpi_y = wintypes.UINT(96)
    if shcore.GetDpiForMonitor(
        monitor, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
    ) != 0:
        return 96
    return dpi_x.value or 96


def get_mouse_speed() -> int:
    """Return the system pointer speed setting (1-20, default 10)."""
    speed = ctypes.c_int(10)
    if not user32.SystemParametersInfoW(
        SPI_GETMOUSESPEED, 0, ctypes.byref(speed), 0
    ):
        return 10
    return speed.value
