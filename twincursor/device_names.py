"""Human-readable names for mouse hardware IDs.

The Interception driver only reports hardware IDs (HID\\VID_xxxx&PID_xxxx...).
SetupAPI is used to find the matching Plug and Play device and read the
product string the device itself reported over USB (e.g. "USB Receiver",
"Mi Wireless Mouse"). The HID node itself rarely carries that string, so
the device tree is walked upward a couple of levels to the USB node that
does. A small VID table adds the vendor name when the product string does
not already contain it.
"""

import ctypes
import logging
import re
from ctypes import wintypes

from . import winapi as w

log = logging.getLogger(__name__)

_VID_RE = re.compile(r"VID_([0-9A-Fa-f]{4})")

# Common mouse vendors by USB vendor ID.
_VENDORS = {
    "045E": "Microsoft",
    "0461": "Primax",
    "046D": "Logitech",
    "04D9": "Holtek",
    "04F2": "Chicony",
    "056E": "Elecom",
    "05AC": "Apple",
    "062A": "MosArt",
    "093A": "Pixart",
    "0951": "HyperX",
    "09DA": "A4Tech",
    "0B05": "ASUS",
    "0C45": "Sonix",
    "1038": "SteelSeries",
    "1532": "Razer",
    "17EF": "Lenovo",
    "1B1C": "Corsair",
    "1BCF": "Sunplus",
    "24AE": "Rapoo",
    "2516": "Cooler Master",
    "258A": "SinoWealth",
    "2717": "Xiaomi",
    "413C": "Dell",
}

# Device descriptions that carry no useful information.
_GENERIC_DESCS = ("hid-compliant", "usb input device", "hid device")


def get_display_names(hwids) -> dict:
    """Map each hardware ID to a human-readable device name.

    IDs that cannot be resolved fall back to a vendor name from the VID
    table or a shortened hardware ID. Never raises.
    """
    hwids = list(hwids)
    descriptions = {}
    if w.setupapi is not None and hwids:
        try:
            descriptions = _query_descriptions(hwids)
        except Exception:
            log.exception("SetupAPI device-name lookup failed")
    return {
        hwid: _compose(hwid, *descriptions.get(hwid, (None, None, (), ()))[:2])
        for hwid in hwids
    }


# Markers in a HID node's hardware/compatible IDs that identify a keyboard
# top-level collection (usage page 0x01, usage 0x06).
_KEYBOARD_ID_MARKERS = ("HID_DEVICE_SYSTEM_KEYBOARD", "UP:0001_U:0006")
# Words in a product name that indicate an actual pointing device; a name
# containing one of these is never treated as keyboard-only.
_POINTER_WORDS = ("mouse", "trackball", "trackpad", "touchpad", "pointing")


def find_keyboard_hwids(hwids) -> set:
    """Return the subset of the given hardware IDs that belong to keyboards.

    Some keyboards register an extra mouse-class HID collection, which the
    Interception driver then lists as a mouse. Such entries are detected by
    keyboard usage IDs on the device node itself, by a keyboard-only
    product name, or by a keyboard collection living on the same USB
    interface (see _looks_like_keyboard). Never raises.
    """
    hwids = set(hwids)
    if w.setupapi is None or not hwids:
        return set()
    try:
        info = _query_descriptions(hwids)
    except Exception:
        log.exception("SetupAPI keyboard filtering failed")
        return set()
    return {
        hwid
        for hwid, (bus_desc, device_desc, ids, sibling_ids) in info.items()
        if _looks_like_keyboard(ids, sibling_ids, bus_desc or device_desc)
    }


def _looks_like_keyboard(ids, sibling_ids, name) -> bool:
    if _has_keyboard_marker(ids):
        return True
    lowered = (name or "").lower()
    if any(word in lowered for word in _POINTER_WORDS):
        return False  # named as a pointing device: trust the name
    if "keyboard" in lowered:
        return True
    # A mouse collection sharing its USB interface with a keyboard
    # collection is a keyboard's phantom mouse. Real mice behind combo
    # receivers are safe: those keep the keyboard on its own interface.
    return _has_keyboard_marker(sibling_ids)


def _has_keyboard_marker(ids) -> bool:
    return any(
        marker in candidate.upper()
        for candidate in ids
        for marker in _KEYBOARD_ID_MARKERS
    )


# How far to walk up from the HID node looking for a product string. Level
# 1-2 is the USB interface/composite device; going further would reach hubs
# and controllers, which report their own (useless) strings.
_MAX_PARENT_DEPTH = 2


def _query_descriptions(hwids) -> dict:
    """Return hwid -> (bus_reported_desc, device_desc, ids, sibling_ids)
    for matches, where ids is the node's combined hardware and compatible
    ID list and sibling_ids is the same for the node's siblings (the other
    HID collections on the same interface).

    Enumerates every present device (not just the HID class) so the matched
    HID node's USB ancestors are available for the product-string lookup.
    """
    wanted = {hwid.upper(): hwid for hwid in hwids}
    found: dict = {}
    devinfo = w.setupapi.SetupDiGetClassDevsW(
        None, None, None, w.DIGCF_PRESENT | w.DIGCF_ALLCLASSES
    )
    if not devinfo or devinfo == w.INVALID_HANDLE_VALUE:
        return found
    try:
        by_devinst: dict = {}
        matches: dict = {}
        index = 0
        while True:
            data = w.SP_DEVINFO_DATA()
            data.cbSize = ctypes.sizeof(w.SP_DEVINFO_DATA)
            if not w.setupapi.SetupDiEnumDeviceInfo(
                devinfo, index, ctypes.byref(data)
            ):
                break
            index += 1
            by_devinst[data.DevInst] = data
            for candidate in _get_hardware_ids(devinfo, data):
                original = wanted.get(candidate.upper())
                if original is not None and original not in matches:
                    matches[original] = data
                    break

        for hwid, data in matches.items():
            bus_desc = _get_bus_reported_desc(devinfo, data)
            node = data.DevInst
            depth = 0
            while bus_desc is None and depth < _MAX_PARENT_DEPTH:
                parent = wintypes.DWORD(0)
                if w.cfgmgr32.CM_Get_Parent(ctypes.byref(parent), node, 0) != 0:
                    break
                node = parent.value
                depth += 1
                parent_data = by_devinst.get(node)
                if parent_data is not None:
                    bus_desc = _get_bus_reported_desc(devinfo, parent_data)
            found[hwid] = (
                bus_desc,
                _get_device_desc(devinfo, data),
                _get_all_ids(devinfo, data),
                _get_sibling_ids(devinfo, by_devinst, data),
            )
    finally:
        w.setupapi.SetupDiDestroyDeviceInfoList(devinfo)
    return found


def _get_hardware_ids(devinfo, data) -> list:
    return _get_multi_sz_property(devinfo, data, w.SPDRP_HARDWAREID)


def _get_all_ids(devinfo, data) -> list:
    return (
        _get_multi_sz_property(devinfo, data, w.SPDRP_HARDWAREID)
        + _get_multi_sz_property(devinfo, data, w.SPDRP_COMPATIBLEIDS)
    )


def _get_sibling_ids(devinfo, by_devinst, data) -> list:
    """Combined IDs of the other children of the matched node's parent
    (for a HID collection: the interface's other top-level collections)."""
    parent = wintypes.DWORD(0)
    if w.cfgmgr32.CM_Get_Parent(ctypes.byref(parent), data.DevInst, 0) != 0:
        return []
    child = wintypes.DWORD(0)
    if w.cfgmgr32.CM_Get_Child(ctypes.byref(child), parent.value, 0) != 0:
        return []
    ids: list = []
    node = child.value
    while True:
        if node != data.DevInst:
            sibling = by_devinst.get(node)
            if sibling is not None:
                ids += _get_all_ids(devinfo, sibling)
        next_node = wintypes.DWORD(0)
        if w.cfgmgr32.CM_Get_Sibling(ctypes.byref(next_node), node, 0) != 0:
            break
        node = next_node.value
    return ids


def _get_multi_sz_property(devinfo, data, prop) -> list:
    buf = ctypes.create_unicode_buffer(1024)
    if not w.setupapi.SetupDiGetDeviceRegistryPropertyW(
        devinfo, ctypes.byref(data), prop,
        None, buf, ctypes.sizeof(buf), None,
    ):
        return []
    # REG_MULTI_SZ: NUL-separated strings, empty-string terminated.
    return [part for part in buf[:].split("\x00") if part]


def _get_device_desc(devinfo, data):
    buf = ctypes.create_unicode_buffer(512)
    if not w.setupapi.SetupDiGetDeviceRegistryPropertyW(
        devinfo, ctypes.byref(data), w.SPDRP_DEVICEDESC,
        None, buf, ctypes.sizeof(buf), None,
    ):
        return None
    # May be an indirect string "@oem1.inf,%foo%;Actual description".
    value = buf.value.rsplit(";", 1)[-1].strip()
    return value or None


def _get_bus_reported_desc(devinfo, data):
    getprop = getattr(w.setupapi, "SetupDiGetDevicePropertyW", None)
    if getprop is None:
        return None
    buf = ctypes.create_unicode_buffer(512)
    prop_type = wintypes.ULONG(0)
    if not getprop(
        devinfo, ctypes.byref(data),
        ctypes.byref(w.DEVPKEY_Device_BusReportedDeviceDesc),
        ctypes.byref(prop_type), buf, ctypes.sizeof(buf), None, 0,
    ) or prop_type.value != w.DEVPROP_TYPE_STRING:
        return None
    value = buf.value.strip()
    return value or None


def _compose(hwid: str, bus_desc, device_desc) -> str:
    match = _VID_RE.search(hwid)
    vendor = _VENDORS.get(match.group(1).upper()) if match else None

    desc = bus_desc
    if not desc and device_desc:
        lowered = device_desc.lower()
        if not any(generic in lowered for generic in _GENERIC_DESCS):
            desc = device_desc

    if desc:
        if vendor and vendor.lower() not in desc.lower():
            return f"{vendor} {desc}"
        return desc
    if vendor:
        return f"{vendor} Mouse"
    # Last resort: the identifying part of the hardware ID.
    return hwid.split("&REV", 1)[0]
