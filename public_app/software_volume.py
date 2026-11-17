"""Make the Windows volume work on the Philips Audio Set output.

The UAC3553B reports a hardware volume control (USB Audio feature unit,
-41..0 dB), so Windows sends the volume to the chip instead of attenuating the
audio itself. The chip ignores that value: only mute works, so the Windows
slider and volume keys seem stuck at 0% / 100% (verified on a FWM589).

This module applies the Windows volume in software instead, the same way a
player's own volume (YouTube, Spotify) works: it follows the endpoint level
and scales every audio session that plays on the Philips output. Volume
changes the user makes in the Windows volume mixer are kept as relative
levels.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

SILENT_SCALAR = 0.005  # endpoint slider at 0%


def endpoint_gain(level_db: float, scalar: float) -> float:
    """Linear gain for an endpoint volume (dB as Windows maps the slider)."""
    if scalar <= SILENT_SCALAR:
        return 0.0
    return max(0.0, min(1.0, math.pow(10.0, float(level_db) / 20.0)))


class SessionLevels:
    """Track per-session user levels so mixer tweaks survive the scaling."""

    def __init__(self, tolerance: float = 0.02) -> None:
        self.tolerance = tolerance
        self.user_level: dict[str, float] = {}
        self.applied: dict[str, float] = {}

    def target(self, key: str, current: float, gain: float) -> float:
        applied = self.applied.get(key)
        if applied is None:
            # First time we see the session: its current level is the user's.
            self.user_level[key] = current
        elif abs(current - applied) > self.tolerance:
            # Changed in the volume mixer since our last write.
            self.user_level[key] = current / gain if gain > 0 else current
            self.user_level[key] = max(0.0, min(1.0, self.user_level[key]))
        level = max(0.0, min(1.0, self.user_level.get(key, 1.0) * gain))
        self.applied[key] = level
        return level

    def restore_level(self, key: str) -> float:
        return self.user_level.get(key, 1.0)

    def forget_missing(self, keys: set[str]) -> None:
        for store in (self.user_level, self.applied):
            for key in list(store):
                if key not in keys:
                    del store[key]


def _session_key(session) -> str:
    try:
        key = session.InstanceIdentifier
    except Exception:  # noqa: BLE001
        key = None
    if not key:
        key = f"pid:{getattr(session, 'ProcessId', 0)}"
    return str(key)


class SoftwareVolume:
    """Background loop that keeps the Philips sessions at the Windows level."""

    def __init__(
        self,
        find_endpoint: Callable[[], object | None],
        *,
        poll_s: float = 0.2,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self._find_endpoint = find_endpoint
        self._poll_s = poll_s
        self._on_status = on_status
        self._levels = SessionLevels()
        self._enabled = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_status = ""
        self.last_gain: float | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled.is_set()

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._enabled.set()
            self.start()
        else:
            self._enabled.clear()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="PhilipsSoftwareVolume", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None

    def _status(self, message: str) -> None:
        if message and message != self._last_status:
            self._last_status = message
            if self._on_status is not None:
                try:
                    self._on_status(message)
                except Exception:  # noqa: BLE001
                    pass

    def _run(self) -> None:
        import comtypes

        comtypes.CoInitialize()
        device = None
        next_lookup = 0.0
        restored = True
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                try:
                    if device is None or now >= next_lookup:
                        device = self._find_endpoint()
                        next_lookup = now + 3.0
                    if device is None:
                        self.last_gain = None
                    elif self._enabled.is_set():
                        self._apply(device)
                        restored = False
                    elif not restored:
                        self._restore(device)
                        restored = True
                except Exception as exc:  # noqa: BLE001 - device unplugged etc.
                    device = None
                    next_lookup = 0.0
                    self._status(f"Volume por software: aguardando a saida Philips ({exc}).")
                self._stop.wait(self._poll_s)
            if device is not None and not restored:
                try:
                    self._restore(device)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass

    def _sessions(self, device):
        from pycaw.pycaw import AudioSession, IAudioSessionControl2

        manager = device.AudioSessionManager
        enumerator = manager.GetSessionEnumerator()
        sessions = []
        for index in range(enumerator.GetCount()):
            control = enumerator.GetSession(index)
            if control is None:
                continue
            control2 = control.QueryInterface(IAudioSessionControl2)
            if control2 is not None:
                sessions.append(AudioSession(control2))
        return sessions

    def _apply(self, device) -> None:
        endpoint = device.EndpointVolume
        gain = endpoint_gain(endpoint.GetMasterVolumeLevel(), endpoint.GetMasterVolumeLevelScalar())
        self.last_gain = gain
        keys: set[str] = set()
        for session in self._sessions(device):
            key = _session_key(session)
            keys.add(key)
            volume = session.SimpleAudioVolume
            current = float(volume.GetMasterVolume())
            level = self._levels.target(key, current, gain)
            if abs(level - current) > 0.001:
                volume.SetMasterVolume(level, None)
        self._levels.forget_missing(keys)
        self._status("Volume do Windows corrigido na saida Philips (aplicado no proprio som).")

    def _restore(self, device) -> None:
        for session in self._sessions(device):
            key = _session_key(session)
            volume = session.SimpleAudioVolume
            volume.SetMasterVolume(self._levels.restore_level(key), None)
        self._levels = SessionLevels()
        self.last_gain = None
        self._status("Volume por software desligado; saida Philips sem atenuacao.")
