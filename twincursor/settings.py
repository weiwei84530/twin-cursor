"""Registry-backed persistence for TwinCursor settings.

Settings are stored as JSON strings under HKCU\\SOFTWARE\\TwinCursor:

- "MirrorSettings": per-mouse settings keyed by hardware ID, so a setting
  follows the physical device across reboots and USB port changes. Each
  entry holds "is_mirrored" and, once configured, "hotkey" (null when the
  user explicitly disabled it; absent when never configured, in which case
  slot defaults apply).
- "DeviceSelection": {"a": <hwid key>|null, "b": <hwid key>|null}. A slot
  missing from the dict means "assign automatically"; null means the user
  explicitly chose no device.
"""

import json
import logging
import winreg

log = logging.getLogger(__name__)

_KEY_PATH = r"SOFTWARE\TwinCursor"
_MIRROR_VALUE = "MirrorSettings"
_SELECTION_VALUE = "DeviceSelection"
_SLOT_NAMES = ("a", "b")

# Sentinel for a per-device hotkey that was never configured (as opposed to
# one the user explicitly cleared, which is stored as null).
HOTKEY_UNSET = object()


def _read_json(value_name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY_PATH) as key:
            raw, _ = winreg.QueryValueEx(key, value_name)
        data = json.loads(raw)
    except FileNotFoundError:
        log.debug("No stored %s found", value_name)
        return None
    except (OSError, ValueError) as exc:
        log.warning("Failed to load %s: %s", value_name, exc)
        return None
    return data if isinstance(data, dict) else None


def _write_json(value_name: str, data: dict) -> None:
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _KEY_PATH) as key:
            winreg.SetValueEx(
                key, value_name, 0, winreg.REG_SZ,
                json.dumps(data, ensure_ascii=False),
            )
    except OSError as exc:
        log.error("Failed to save %s: %s", value_name, exc)
        return
    log.debug("Saved %s: %s", value_name, data)


def load() -> dict:
    """Return the stored per-mouse settings mapping, or an empty dict."""
    return _read_json(_MIRROR_VALUE) or {}


def save(devices) -> None:
    """Persist the settings of the given mouse devices.

    Entries for devices that are not currently connected are preserved.
    """
    data = load()
    for device in devices:
        entry = {"is_mirrored": device.is_mirrored}
        if device.hotkey is not HOTKEY_UNSET:
            entry["hotkey"] = device.hotkey
        data[device.settings_key] = entry
    _write_json(_MIRROR_VALUE, data)


def apply(devices) -> None:
    """Apply stored settings to the given mouse devices."""
    data = load()
    for device in devices:
        entry = data.get(device.settings_key)
        if not isinstance(entry, dict):
            continue
        device.is_mirrored = bool(entry.get("is_mirrored", False))
        if "hotkey" in entry:
            device.hotkey = _validate_hotkey(entry["hotkey"])
        log.debug(
            "%s settings restored: mirrored=%s", device.label, device.is_mirrored
        )


def _validate_hotkey(value):
    if (
        isinstance(value, dict)
        and isinstance(value.get("mods"), int)
        and isinstance(value.get("vk"), int)
        and isinstance(value.get("label"), str)
    ):
        return {
            "mods": value["mods"], "vk": value["vk"], "label": value["label"]
        }
    return None


def load_selection():
    """Return the stored slot selection ({"a": ..., "b": ...}), or None.

    Only slots that were explicitly stored are included; values are either
    a hardware-ID key or None.
    """
    data = _read_json(_SELECTION_VALUE)
    if data is None:
        return None
    return {
        name: data[name]
        for name in _SLOT_NAMES
        if name in data and (data[name] is None or isinstance(data[name], str))
    }


def save_selection(assignment) -> None:
    """Persist the slot assignment (a sequence of two hwid keys / Nones)."""
    _write_json(_SELECTION_VALUE, dict(zip(_SLOT_NAMES, assignment)))
