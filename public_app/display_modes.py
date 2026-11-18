"""What the stereo display shows: modes, effects and notices.

The unit shows the text it fetches with GET_PC_CURRENT_TRACK_NAME. A FWM589
shows 8 characters, accepts at most 16 per frame and follows about three
text changes per second, so everything here is built from short windows.

Modes (cycled with the stereo's PROG button or from the app):
    music  "Title - Artist" of the current Windows media session
    clock  HH:MM:SS
    pc     CPU and memory usage
    net    internet download and upload speed
    text   free text typed in the app (only offered when not empty)
    auto   music, clock and PC in turn

Only the music (and a long free text) scrolls. Clock, PC, internet, notices
and mode names are always still and centered, cut if they do not fit.

Notices (volume, mute, play/pause, mode names) appear immediately on top of
the current mode for a moment, then the mode resumes.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from pc_link_protocol import MAX_TEXT_CHARS, MediaInfo, display_line, display_text

MODES = ("music", "clock", "pc", "net", "text", "auto")
MODE_LABELS = {
    "music": "MUSICA",
    "clock": "RELOGIO",
    "pc": "PC",
    "net": "INTERNET",
    "text": "TEXTO",
    "auto": "AUTO",
}
EFFECTS = ("scroll", "bounce", "type")
# Names shown in the app.
MODE_NAMES = {
    "music": "Musica",
    "clock": "Relogio",
    "pc": "PC (CPU e RAM)",
    "net": "Internet (download e upload)",
    "text": "Texto livre",
    "auto": "Auto (alternar)",
}
EFFECT_NAMES = {"scroll": "Rolar", "bounce": "Quicar", "type": "Digitar"}

STEP_S = 0.33          # one character per step (the unit follows ~3/s)
HOLD_S = 1.6           # pause at the start of a scrolling line
TYPE_STEP_S = 0.12     # typewriter speed (limited by the unit anyway)
LABEL_S = 1.3          # mode name shown after a mode change
NOTICE_S = 2.0
AUTO_SECONDS = 8.0
GAP = "   "


def fit(text: str, width: int) -> str:
    """Center a short text in the display, trimming long ones."""
    text = text[:width]
    pad = width - len(text)
    return " " * (pad // 2) + text + " " * (pad - pad // 2)


def scroll_window(text: str, width: int, elapsed: float, *, step_s: float = STEP_S, hold_s: float = HOLD_S) -> str:
    """Marquee: hold at the start, then move one character per step and wrap."""
    if len(text) <= width:
        return fit(text, width)
    loop = text + GAP
    cycle = hold_s + len(loop) * step_s
    t = elapsed % cycle
    offset = 0 if t < hold_s else min(len(loop) - 1, int((t - hold_s) / step_s) + 1)
    return (loop + loop)[offset : offset + width]


def bounce_window(text: str, width: int, elapsed: float, *, step_s: float = STEP_S, hold_s: float = HOLD_S) -> str:
    """Ping-pong: slide to the end, hold, slide back, hold."""
    if len(text) <= width:
        return fit(text, width)
    span = len(text) - width
    leg = span * step_s
    cycle = 2 * hold_s + 2 * leg
    t = elapsed % cycle
    if t < hold_s:
        offset = 0
    elif t < hold_s + leg:
        offset = min(span, int((t - hold_s) / step_s) + 1)
    elif t < 2 * hold_s + leg:
        offset = span
    else:
        offset = max(0, span - int((t - 2 * hold_s - leg) / step_s) - 1)
    return text[offset : offset + width]


def type_window(text: str, width: int, elapsed: float, *, type_step_s: float = TYPE_STEP_S) -> str:
    """Typewriter intro, then the normal marquee for long lines."""
    visible = min(len(text), width)
    typed = int(elapsed / type_step_s) + 1
    if typed < visible:
        if len(text) > width:
            return text[:typed].ljust(width)
        # Short text: type it in its centered position.
        centered = fit(text, width)
        start = (width - len(text)) // 2
        return centered[: start + typed].ljust(width)
    if len(text) <= width:
        return fit(text, width)
    return scroll_window(text, width, elapsed - visible * type_step_s)


PAGE_S = 3.5          # a page that fits stays still this long
END_HOLD_S = 1.2      # pause at the end of a page that had to scroll
# The song title is the main information: it stays longer on the display.
TITLE_S = 6.0
TITLE_HOLD_S = 2.5
TITLE_END_S = 2.0


@dataclass(frozen=True)
class Timing:
    still_s: float = PAGE_S
    hold_s: float = HOLD_S
    end_s: float = END_HOLD_S


TITLE_TIMING = Timing(TITLE_S, TITLE_HOLD_S, TITLE_END_S)
PAGE_TIMING = Timing()


def page_duration(text: str, width: int, effect: str, timing: Timing = PAGE_TIMING) -> float:
    if len(text) <= width:
        return timing.still_s + (min(len(text), width) * TYPE_STEP_S if effect == "type" else 0.0)
    span = len(text) - width
    duration = timing.hold_s + span * STEP_S + timing.end_s
    if effect == "bounce":
        duration += span * STEP_S + timing.hold_s
    if effect == "type":
        duration += width * TYPE_STEP_S
    return duration


def page_window(text: str, width: int, elapsed: float, effect: str, timing: Timing = PAGE_TIMING) -> str:
    """One page: still when it fits, otherwise scrolled once to its end."""
    if effect == "type":
        typed_s = min(len(text), width) * TYPE_STEP_S
        if elapsed < typed_s:
            return type_window(text, width, elapsed)
        elapsed -= typed_s
    if len(text) <= width:
        return fit(text, width)
    if effect == "bounce":
        return bounce_window(text, width, elapsed, hold_s=timing.hold_s)
    span = len(text) - width
    if elapsed < timing.hold_s:
        offset = 0
    else:
        offset = min(span, int((elapsed - timing.hold_s) / STEP_S) + 1)
    return text[offset : offset + width]


def _timings(pages: list[str]) -> list[Timing]:
    # The first page (song title) gets the longer timing.
    return [TITLE_TIMING if index == 0 else PAGE_TIMING for index in range(len(pages))]


def pages_duration(pages: list[str], width: int, effect: str) -> float:
    pages = [page for page in pages if page]
    return sum(page_duration(p, width, effect, t) for p, t in zip(pages, _timings(pages)))


def pages_window(pages: list[str], width: int, elapsed: float, effect: str) -> str:
    """Show the pages one after the other (title, artist...), then repeat."""
    pages = [page for page in pages if page]
    if not pages:
        return fit("", width)
    if len(pages) == 1 and len(pages[0]) <= width:
        return fit(pages[0], width) if effect != "type" else page_window(pages[0], width, elapsed, effect)
    timings = _timings(pages)
    durations = [page_duration(page, width, effect, timing) for page, timing in zip(pages, timings)]
    t = elapsed % sum(durations)
    for page, duration, timing in zip(pages, durations, timings):
        if t < duration:
            return page_window(page, width, t, effect, timing)
        t -= duration
    return page_window(pages[-1], width, 0.0, effect)


APP_NAME_S = 2.0  # notification: how long the app name stays


def _notification_timings(pages: list[str]) -> list[Timing]:
    return [Timing(APP_NAME_S, HOLD_S, END_HOLD_S) if i == 0 else PAGE_TIMING for i in range(len(pages))]


def notification_duration(pages: list[str], width: int) -> float:
    return sum(page_duration(p, width, "scroll", t) for p, t in zip(pages, _notification_timings(pages)))


def notification_window(pages: list[str], width: int, elapsed: float) -> str:
    """Pages shown once (no repeat): app name, then title, then message."""
    for page, timing in zip(pages, _notification_timings(pages)):
        duration = page_duration(page, width, "scroll", timing)
        if elapsed < duration:
            return page_window(page, width, elapsed, "scroll", timing)
        elapsed -= duration
    last = pages[-1]
    return page_window(last, width, page_duration(last, width, "scroll"), "scroll")


def format_rate(bytes_per_s: float) -> str:
    """Network speed in 4 characters: 850K, 1.2M, 12M (bytes per second)."""
    value = max(0.0, float(bytes_per_s))
    if value < 1000:
        return f"{value / 1000:.1f}K" if value >= 100 else "0K"
    if value < 1_000_000:
        return f"{value / 1000:.0f}K"
    if value < 10_000_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{value / 1_000_000:.0f}M"


class PcStats:
    """CPU/RAM/network usage sampled at most once per second (never blocks)."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._next = 0.0
        self.cpu = 0
        self.ram = 0
        self.down = 0.0
        self.up = 0.0
        self._net_last: tuple[float, int, int] | None = None
        try:
            import psutil

            self._psutil = psutil
            psutil.cpu_percent(interval=None)
        except Exception:  # noqa: BLE001
            self._psutil = None

    def sample(self) -> tuple[int, int]:
        now = self._monotonic()
        if self._psutil is not None and now >= self._next:
            self._next = now + 1.0
            try:
                self.cpu = round(self._psutil.cpu_percent(interval=None))
                self.ram = round(self._psutil.virtual_memory().percent)
                counters = self._psutil.net_io_counters()
                sample = (now, int(counters.bytes_recv), int(counters.bytes_sent))
                if self._net_last is not None and now > self._net_last[0]:
                    span = now - self._net_last[0]
                    self.down = max(0, sample[1] - self._net_last[1]) / span
                    self.up = max(0, sample[2] - self._net_last[2]) / span
                self._net_last = sample
            except Exception:  # noqa: BLE001
                pass
        return self.cpu, self.ram

    def network(self) -> tuple[float, float]:
        self.sample()
        return self.down, self.up


@dataclass
class _Notice:
    text: str
    until: float
    started: float
    pages: tuple[str, ...] = ()


class DisplayComposer:
    """Thread-safe source of the display window for the PC Link session."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        stats: PcStats | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self._stats = stats or PcStats()
        self.mode = "music"
        self.effect = "scroll"
        self.free_text = ""
        self.auto_seconds = AUTO_SECONDS
        self.notices_enabled = True
        self._notice: _Notice | None = None
        self._content_key: str | None = None
        self._content_started = 0.0
        self._last_status: str | None = None
        self.on_mode_change: Callable[[str], None] | None = None

    # ----- configuration (GUI thread) -----

    def available_modes(self) -> list[str]:
        modes = ["music", "clock", "pc", "net"]
        if display_text(self.free_text, 80):
            modes.append("text")
        modes.append("auto")
        return modes

    def set_mode(self, mode: str, now: float, *, announce: bool = True) -> None:
        with self._lock:
            if mode not in MODES:
                mode = "music"
            if mode == "text" and not display_text(self.free_text, 80):
                mode = "music"
            changed = mode != self.mode
            self.mode = mode
            self._content_key = None
            if announce:
                self._show(MODE_LABELS[mode], now, LABEL_S, force=True)
            else:
                self._notice = None
        if changed and self.on_mode_change is not None:
            try:
                self.on_mode_change(mode)
            except Exception:  # noqa: BLE001
                pass

    def next_mode(self, now: float) -> str:
        """PROG: go to the next available mode and announce it."""
        with self._lock:
            modes = self.available_modes()
            index = modes.index(self.mode) if self.mode in modes else -1
            mode = modes[(index + 1) % len(modes)]
        self.set_mode(mode, now)
        return mode

    def previous_mode(self, now: float) -> str:
        with self._lock:
            modes = self.available_modes()
            index = modes.index(self.mode) if self.mode in modes else 0
            mode = modes[(index - 1) % len(modes)]
        self.set_mode(mode, now)
        return mode

    def set_effect(self, effect: str) -> None:
        with self._lock:
            self.effect = effect if effect in EFFECTS else "scroll"
            self._content_key = None

    def set_free_text(self, text: str, now: float) -> None:
        with self._lock:
            self.free_text = text or ""
            if self.mode == "text" and not display_text(self.free_text, 80):
                self.mode = "music"
            self._content_key = None

    # ----- notices (any thread) -----

    def notify(self, text: str, now: float, seconds: float = NOTICE_S) -> None:
        if self.notices_enabled:
            self._show(text, now, seconds)

    def notify_pages(self, pages: list[str], now: float) -> None:
        """A Windows notification: app name (still), then title/message pages.

        Shown at once on top of the current mode; each page stays still when
        it fits and scrolls once when it does not.
        """
        # Has its own option in the app; not tied to the volume/pause notices.
        clean = [display_text(page, 80) for page in pages]
        clean = [page for page in clean if page]
        if not clean:
            return
        with self._lock:
            duration = notification_duration(clean, self.width)
            self._notice = _Notice(clean[0].upper(), now + duration, now, tuple(clean))

    def _show(self, text: str, now: float, seconds: float, *, force: bool = False) -> None:
        with self._lock:
            clean = display_text(text, 80).upper()
            if clean:
                self._notice = _Notice(clean, now + seconds, now)

    def observe_media(self, media: MediaInfo, now: float) -> None:
        """Play/pause notices when the Windows player changes state."""
        with self._lock:
            status = media.status
            previous, self._last_status = self._last_status, status
        if previous is None or previous == status:
            return
        if status == "paused":
            self.notify("PAUSA", now, 1.6)
        elif status == "playing" and previous in ("paused", "stopped"):
            self.notify("PLAY", now, 1.2)

    # ----- window (USB thread) -----

    def _content(self, media: MediaInfo, now: float) -> tuple[str, str, bool]:
        """(key, text, live) for the current mode; live texts are not re-typed."""
        mode = self.mode
        if mode == "auto":
            # The music slot lasts until title and artist were fully shown.
            slots: list[tuple[str, float]] = []
            if media.title or media.artist:
                music_pages = [display_text(media.title, 80), display_text(media.artist, 80)]
                slots.append(("music", max(self.auto_seconds, pages_duration(music_pages, self.width, self.effect))))
            slots += [(name, max(2.0, self.auto_seconds)) for name in ("clock", "pc", "net")]
            t = now % sum(duration for _name, duration in slots)
            mode = slots[-1][0]
            for name, duration in slots:
                if t < duration:
                    mode = name
                    break
                t -= duration
        if mode == "music":
            title = display_text(media.title, 80)
            artist = display_text(media.artist, 80)
            if title or artist:
                # Title and artist are separate pages: each one stays still
                # when it fits and scrolls once when it does not.
                return f"music:{title}|{artist}", [title, artist], False
            mode = "clock"
        if mode == "clock":
            stamp = time.localtime(self._clock())
            return "clock", [time.strftime("%H:%M:%S", stamp)], True
        if mode == "pc":
            cpu, ram = self._stats.sample()
            if self.width >= 16:
                return "pc", [f"CPU {cpu:2d}% RAM {ram:2d}%"], True
            show_ram = int(now / PAGE_S) % 2 == 1
            return "pc", [f"RAM {ram:3d}%" if show_ram else f"CPU {cpu:3d}%"], True
        if mode == "net":
            down, up = self._stats.network()
            if self.width >= 16:
                return "net", [f"DL {format_rate(down)} UP {format_rate(up)}"], True
            show_up = int(now / PAGE_S) % 2 == 1
            return "net", [f"UP {format_rate(up):>5}" if show_up else f"DL {format_rate(down):>5}"], True
        if mode == "text":
            text = display_text(self.free_text, 80)
            if text:
                return f"text:{text}", [text], False
        return "idle", ["PC LINK"], False

    width = 8

    def window(self, now: float, media: MediaInfo, width: int) -> str:
        with self._lock:
            self.width = max(1, min(MAX_TEXT_CHARS, int(width)))
            notice = self._notice
            if notice is not None:
                if now < notice.until:
                    # Notices and mode names never scroll: still and centered.
                    if notice.pages:
                        return notification_window(list(notice.pages), self.width, now - notice.started)
                    return fit(notice.text, self.width)
                self._notice = None
                self._content_key = None  # resume with the intro effect
            key, pages, live = self._content(media, now)
            if key != self._content_key:
                self._content_key = key
                self._content_started = now
            elapsed = now - self._content_started
            if live:
                return fit(pages[0], self.width)
            return pages_window(pages, self.width, elapsed, self.effect)
