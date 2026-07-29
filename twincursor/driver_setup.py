"""First-run installation flow for the Interception kernel driver.

TwinCursor is a windowed executable with no console, so a failed driver
connection would otherwise be a silent exit. When the driver cannot be
opened, this module explains the problem in a message box and — when the
driver is simply not installed — offers to run the bundled official
installer (elevated through UAC). A reboot is required afterwards.
"""

import ctypes
import logging
import os
import winreg
from ctypes import wintypes

from . import resource_path
from . import winapi as w

log = logging.getLogger(__name__)

_TITLE = "TwinCursor"

# The Interception installer registers these upper-filter driver services.
_SERVICE_KEYS = (
    r"SYSTEM\CurrentControlSet\Services\keyboard",
    r"SYSTEM\CurrentControlSet\Services\mouse",
)


def driver_installed() -> bool:
    """True when the Interception filter services are registered.

    Services present while opening the driver fails usually means the
    machine has not been rebooted since installation; services absent
    means the driver was never installed.
    """
    try:
        for path in _SERVICE_KEYS:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path))
    except OSError:
        return False
    return True


def _message_box(text: str, flags: int) -> int:
    return w.user32.MessageBoxW(None, text, _TITLE, flags)


def _installer_path() -> str:
    """Bundle root in the frozen EXE; interception_installer/ in source."""
    path = resource_path("install-interception.exe")
    if os.path.isfile(path):
        return path
    return resource_path(
        os.path.join("interception_installer", "install-interception.exe")
    )


def _run_installer_elevated() -> bool:
    """Run the bundled installer with admin rights; True on exit code 0."""
    info = w.SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = w.SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = _installer_path()
    info.lpParameters = "/install"
    info.nShow = w.SW_HIDE  # the installer is a console program
    if not w.shell32.ShellExecuteExW(ctypes.byref(info)):
        # Most commonly ERROR_CANCELLED: the user declined the UAC prompt.
        log.error(
            "Driver installer did not start (error %d)",
            ctypes.get_last_error(),
        )
        return False
    if not info.hProcess:
        return False
    try:
        w.kernel32.WaitForSingleObject(info.hProcess, w.INFINITE)
        code = wintypes.DWORD()
        if not w.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            return False
        log.info("Driver installer finished with exit code %d", code.value)
        return code.value == 0
    finally:
        w.kernel32.CloseHandle(info.hProcess)


def handle_driver_failure() -> None:
    """Explain a failed driver connection and offer to fix it."""
    if driver_installed():
        _message_box(
            "TwinCursor could not open the Interception driver even though"
            " it appears to be installed.\n\nIf you just installed it,"
            " restart your computer and launch TwinCursor again.",
            w.MB_OK | w.MB_ICONWARNING,
        )
        return

    choice = _message_box(
        "TwinCursor needs the Interception kernel driver, which is not"
        " installed on this computer.\n\nInstall it now? Windows will ask"
        " for administrator approval, and a restart is required afterwards.",
        w.MB_YESNO | w.MB_ICONQUESTION,
    )
    if choice != w.IDYES:
        return

    # The installer's exit code is not documented, so treat the services
    # showing up as success as well.
    if not _run_installer_elevated() and not driver_installed():
        _message_box(
            "The driver installation did not complete.\n\nYou can install"
            " it manually: download Interception from"
            " https://github.com/oblitum/Interception and run"
            ' "install-interception.exe /install" as administrator.',
            w.MB_OK | w.MB_ICONERROR,
        )
        return

    choice = _message_box(
        "The Interception driver was installed. Restart your computer,"
        " then launch TwinCursor again.\n\nRestart now?",
        w.MB_YESNO | w.MB_ICONQUESTION,
    )
    if choice == w.IDYES:
        w.shell32.ShellExecuteW(
            None, None, "shutdown", "/r /t 3", None, w.SW_HIDE
        )
