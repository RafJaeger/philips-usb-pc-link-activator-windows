"""Read Windows Plug and Play devices directly (SetupAPI).

The same information Device Manager shows: instance id, friendly name, class,
driver service, driver INF and whether the device is plugged in right now.
"""
from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes
from dataclasses import dataclass

DIGCF_PRESENT = 0x02
DIGCF_ALLCLASSES = 0x04
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_MORE_ITEMS = 259

SPDRP_DEVICEDESC = 0x00
SPDRP_SERVICE = 0x04
SPDRP_CLASS = 0x07
SPDRP_FRIENDLYNAME = 0x0C

DEVPROP_TYPE_BOOLEAN = 0x11
DEVPROP_TYPE_STRING = 0x12


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, text: str) -> "GUID":
        value = uuid.UUID(text)
        guid = cls()
        guid.Data1, guid.Data2, guid.Data3 = value.fields[0], value.fields[1], value.fields[2]
        guid.Data4[:] = list(value.bytes[8:])
        return guid


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.ULONG)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


def _key(fmtid: str, pid: int) -> DEVPROPKEY:
    return DEVPROPKEY(GUID.from_string(fmtid), pid)


DEVPKEY_DEVICE_IS_PRESENT = _key("540b947e-8b40-45bc-a8a2-6a0b894cbda2", 5)
DEVPKEY_DEVICE_DRIVER_INF_PATH = _key("a8b865dd-2e3d-4094-ad97-e593a70c75d6", 5)

_setupapi = None


def _api():
    global _setupapi
    if _setupapi is None:
        api = ctypes.WinDLL("setupapi", use_last_error=True)
        api.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
        api.SetupDiGetClassDevsW.restype = ctypes.c_void_p
        api.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
        api.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
        api.SetupDiGetDeviceInstanceIdW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        api.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
        api.SetupDiGetDeviceRegistryPropertyW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        api.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
        api.SetupDiGetDevicePropertyW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(DEVPROPKEY),
            ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            wintypes.DWORD,
        ]
        api.SetupDiGetDevicePropertyW.restype = wintypes.BOOL
        api.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
        api.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
        _setupapi = api
    return _setupapi


@dataclass(frozen=True)
class PnpDevice:
    instance_id: str
    name: str
    device_class: str
    service: str | None
    inf_path: str | None
    present: bool

    @property
    def inf_name(self) -> str | None:
        """Published INF name (e.g. oem12.inf) of the installed driver."""
        if not self.inf_path:
            return None
        return self.inf_path.replace("/", "\\").rsplit("\\", 1)[-1]


def _registry_string(api, handle, data, prop: int) -> str | None:
    buffer = ctypes.create_unicode_buffer(512)
    reg_type = wintypes.DWORD()
    if not api.SetupDiGetDeviceRegistryPropertyW(
        handle, ctypes.byref(data), prop, ctypes.byref(reg_type), buffer, ctypes.sizeof(buffer), None
    ):
        return None
    return buffer.value or None


def _property(api, handle, data, key: DEVPROPKEY):
    buffer = ctypes.create_string_buffer(1024)
    prop_type = wintypes.ULONG()
    if not api.SetupDiGetDevicePropertyW(
        handle, ctypes.byref(data), ctypes.byref(key), ctypes.byref(prop_type), buffer,
        ctypes.sizeof(buffer), None, 0,
    ):
        return None
    if prop_type.value == DEVPROP_TYPE_BOOLEAN:
        return buffer.raw[0] != 0
    if prop_type.value == DEVPROP_TYPE_STRING:
        return ctypes.wstring_at(ctypes.addressof(buffer)) or None
    return None


def list_devices(enumerator: str | None = "USB", *, present_only: bool = False) -> list[PnpDevice]:
    """Every device under one enumerator ("USB", "SWD", ... or None for all)."""
    api = _api()
    flags = DIGCF_ALLCLASSES | (DIGCF_PRESENT if present_only else 0)
    handle = api.SetupDiGetClassDevsW(None, enumerator, None, flags)
    if handle in (None, INVALID_HANDLE_VALUE):
        raise ctypes.WinError(ctypes.get_last_error())
    devices: list[PnpDevice] = []
    try:
        index = 0
        while True:
            data = SP_DEVINFO_DATA()
            data.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not api.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(data)):
                if ctypes.get_last_error() == ERROR_NO_MORE_ITEMS:
                    break
                index += 1
                continue
            index += 1
            id_buffer = ctypes.create_unicode_buffer(512)
            if not api.SetupDiGetDeviceInstanceIdW(handle, ctypes.byref(data), id_buffer, 512, None):
                continue
            present = _property(api, handle, data, DEVPKEY_DEVICE_IS_PRESENT)
            devices.append(
                PnpDevice(
                    instance_id=id_buffer.value,
                    name=(
                        _registry_string(api, handle, data, SPDRP_FRIENDLYNAME)
                        or _registry_string(api, handle, data, SPDRP_DEVICEDESC)
                        or ""
                    ),
                    device_class=_registry_string(api, handle, data, SPDRP_CLASS) or "",
                    service=_registry_string(api, handle, data, SPDRP_SERVICE),
                    inf_path=_property(api, handle, data, DEVPKEY_DEVICE_DRIVER_INF_PATH),
                    present=bool(present) if present is not None else present_only,
                )
            )
    finally:
        api.SetupDiDestroyDeviceInfoList(handle)
    return devices


def find_devices(prefix: str, *, enumerator: str | None = "USB") -> list[PnpDevice]:
    """Devices whose instance id starts with ``prefix`` (case-insensitive)."""
    wanted = prefix.upper()
    return [d for d in list_devices(enumerator) if d.instance_id.upper().startswith(wanted)]


def describe(devices: list[PnpDevice]) -> str:
    """Readable listing for the diagnostic log."""
    lines = []
    for d in devices:
        lines.extend(
            (
                d.instance_id,
                f"  Nome: {d.name or '-'}",
                f"  Classe: {d.device_class or '-'}",
                f"  Servico: {d.service or '-'}",
                f"  INF: {d.inf_path or '-'}",
                f"  Conectado: {'sim' if d.present else 'nao'}",
            )
        )
    return "\n".join(lines) if lines else "(nenhum dispositivo)"
