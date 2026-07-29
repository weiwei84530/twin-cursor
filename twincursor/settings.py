"""Registry-backed persistence for TwinCursor settings.

Settings are stored as JSON strings under HKCU\\SOFTWARE\\TwinCursor:

- "SlotSettings": per-slot settings keyed by slot name ("a" = First Mouse,
  "b" = Second Mouse). Each entry holds "is_mirrored" and "hotkey" (null =
  disabled). Mirror state and hotkey belong to the slot, not the device:
  changing or swapping the devices in the slots leaves each slot's settings
  in place.
- "DeviceSelection": {"a": <hwid key>|null, "b": <hwid key>|null}. A slot
  missing from the dict means "assign automatically"; null means the user
  explicitly chose no device.

Versions up to 1.0 stored per-device settings keyed by hardware ID in
"MirrorSettings"; on first load those are migrated to slot settings using
the stored device selection.
"""

import json
import logging
import winreg

log = logging.getLogger(__name__)

_KEY_PATH = r"SOFTWARE\TwinCursor"
_SLOT_VALUE = "SlotSettings"
_SELECTION_VALUE = "DeviceSelection"
_LEGACY_MIRROR_VALUE = "MirrorSettings"
_SLOT_NAMES = ("a", "b")


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


def load_slots() -> dict:
    """Return the stored per-slot settings ({"a": entry, "b": entry}).

    Only slots that were actually stored are included. Each entry has
    "is_mirrored" (bool) and, when it was stored, "hotkey" (validated dict
    or None); an absent "hotkey" means the slot default applies.
    """
    data = _read_json(_SLOT_VALUE)
    if data is None:
        data = _migrate_legacy()
    if data is None:
        return {}
    result = {}
    for name in _SLOT_NAMES:
        entry = data.get(name)
        if not isinstance(entry, dict):
            continue
        cleaned = {"is_mirrored": bool(entry.get("is_mirrored", False))}
        if "hotkey" in entry:
            cleaned["hotkey"] = _validate_hotkey(entry["hotkey"])
        result[name] = cleaned
    return result


def save_slots(slots) -> None:
    """Persist the slot settings (a sequence of two dicts)."""
    _write_json(_SLOT_VALUE, {
        name: {
            "is_mirrored": bool(slot["is_mirrored"]),
            "hotkey": slot["hotkey"],
        }
        for name, slot in zip(_SLOT_NAMES, slots)
    })


def _migrate_legacy():
    """Build slot settings from the pre-1.1 per-device store, if present.

    The devices stored in the selection carry their old settings over to
    the slot they were assigned to; from then on the settings stay with
    the slot.
    """
    mirror = _read_json(_LEGACY_MIRROR_VALUE)
    if not mirror:
        return None
    selection = _read_json(_SELECTION_VALUE) or {}
    data = {}
    for name in _SLOT_NAMES:
        key = selection.get(name)
        entry = mirror.get(key) if isinstance(key, str) else None
        if isinstance(entry, dict):
            data[name] = entry
    if not data:
        return None
    _write_json(_SLOT_VALUE, data)
    log.info("Migrated legacy per-device settings to slot settings")
    return data


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
