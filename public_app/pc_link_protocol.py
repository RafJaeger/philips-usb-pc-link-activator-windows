"""Philips USB PC Link "remote" protocol (title display, transport, volume).

Recovered from PhilipsRemote.exe 8.00.0119, the Philips component that the
MCM/FWM installation CD installs together with MusicMatch Jukebox, and from
uacb.dll, which carries its frames to 0471:0111 units.

Unit -> PC
    The control interface reports ``80 <command>``. Parameters, when a
    command has them, are read from the HID mailbox (A4EE + A4C1), exactly
    like UacbGetHidBuffer.

PC -> unit
    Replies and notifications use the same frame format and the same
    A4F4/A544 mailbox as the Sound Agent: ``[remaining bytes, command,
    0x82 ok / 0x81 failure, data...]``. When uacb.dll is loaded by
    PhilipsRemote.exe it converts that program's legacy A4F4/A4F2/B0A0
    writes into this mailbox, so for 0x0111 units this is the real wire
    format.

Commands 0x60-0x6F belong to the Sound Agent and are handled elsewhere.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from sound_agent import (
    read_hid_mailbox,
    read_memory,
    send_frame,
    write_memory,
)

OK = 0x82
FAIL = 0x81
MAX_TEXT_CHARS = 0x25  # PhilipsRemote truncates every name to 37 characters.

COMMAND_NAMES: dict[int, str] = {
    0x01: "SET_PC_PLAY",
    0x02: "SET_PC_STOP",
    0x03: "SET_PC_NEXT_TRACK",
    0x04: "SET_PC_PREVIOUS_TRACK",
    0x05: "SET_PC_MUTE_AUDIO",
    0x06: "SET_PC_VOLUME_UP",
    0x07: "SET_PC_VOLUME_DOWN",
    0x08: "SET_PC_VOLUME",
    0x09: "SET_PC_BALANCE",
    0x0A: "SET_PC_PAUSE",
    0x10: "SET_PC_SHUFFLE",
    0x11: "SET_PC_REPEAT",
    0x12: "SET_PC_SEARCH_FORWARD",
    0x13: "SET_PC_SEARCH_BACKWARD",
    0x14: "SET_PC_NEXT_PLAYLIST",
    0x15: "SET_PC_PREVIOUS_PLAYLIST",
    0x16: "SET_PC_CURSOR_NEXT",
    0x17: "SET_PC_CURSOR_PREVIOUS",
    0x18: "SET_PC_TRACK_NR",
    0x19: "SET_PC_RECORD_PREPARE",
    0x1A: "SET_PC_RECORD_START",
    0x1B: "SET_PC_LAUNCH_PLAYER_1",
    0x1C: "SET_PC_LAUNCH_PLAYER_2",
    0x1D: "SET_PC_INTERNET_RADIO",
    0x1E: "SET_CD_INFO",
    0x1F: "SET_AUDIO_MODEL_NR",
    0x20: "SET_AUDIO_MODEL_NAME",
    0x21: "SET_DATA",
    0x22: "SET_UAC_VOLUME_CONTROL",
    0x23: "SET_PC_EVENTS",
    0x25: "SET_PC_PLAYER_MODE",
    0x26: "SET_PC_CD_MODE",
    0x30: "SET_PC_TRACK_BY_ID",
    0x31: "SET_PC_PLAYLIST_BY_ID",
    0x40: "GET_PC_CURRENT_TRACK_NAME",
    0x41: "GET_PC_CURRENT_ARTIST_NAME",
    0x42: "GET_PC_CURRENT_ALBUM_NAME",
    0x43: "GET_PC_CDDB_TRACK_NAME",
    0x44: "GET_PC_CDDB_ARTIST_NAME",
    0x45: "GET_PC_CDDB_ALBUM_NAME",
    0x46: "GET_PC_CURRENT_PLAYLIST_NAME",
    0x47: "GET_PC_CURRENT_PLAYTIME",
    0x48: "GET_PC_CURRENT_TRACK_NR",
    0x49: "GET_PC_DRIVER_READY",
    0x4A: "GET_PC_PLAYER1_STATUS",
    0x4B: "GET_PC_PLAYER2_STATUS",
    0x4C: "GET_PC_VOLUME_STATUS",
    0x4D: "GET_PC_PLAYER_VERSION",
    0x4E: "GET_PC_PLAYER_NAME",
    0x4F: "GET_MODULE_SOFTWARE_VERSION",
    0x50: "GET_DATA",
    0x51: "GET_PC_PLAYER1_STATE",
    0x52: "GET_PC_CURSOR_TEXT",
    0x70: "SET_PC_SAY",
}

# Commands whose parameters PhilipsRemote reads with UacbGetHidBuffer(n).
PARAMETER_LENGTHS = {0x08: 3, 0x22: 2, 0x23: 2, 0x51: 2}

# Media transport requests forwarded to the Windows player.
TRANSPORT_COMMANDS = {
    0x01: "play",
    0x02: "stop",
    0x03: "next",
    0x04: "previous",
    0x0A: "pause",
    0x10: "shuffle",
    0x11: "repeat",
    0x12: "fast_forward",
    0x13: "rewind",
}

# Requests that PhilipsRemote answers with a fixed failure frame.
FIXED_FAILURES = {
    0x18: bytes((0x02, 0x18, FAIL)),
    0x19: bytes((0x02, 0x19, FAIL)),
    0x1A: bytes((0x02, 0x1A, FAIL)),
    0x1C: bytes((0x02, 0x1C, FAIL)),
    0x43: bytes((0x02, 0x43, FAIL)),
    0x44: bytes((0x02, 0x44, FAIL)),
    0x45: bytes((0x02, 0x45, FAIL)),
    0x46: bytes((0x02, 0x46, FAIL)),
    0x48: bytes((0x03, 0x48, FAIL, 0x00)),
}

# PC -> unit notifications (PhilipsRemote ThreadMachine events A0-AA).
EVENT_CHANGES = 0xA1        # bit mask of what changed
EVENT_PLAYER_STATE = 0xA2   # 0 stopped, 1 playing, 4 paused, 5 finished
# Verified on a real FWM589: "02 A1 02" makes the unit ask
# GET_PC_CURRENT_TRACK_NAME (0x40) again and show the answer. Bits 01, 04,
# 08, 10, 20 and 80 trigger no request on that unit.
CHANGE_TRACK_NAME = 0x02
TRACK_CHANGE_MASK = CHANGE_TRACK_NAME

# The FWM589 display shows 8 characters and does not scroll by itself.
DEFAULT_DISPLAY_WIDTH = 8
PLAYER_STATE_CODES = {"stopped": 0x00, "playing": 0x01, "paused": 0x04, "finished": 0x05}

# Version reported for GET_PC_PLAYER_VERSION, as BCD: the MusicMatch
# Jukebox build that shipped with PhilipsRemote.exe (8.00.0119).
PLAYER_VERSION_BCD = bytes((0x08, 0x00, 0x01, 0x19))
PLAYER_NAME = "Windows"

GET_DATA_REPLY = (
    bytes((0x29, 0x50, OK)) + b"random-data"
).ljust(0x28, b"\x00")


def is_pc_link_request(report: bytes) -> bool:
    raw = bytes(report)
    return len(raw) >= 2 and raw[0] == 0x80 and raw[1] in COMMAND_NAMES


def display_text(value: str | None, limit: int = MAX_TEXT_CHARS) -> str:
    """Convert a title for the unit's ASCII display (no accents, no quotes)."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.replace('"', "")
    text = " ".join(text.split())
    return text[:limit].rstrip()


def display_line(info: "MediaInfo") -> str:
    """Text for the unit's display (GET_PC_CURSOR_TEXT): 'Title - Artist'."""
    title = display_text(info.title)
    artist = display_text(info.artist)
    if title and artist:
        return display_text(f"{title} - {artist}")
    return title or artist


def text_frame(command: int, value: str | None, *, exact: bool = False) -> bytes:
    """Build PhilipsRemote's name reply: [n+2, command, 0x82, text].

    exact=True sends an already prepared display window as-is (spaces kept),
    so a scrolling marquee never jumps when a space reaches its edge.
    """
    if exact:
        text = (value or "").encode("ascii", "ignore")[:MAX_TEXT_CHARS]
        if not text.strip():
            text = b"" if not value else text
    else:
        text = display_text(value).encode("ascii")
    if not text:
        return bytes((0x02, command, FAIL))
    return bytes((len(text) + 2, command, OK)) + text


class DisplayScroller:
    """Scroll a long line through the unit's short display (marquee).

    The unit only shows the first ``width`` characters of the track name, so
    the PC sends a moving window and tells the unit to fetch it again.
    """

    def __init__(self, *, step_s: float = 0.45, hold_s: float = 1.6, gap: str = "   ") -> None:
        self.step_s = step_s
        self.hold_s = hold_s
        self.gap = gap
        self.width = DEFAULT_DISPLAY_WIDTH
        self.text = ""
        self.offset = 0
        self.next_step_at = 0.0

    def configure(self, width: int) -> bool:
        width = max(1, min(MAX_TEXT_CHARS, int(width)))
        if width == self.width:
            return False
        self.width = width
        self.offset = 0
        return True

    @property
    def scrolling(self) -> bool:
        return len(self.text) > self.width

    def set_text(self, text: str, now: float) -> bool:
        if text == self.text:
            return False
        self.text = text
        self.offset = 0
        self.next_step_at = now + self.hold_s
        return True

    def window(self) -> str:
        if not self.scrolling:
            return self.text
        loop = self.text + self.gap
        doubled = loop + loop
        return doubled[self.offset : self.offset + self.width]

    def tick(self, now: float) -> bool:
        """Advance one character when due. Returns True if the window moved."""
        if not self.scrolling or now < self.next_step_at:
            return False
        loop_len = len(self.text) + len(self.gap)
        self.offset = (self.offset + 1) % loop_len
        # Hold a little longer every time the start of the line comes back.
        self.next_step_at = now + (self.hold_s if self.offset == 0 else self.step_s)
        return True


def bcd(value: int) -> int:
    value = max(0, min(99, int(value)))
    return ((value // 10) << 4) | (value % 10)


@dataclass(frozen=True)
class MediaInfo:
    title: str = ""
    artist: str = ""
    album: str = ""
    status: str = "none"  # playing, paused, stopped or none
    position_s: float | None = None

    @property
    def track_key(self) -> tuple[str, str, str]:
        return (self.title, self.artist, self.album)


class PcLinkHost(Protocol):
    def media(self) -> MediaInfo | None: ...

    def media_command(self, name: str) -> None: ...

    def volume_step(self, direction: int) -> None: ...

    def toggle_mute(self) -> None: ...

    def set_volume(self, percent: int) -> None: ...

    def volume_state(self) -> tuple[int, bool] | None: ...


@dataclass
class PcLinkState:
    events_enabled: bool = True
    driver_ready: bool = False
    uac_volume_control: bool = False
    unit_active: bool = False
    mailbox_ready: bool = False
    refresh_pending: bool = False
    last_track_key: tuple[str, str, str] | None = None
    last_player_state: int | None = None
    # Last value sent with each PC event (A1-A8); GET_PC_PLAYER1_STATE reads it.
    event_values: dict[int, int] = field(default_factory=dict)
    failures: int = 0
    disabled_until: float = 0.0
    requests_seen: dict[int, int] = field(default_factory=dict)


class PcLinkSession:
    """Answer one unit's PC Link requests and push title/state changes."""

    def __init__(
        self,
        host: PcLinkHost,
        *,
        ensure_mailbox: Callable[[object], list[str]] | None = None,
        display_enabled: Callable[[], bool] = lambda: True,
        display_width: Callable[[], int] = lambda: DEFAULT_DISPLAY_WIDTH,
        timeout_ms: int = 1000,
        ready_timeout_s: float = 1.25,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.host = host
        self.state = PcLinkState()
        self._ensure_mailbox = ensure_mailbox
        self._display_enabled = display_enabled
        self._display_width = display_width
        self.display = DisplayScroller()
        self._timeout_ms = timeout_ms
        self._ready_timeout_s = ready_timeout_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._notes: list[str] = []

    # ----- helpers -----

    def reset(self, *, mailbox_ready: bool = False) -> None:
        self.state = PcLinkState(mailbox_ready=mailbox_ready)

    def on_activation(self, *, mailbox_installed: bool) -> None:
        """Called after every PC Link activation (also periodic refreshes).

        The MCM startup writes the setup block itself. FWM refreshes only
        resend A4EF/A4F0, so the mailbox state learned earlier is kept; a
        USB reconnection calls reset() instead.
        """
        if mailbox_installed:
            self.state.mailbox_ready = True
        self.state.failures = 0
        self.state.disabled_until = 0.0
        # A unit that already found the PC earlier does not poll again, so
        # announce ourselves like PhilipsRemote does on connect and make the
        # unit fetch the display text again.
        self.state.unit_active = True
        self.state.refresh_pending = True

    def take_notes(self) -> list[str]:
        notes, self._notes = self._notes, []
        return notes

    def _send(self, handle, packet: bytes, *, strict_length: bool = True) -> int:
        if not self.state.mailbox_ready and self._ensure_mailbox is not None:
            lines = self._ensure_mailbox(handle) or []
            if lines:
                self._notes.append(
                    "PC Link: canal de respostas instalado no aparelho (bloco original do driver Philips)."
                )
                self._notes.extend(lines)
        self.state.mailbox_ready = True
        polls = send_frame(
            handle,
            packet,
            timeout_ms=self._timeout_ms,
            ready_timeout_s=self._ready_timeout_s,
            sleep=self._sleep,
            monotonic=self._monotonic,
            strict_length=strict_length,
        )
        self.state.failures = 0
        return polls

    def _parameters(self, handle, command: int) -> bytes | None:
        length = PARAMETER_LENGTHS.get(command)
        if not length:
            return b""
        buffer = read_hid_mailbox(handle, length, timeout_ms=self._timeout_ms)
        # PhilipsRemote rejects a mailbox that echoes a different command.
        if buffer and buffer[0] not in (0x00, command):
            return None
        return buffer

    def _media(self) -> MediaInfo:
        try:
            info = self.host.media()
        except Exception:
            info = None
        return info or MediaInfo()

    def _note_failure(self, exc: Exception) -> str:
        self.state.failures += 1
        if self.state.failures >= 3:
            self.state.disabled_until = self._monotonic() + 30.0
            self.state.failures = 0
            return (
                f"PC Link: o aparelho nao confirmou respostas ({exc}); "
                "respostas pausadas por 30 segundos."
            )
        return f"PC Link: falha ao responder: {exc}"

    @property
    def paused(self) -> bool:
        return self._monotonic() < self.state.disabled_until

    # ----- unit -> PC -----

    def handle_request(self, handle, report: bytes) -> str | None:
        raw = bytes(report)
        if not is_pc_link_request(raw):
            return None
        command = raw[1]
        name = COMMAND_NAMES[command]
        self.state.unit_active = True
        self.state.requests_seen[command] = self.state.requests_seen.get(command, 0) + 1
        if self.paused:
            return None
        try:
            detail, packet, strict = self._dispatch(handle, command)
            if packet is None:
                return f"PC Link: {name} (0x{command:02X})" + (f" - {detail}" if detail else "")
            polls = self._send(handle, packet, strict_length=strict)
        except Exception as exc:  # noqa: BLE001 - keep the reader alive
            return self._note_failure(exc)
        return (
            f"PC Link: {name} (0x{command:02X}) respondido com {packet.hex(' ')}"
            f" em {polls} leitura(s)" + (f" - {detail}" if detail else "")
        )

    def _dispatch(self, handle, command: int) -> tuple[str, bytes | None, bool]:
        host = self.host
        if command in TRANSPORT_COMMANDS:
            action = TRANSPORT_COMMANDS[command]
            host.media_command(action)
            return action, None, True
        if command == 0x05:
            host.toggle_mute()
            return "mudo do Windows", None, True
        if command in (0x06, 0x07):
            host.volume_step(1 if command == 0x06 else -1)
            return "volume do Windows " + ("+" if command == 0x06 else "-"), None, True
        if command == 0x08:
            params = self._parameters(handle, command)
            if params is None or len(params) < 3 or params[1] == 0x01:
                shown = params.hex(" ") if params else "-"
                return f"parametro ignorado (mailbox {shown})", None, True
            percent = round(params[2] * 100 / 255)
            host.set_volume(percent)
            return f"volume do Windows em {percent}%", None, True
        if command == 0x22:
            params = self._parameters(handle, command)
            if params is None or len(params) < 2:
                return "parametro invalido", None, True
            enabled = params[1] > 0
            self.state.uac_volume_control = enabled
            write_memory(handle, 0xA4F3, b"\x01" if enabled else b"\x00", timeout_ms=self._timeout_ms)
            return f"A4F3={int(enabled)}", None, True
        if command == 0x23:
            params = self._parameters(handle, command)
            if params is None or len(params) < 2:
                self.state.events_enabled = True
                return "eventos do PC ligados (padrao)", None, True
            self.state.events_enabled = bool(params[1] & 0x01)
            return f"eventos do PC {'ligados' if self.state.events_enabled else 'desligados'} (0x{params[1]:02X})", None, True
        if command == 0x49:
            self.state.driver_ready = True
            return "PC pronto", None, True
        if command in FIXED_FAILURES:
            return "", FIXED_FAILURES[command], True

        media = self._media()
        show_text = self._display_enabled()
        if command == 0x40:
            # The display shows this field: send the current marquee window.
            window = self.current_display(media)
            return window, text_frame(0x40, window, exact=True), True
        if command == 0x41:
            return media.artist, text_frame(0x41, media.artist if show_text else ""), True
        if command == 0x42:
            return media.album, text_frame(0x42, media.album if show_text else ""), True
        if command == 0x47:
            if media.position_s is None or media.status == "none":
                return "", bytes((0x02, 0x47, FAIL)), True
            total = int(media.position_s)
            packet = bytes((0x05, 0x47, OK, bcd(total // 3600), bcd(total // 60 % 60), bcd(total % 60)))
            return "", packet, True
        if command == 0x4A:
            # PhilipsRemote answers 0x82 while the player answers "ok".
            return "player disponivel", bytes((0x02, 0x4A, OK)), True
        if command == 0x4B:
            return "", bytes((0x02, 0x4B, 0x83)), True
        if command == 0x4C:
            volume = None
            try:
                volume = host.volume_state()
            except Exception:
                volume = None
            if volume is None:
                return "", bytes((0x02, 0x4C, FAIL)), True
            percent, muted = volume
            level = 0 if muted else round(max(0, min(100, percent)) * 255 / 100)
            return f"{percent}%", bytes((0x04, 0x4C, OK, 0x00, level)), True
        if command == 0x4D:
            return "", bytes((0x06, 0x4D, OK)) + PLAYER_VERSION_BCD, True
        if command == 0x4E:
            return PLAYER_NAME, text_frame(0x4E, PLAYER_NAME), True
        if command == 0x4F:
            try:
                version = read_memory(handle, 0x1C4B, 4, timeout_ms=self._timeout_ms)
            except Exception:
                return "", bytes((0x02, 0x4F, FAIL)), True
            return version.hex(" "), bytes((0x06, 0x4F, OK)) + version, True
        if command == 0x50:
            return "", GET_DATA_REPLY, False
        if command == 0x51:
            # [51, event] -> [02, event, last value sent with that event].
            params = self._parameters(handle, command)
            if not params or len(params) < 2:
                return "parametro invalido", None, True
            index = params[1]
            if index == EVENT_PLAYER_STATE:
                value = PLAYER_STATE_CODES.get(media.status, 0x00)
            else:
                value = self.state.event_values.get(index, 0x00)
            return f"evento 0x{index:02X}", bytes((0x02, index, value)), True
        if command == 0x52:
            window = self.current_display(media)
            return window, text_frame(0x52, window, exact=True), True
        # Requests without a reply in PhilipsRemote (playlists, cursor,
        # record, model name, balance, say...). Consumed silently.
        return "sem resposta (igual ao original)", None, True

    # ----- PC -> unit -----

    def poll_events(self, handle) -> list[str]:
        """Notify the unit about title/state changes, like ThreadMachine."""
        if not self.state.unit_active or not self.state.events_enabled or self.paused:
            return []
        media = self._media()
        messages: list[str] = []
        player_state = PLAYER_STATE_CODES.get(media.status, 0x00)
        if self.state.last_player_state is None:
            self.state.last_player_state = player_state
        elif player_state != self.state.last_player_state:
            self.state.last_player_state = player_state
            messages.append(self._event(handle, bytes((0x02, EVENT_PLAYER_STATE, player_state)), f"estado {media.status}"))

        refresh = self.state.refresh_pending
        self.state.refresh_pending = False
        now = self._monotonic()
        width_changed = self.display.configure(self._display_width())
        line = display_line(media) if self._display_enabled() else ""
        text_changed = self.display.set_text(line, now)
        track_changed = media.track_key != self.state.last_track_key
        first_snapshot = self.state.last_track_key is None
        self.state.last_track_key = media.track_key
        if first_snapshot and not refresh:
            return [message for message in messages if message]

        if refresh or text_changed or width_changed or (track_changed and line):
            label = line or "(visor limpo)"
            messages.append(
                self._event(handle, bytes((0x02, EVENT_CHANGES, TRACK_CHANGE_MASK)), f"visor: {label}")
            )
        elif self.display.tick(now):
            # Marquee step: ask the unit to fetch the moved window. Only
            # failures are logged, steps happen several times per second.
            message = self._event(handle, bytes((0x02, EVENT_CHANGES, TRACK_CHANGE_MASK)), "rolagem")
            if "falha" in message or "pausadas" in message:
                messages.append(message)
        return [message for message in messages if message]

    def current_display(self, media: MediaInfo | None = None) -> str:
        """Window of the display line the unit should show right now."""
        info = media or self._media()
        self.display.configure(self._display_width())
        line = display_line(info) if self._display_enabled() else ""
        self.display.set_text(line, self._monotonic())
        return self.display.window()

    def _event(self, handle, packet: bytes, detail: str) -> str:
        try:
            polls = self._send(handle, packet)
        except Exception as exc:  # noqa: BLE001
            return self._note_failure(exc)
        self.state.event_values[packet[1]] = packet[2]
        return f"PC Link: aviso {packet.hex(' ')} enviado em {polls} leitura(s) - {detail}"
