"""Bass, treble and AGC of the Philips chip (UAC3553B), through Windows.

The USB Audio feature unit of the stereo exposes bass and treble (-12..+12 dB,
0.5 dB steps) and an automatic gain control. Unlike the volume, the chip
really applies them (verified on a FWM589). Windows publishes them in the
device topology as IAudioBass, IAudioTreble and IAudioAutoGainControl.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, c_float, c_int
from ctypes.wintypes import BOOL, DWORD, LPWSTR, UINT
from dataclasses import dataclass

import comtypes
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown


class IControlInterface(IUnknown):
    _iid_ = GUID("{45d37c3f-5140-444a-ae24-400789f3cbf3}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetName", (["out"], POINTER(LPWSTR), "name")),
        COMMETHOD([], HRESULT, "GetIID", (["out"], POINTER(GUID), "iid")),
    ]


class IPart(IUnknown):
    _iid_ = GUID("{AE2DE0E4-5BCA-4F2D-AA46-5D13F8FDB3A9}")


class IPartsList(IUnknown):
    _iid_ = GUID("{6DAA848C-5EB0-45CC-AEA5-998A2CDA1FFB}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(UINT), "count")),
        COMMETHOD([], HRESULT, "GetPart", (["in"], UINT, "index"), (["out"], POINTER(POINTER(IPart)), "part")),
    ]


IPart._methods_ = [
    COMMETHOD([], HRESULT, "GetName", (["out"], POINTER(LPWSTR), "name")),
    COMMETHOD([], HRESULT, "GetLocalId", (["out"], POINTER(UINT), "id")),
    COMMETHOD([], HRESULT, "GetGlobalId", (["out"], POINTER(LPWSTR), "id")),
    COMMETHOD([], HRESULT, "GetPartType", (["out"], POINTER(c_int), "kind")),
    COMMETHOD([], HRESULT, "GetSubType", (["out"], POINTER(GUID), "subtype")),
    COMMETHOD([], HRESULT, "GetControlInterfaceCount", (["out"], POINTER(UINT), "count")),
    COMMETHOD(
        [], HRESULT, "GetControlInterface",
        (["in"], UINT, "index"), (["out"], POINTER(POINTER(IControlInterface)), "control"),
    ),
    COMMETHOD([], HRESULT, "EnumPartsIncoming", (["out"], POINTER(POINTER(IPartsList)), "parts")),
    COMMETHOD([], HRESULT, "EnumPartsOutgoing", (["out"], POINTER(POINTER(IPartsList)), "parts")),
    COMMETHOD([], HRESULT, "GetTopologyObject", (["out"], POINTER(POINTER(IUnknown)), "topology")),
    COMMETHOD(
        [], HRESULT, "Activate",
        (["in"], DWORD, "context"), (["in"], POINTER(GUID), "iid"), (["out"], POINTER(POINTER(IUnknown)), "object"),
    ),
]


class IConnector(IUnknown):
    _iid_ = GUID("{9c2c4058-23f5-41de-877a-df3af236a09e}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetType", (["out"], POINTER(c_int), "kind")),
        COMMETHOD([], HRESULT, "GetDataFlow", (["out"], POINTER(c_int), "flow")),
        COMMETHOD([], HRESULT, "ConnectTo", (["in"], POINTER(IUnknown), "other")),
        COMMETHOD([], HRESULT, "Disconnect"),
        COMMETHOD([], HRESULT, "IsConnected", (["out"], POINTER(BOOL), "connected")),
        COMMETHOD([], HRESULT, "GetConnectedTo", (["out"], POINTER(POINTER(IUnknown)), "other")),
    ]


class IDeviceTopology(IUnknown):
    _iid_ = GUID("{2A07407E-6497-4A18-9787-32F79BD0D98F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetConnectorCount", (["out"], POINTER(UINT), "count")),
        COMMETHOD([], HRESULT, "GetConnector", (["in"], UINT, "index"), (["out"], POINTER(POINTER(IConnector)), "c")),
    ]


class IPerChannelDbLevel(IUnknown):
    _iid_ = GUID("{C2F8E001-F205-4BC9-99BC-C13B1E048CCB}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetChannelCount", (["out"], POINTER(UINT), "count")),
        COMMETHOD(
            [], HRESULT, "GetLevelRange",
            (["in"], UINT, "channel"), (["out"], POINTER(c_float), "min"),
            (["out"], POINTER(c_float), "max"), (["out"], POINTER(c_float), "step"),
        ),
        COMMETHOD([], HRESULT, "GetLevel", (["in"], UINT, "channel"), (["out"], POINTER(c_float), "level")),
        COMMETHOD(
            [], HRESULT, "SetLevel",
            (["in"], UINT, "channel"), (["in"], c_float, "level"), (["in"], POINTER(GUID), "context"),
        ),
        COMMETHOD([], HRESULT, "SetLevelUniform", (["in"], c_float, "level"), (["in"], POINTER(GUID), "context")),
    ]


class IAudioAutoGainControl(IUnknown):
    _iid_ = GUID("{85401FD4-6DE4-4B9D-9869-2D6753A82F3C}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetEnabled", (["out"], POINTER(BOOL), "enabled")),
        COMMETHOD([], HRESULT, "SetEnabled", (["in"], BOOL, "enabled"), (["in"], POINTER(GUID), "context")),
    ]


IID_BASS = "{A2B1A1D9-4DB3-425D-A2B2-BD335CB3E2E5}"
IID_TREBLE = "{0A717812-694E-4907-B74B-BAFA5CFDCA7B}"
IID_AGC = "{85401FD4-6DE4-4B9D-9869-2D6753A82F3C}"


@dataclass(frozen=True)
class LevelRange:
    minimum: float
    maximum: float
    step: float

    def clamp(self, value: float) -> float:
        step = self.step or 0.5
        value = max(self.minimum, min(self.maximum, float(value)))
        return round(round(value / step) * step, 3)


class ChipTone:
    """Bass/treble/AGC controls of one render endpoint (Philips Audio Set)."""

    def __init__(self, bass=None, treble=None, agc=None) -> None:
        self._bass = bass
        self._treble = treble
        self._agc = agc

    @classmethod
    def from_endpoint(cls, endpoint) -> "ChipTone":
        """Walk the endpoint topology upstream and collect the tone controls."""
        comtypes.CoInitialize()
        topology = endpoint._dev.Activate(IDeviceTopology._iid_, comtypes.CLSCTX_ALL, None)
        topology = topology.QueryInterface(IDeviceTopology)
        connector = topology.GetConnector(0)
        start = connector.GetConnectedTo().QueryInterface(IConnector).QueryInterface(IPart)
        found: dict[str, object] = {}
        seen: set[str] = set()
        queue = [start]
        while queue:
            part = queue.pop(0)
            part_id = part.GetGlobalId()
            if part_id in seen:
                continue
            seen.add(part_id)
            for index in range(part.GetControlInterfaceCount()):
                iid = str(part.GetControlInterface(index).GetIID()).upper()
                if iid in (IID_BASS, IID_TREBLE, IID_AGC) and iid not in found:
                    control = part.Activate(comtypes.CLSCTX_ALL, ctypes.pointer(GUID(iid)))
                    interface = IAudioAutoGainControl if iid == IID_AGC else IPerChannelDbLevel
                    found[iid] = control.QueryInterface(interface)
            try:
                incoming = part.EnumPartsIncoming()
            except Exception:  # noqa: BLE001 - the first part has no inputs
                continue
            for index in range(incoming.GetCount()):
                queue.append(incoming.GetPart(index))
        return cls(found.get(IID_BASS), found.get(IID_TREBLE), found.get(IID_AGC))

    @property
    def available(self) -> bool:
        return self._bass is not None or self._treble is not None

    @property
    def has_agc(self) -> bool:
        return self._agc is not None

    def _level(self, control) -> float | None:
        return None if control is None else round(float(control.GetLevel(0)), 2)

    def _range(self, control) -> LevelRange | None:
        if control is None:
            return None
        minimum, maximum, step = control.GetLevelRange(0)
        return LevelRange(float(minimum), float(maximum), float(step))

    def _set(self, control, value: float) -> float | None:
        if control is None:
            return None
        level = self._range(control).clamp(value)
        control.SetLevelUniform(level, None)
        return level

    @property
    def bass(self) -> float | None:
        return self._level(self._bass)

    @property
    def treble(self) -> float | None:
        return self._level(self._treble)

    def bass_range(self) -> LevelRange | None:
        return self._range(self._bass)

    def treble_range(self) -> LevelRange | None:
        return self._range(self._treble)

    def set_bass(self, value: float) -> float | None:
        return self._set(self._bass, value)

    def set_treble(self, value: float) -> float | None:
        return self._set(self._treble, value)

    @property
    def agc(self) -> bool | None:
        return None if self._agc is None else bool(self._agc.GetEnabled())

    def set_agc(self, enabled: bool) -> None:
        if self._agc is not None:
            self._agc.SetEnabled(bool(enabled), None)
