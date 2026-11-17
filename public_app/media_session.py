"""Current Windows media session (the same data as the Windows media flyout).

Uses Windows.Media.Control (GlobalSystemMediaTransportControlsSessionManager),
which every modern player feeds: Spotify, browsers, Windows Media Player,
VLC, foobar2000 and so on. When the WinRT projection is not available, the
monitor stays empty and transport commands fall back to media keys.
"""

from __future__ import annotations

import asyncio
import ctypes
import threading
import time
from collections.abc import Callable

from pc_link_protocol import MediaInfo

try:  # pragma: no cover - depends on the Windows SDK projection
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _SessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as _PlaybackStatus,
    )
except Exception:  # noqa: BLE001
    _SessionManager = None
    _PlaybackStatus = None

VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3

_FALLBACK_KEYS = {
    "play": VK_MEDIA_PLAY_PAUSE,
    "pause": VK_MEDIA_PLAY_PAUSE,
    "playpause": VK_MEDIA_PLAY_PAUSE,
    "stop": VK_MEDIA_STOP,
    "next": VK_MEDIA_NEXT_TRACK,
    "previous": VK_MEDIA_PREV_TRACK,
}


def media_api_available() -> bool:
    return _SessionManager is not None


def send_virtual_key(vk_code: int) -> None:
    user32 = ctypes.windll.user32
    keyeventf_extendedkey = 0x0001
    keyeventf_keyup = 0x0002
    user32.keybd_event(vk_code, 0, keyeventf_extendedkey, 0)
    user32.keybd_event(vk_code, 0, keyeventf_extendedkey | keyeventf_keyup, 0)


def _status_name(value) -> str:
    if _PlaybackStatus is None or value is None:
        return "none"
    if value == _PlaybackStatus.PLAYING:
        return "playing"
    if value == _PlaybackStatus.PAUSED:
        return "paused"
    if value in (_PlaybackStatus.STOPPED, _PlaybackStatus.CLOSED):
        return "stopped"
    return "stopped"


class MediaMonitor:
    """Poll the current media session in a background asyncio loop."""

    def __init__(
        self,
        *,
        poll_interval_s: float = 1.0,
        on_change: Callable[[MediaInfo], None] | None = None,
        key_sender: Callable[[int], None] = send_virtual_key,
    ) -> None:
        self._poll_interval_s = poll_interval_s
        self._on_change = on_change
        self._key_sender = key_sender
        self._lock = threading.Lock()
        self._info = MediaInfo()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._manager = None
        self.last_error = ""

    @property
    def available(self) -> bool:
        return media_api_available()

    def snapshot(self) -> MediaInfo:
        with self._lock:
            return self._info

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not self.available:
            self.last_error = "API de midia do Windows indisponivel; usando teclas de midia."
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="WindowsMediaSession", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(lambda: None)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None

    # ----- background loop -----

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._poll_forever())
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"Monitor de midia parou: {exc}"
        finally:
            self._loop = None
            loop.close()

    async def _poll_forever(self) -> None:
        while not self._stop.is_set():
            try:
                info = await self._read_current()
                self.last_error = ""
            except Exception as exc:  # noqa: BLE001
                info = MediaInfo()
                self.last_error = str(exc)
                self._manager = None
            self._publish(info)
            await asyncio.sleep(self._poll_interval_s)

    async def _session(self):
        if self._manager is None:
            self._manager = await _SessionManager.request_async()
        return self._manager.get_current_session()

    async def _read_current(self) -> MediaInfo:
        session = await self._session()
        if session is None:
            return MediaInfo()
        properties = await session.try_get_media_properties_async()
        status = "none"
        try:
            status = _status_name(session.get_playback_info().playback_status)
        except Exception:  # noqa: BLE001
            status = "none"
        position = None
        try:
            timeline = session.get_timeline_properties()
            position = float(timeline.position.total_seconds())
            if status == "playing":
                updated = timeline.last_updated_time
                # The timeline is refreshed only now and then; extrapolate.
                if updated is not None:
                    elapsed = time.time() - updated.timestamp()
                    if 0 <= elapsed < 6 * 3600:
                        position += elapsed
        except Exception:  # noqa: BLE001
            position = None
        return MediaInfo(
            title=str(getattr(properties, "title", "") or ""),
            artist=str(getattr(properties, "artist", "") or ""),
            album=str(getattr(properties, "album_title", "") or ""),
            status=status,
            position_s=position,
        )

    def _publish(self, info: MediaInfo) -> None:
        with self._lock:
            changed = info != self._info and (
                info.track_key != self._info.track_key or info.status != self._info.status
            )
            self._info = info
        if changed and self._on_change is not None:
            try:
                self._on_change(info)
            except Exception:  # noqa: BLE001
                pass

    # ----- transport -----

    def command(self, name: str) -> str:
        """Run a transport command on the current player. Returns how."""
        loop = self._loop
        if loop is not None and self.available:
            future = asyncio.run_coroutine_threadsafe(self._command_async(name), loop)
            try:
                if future.result(timeout=2.0):
                    return "sessao de midia"
            except Exception:  # noqa: BLE001
                pass
        vk_code = _FALLBACK_KEYS.get(name)
        if vk_code is None:
            return "sem equivalente no Windows"
        self._key_sender(vk_code)
        return "tecla de midia"

    async def _command_async(self, name: str) -> bool:
        session = await self._session()
        if session is None:
            return False
        if name == "play":
            return bool(await session.try_play_async())
        if name == "pause":
            return bool(await session.try_pause_async())
        if name == "playpause":
            return bool(await session.try_toggle_play_pause_async())
        if name == "stop":
            return bool(await session.try_stop_async())
        if name == "next":
            return bool(await session.try_skip_next_async())
        if name == "previous":
            return bool(await session.try_skip_previous_async())
        if name == "fast_forward":
            return bool(await session.try_fast_forward_async())
        if name == "rewind":
            return bool(await session.try_rewind_async())
        if name == "shuffle":
            current = session.get_playback_info().is_shuffle_active
            return bool(await session.try_change_shuffle_active_async(not bool(current)))
        if name == "repeat":
            info = session.get_playback_info()
            mode = info.auto_repeat_mode
            next_mode = 0 if mode is None else (int(mode) + 1) % 3
            return bool(await session.try_change_auto_repeat_mode_async(next_mode))
        return False
