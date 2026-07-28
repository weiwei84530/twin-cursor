"""Registry-backed persistence for per-mouse settings.

Settings are stored as a JSON string in HKCU\\SOFTWARE\\TwinCursor under the
"MirrorSettings" value, keyed by the mouse hardware ID so a setting follows
the physical device across reboots and USB port changes. Entries from the
legacy format (keyed by interception device number) are simply ignored.
"""

import json
import logging
import winreg

log = logging.getLogger(__name__)

_KEY_PATH = r"SOFTWARE\TwinCursor"
_VALUE_NAME = "MirrorSettings"


def load() -> dict:
    """Return the stored settings mapping, or an empty dict."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY_PATH) as key:
            raw, _ = winreg.QueryValueEx(key, _VALUE_NAME)
        data = json.loads(raw)
    except FileNotFoundError:
        log.debug("No stored settings found, using defaults")
        return {}
    except (OSError, ValueError) as exc:
        log.warning("Failed to load settings: %s", exc)
        return {}

    if not isinstance(data, dict):
        return {}
    log.debug("Loaded settings: %s", data)
    return data


def save(devices) -> None:
    """Persist the settings of the given mouse devices.

    Entries for devices that are not currently connected are preserved.
    """
    data = load()
    for device in devices:
        data[device.settings_key] = {"is_mirrored": device.is_mirrored}
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _KEY_PATH) as key:
            winreg.SetValueEx(
                key, _VALUE_NAME, 0, winreg.REG_SZ,
                json.dumps(data, ensure_ascii=False),
            )
    except OSError as exc:
        log.error("Failed to save settings: %s", exc)
        return
    log.debug("Saved settings: %s", data)


def apply(devices) -> None:
    """Apply stored settings to the given mouse devices."""
    data = load()
    for device in devices:
        entry = data.get(device.settings_key)
        if isinstance(entry, dict):
            device.is_mirrored = bool(entry.get("is_mirrored", False))
            log.debug(
                "%s mirror setting restored: %s", device.name, device.is_mirrored
            )
