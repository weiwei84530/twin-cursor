"""Run-at-logon registration.

The launch command lives under the per-user Run key,
HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run: writing it needs
no elevation, and Windows starts it once the user has logged on (late
enough for the Interception driver to have enumerated the mice, and the
startup wait in router.connect covers the rest).

Windows runs the command with an arbitrary working directory, so a source
run cannot simply use "-m twincursor" — the package root has to be put on
sys.path explicitly.
"""

import logging
import os
import sys
import winreg

log = logging.getLogger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "TwinCursor"


def _windowless_python() -> str:
    """pythonw.exe next to the running interpreter, so no console flashes."""
    folder, name = os.path.split(sys.executable)
    if name.lower() == "python.exe":
        candidate = os.path.join(folder, "pythonw.exe")
        if os.path.isfile(candidate):
            return candidate
    return sys.executable


def _string_literal(text: str) -> str:
    """Render a path as a single-quoted Python string literal."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def launch_command() -> str:
    """The command line Windows should run at logon."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import sys, runpy; "
        f"sys.path.insert(0, {_string_literal(root)}); "
        "runpy.run_module('twincursor', run_name='__main__')"
    )
    # The code contains no double quote (Windows paths cannot hold one),
    # so wrapping it in double quotes survives the command-line parser.
    return f'"{_windowless_python()}" -c "{code}"'


def _read():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, kind = winreg.QueryValueEx(key, _VALUE_NAME)
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("Could not read the autostart entry: %s", exc)
        return None
    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        return None
    return value


def is_enabled() -> bool:
    return _read() is not None


def set_enabled(enabled: bool) -> bool:
    """Add or remove the Run entry; True when the change went through."""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(
                    key, _VALUE_NAME, 0, winreg.REG_SZ, launch_command()
                )
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        log.error(
            "Could not %s start with Windows: %s",
            "enable" if enabled else "disable", exc,
        )
        return False
    log.info("Start with Windows: %s", "on" if enabled else "off")
    return True


def sync_command() -> None:
    """Refresh a stale entry after the executable was moved or renamed.

    Only frozen builds do this: a run from source must not overwrite an
    entry that points at an installed EXE.
    """
    if not getattr(sys, "frozen", False):
        return
    stored = _read()
    if stored is None or stored == launch_command():
        return
    log.info("Updating the autostart entry to the current executable path")
    set_enabled(True)
