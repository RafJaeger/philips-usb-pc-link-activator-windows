from __future__ import annotations

import time
import unittest

from display_modes import (
    DisplayComposer,
    bounce_window,
    fit,
    scroll_window,
    type_window,
)
from pc_link_protocol import MediaInfo, PcLinkSession, text_frame


class FakeStats:
    def __init__(self, cpu=37, ram=62, down=1_234_000, up=120_000):
        self.cpu, self.ram, self.down, self.up = cpu, ram, down, up

    def sample(self):
        return self.cpu, self.ram

    def network(self):
        return self.down, self.up


SONG = MediaInfo(title="Andromeda", artist="Gorillaz", status="playing")
NOON = time.mktime((2026, 11, 17, 21, 45, 8, 0, 0, -1))


def composer(**kwargs) -> DisplayComposer:
    kwargs.setdefault("clock", lambda: NOON)
    kwargs.setdefault("stats", FakeStats())
    return DisplayComposer(**kwargs)


class EffectTests(unittest.TestCase):
    def test_fit_centers_short_text(self):
        self.assertEqual(fit("PC", 8), "   PC   ")
        self.assertEqual(fit("PAUSA", 8), " PAUSA  ")
        self.assertEqual(fit("ABCDEFGHIJ", 8), "ABCDEFGH")

    def test_scroll_holds_then_moves_and_wraps(self):
        text = "Andromeda - Gorillaz"
        self.assertEqual(scroll_window(text, 8, 0.0), "Andromed")
        self.assertEqual(scroll_window(text, 8, 1.5), "Andromed")
        self.assertEqual(scroll_window(text, 8, 1.7), "ndromeda")
        cycle = 1.6 + (len(text) + 3) * 0.33
        self.assertEqual(scroll_window(text, 8, cycle + 0.1), "Andromed")

    def test_bounce_reaches_the_end_and_comes_back(self):
        text = "Andromeda - Gorillaz"
        windows = {bounce_window(text, 8, t / 10) for t in range(0, 200)}
        self.assertIn("Andromed", windows)
        self.assertIn("Gorillaz", windows)
        self.assertTrue(all(len(w) == 8 for w in windows))

    def test_type_reveals_letters_then_scrolls(self):
        self.assertEqual(type_window("Andromeda - Gorillaz", 8, 0.0), "A       ")
        self.assertEqual(type_window("Andromeda - Gorillaz", 8, 0.3), "And     ")
        self.assertEqual(type_window("Andromeda - Gorillaz", 8, 1.0), "Andromed")
        self.assertEqual(type_window("PLAY", 8, 0.0), "  P     ")
        self.assertEqual(type_window("PLAY", 8, 5.0), "  PLAY  ")


class PageTests(unittest.TestCase):
    def test_pages_that_fit_stay_still_and_alternate(self):
        from display_modes import pages_window

        pages = ["Broken", "Gorillaz"]
        # The title stays 6 s, the artist 3.5 s.
        self.assertEqual({pages_window(pages, 8, t / 10, "scroll") for t in range(0, 60)}, {" Broken "})
        self.assertEqual(pages_window(pages, 8, 6.1, "scroll"), "Gorillaz")
        self.assertEqual(pages_window(pages, 8, 9.6, "scroll"), " Broken ")

    def test_long_page_scrolls_once_then_next(self):
        from display_modes import TITLE_TIMING, page_duration, pages_window

        pages = ["Andromeda (Remaster)", "Gorillaz"]
        first = page_duration(pages[0], 8, "scroll", TITLE_TIMING)
        self.assertEqual(pages_window(pages, 8, 2.4, "scroll"), "Andromed")  # held 2.5 s
        self.assertEqual(pages_window(pages, 8, first - 0.1, "scroll"), "emaster)")
        self.assertEqual(pages_window(pages, 8, first + 0.1, "scroll"), "Gorillaz")

    def test_single_page_that_fits_never_moves(self):
        from display_modes import pages_window

        self.assertEqual({pages_window(["Boa noite"[:8]], 8, t, "scroll") for t in range(50)}, {"Boa noit"})


class ModeTests(unittest.TestCase):
    def test_music_clock_and_pc(self):
        item = composer()
        self.assertEqual(item.window(0.0, SONG, 8), "Andromed")
        self.assertEqual(item.window(0.0, MediaInfo(title="Broken", artist="Gorillaz"), 8), " Broken ")
        item.set_mode("clock", 10.0, announce=False)
        self.assertEqual(item.window(10.0, SONG, 8), "21:45:08")
        item.set_mode("pc", 20.0, announce=False)
        self.assertEqual(item.window(21.0, SONG, 8), "CPU  37%")
        self.assertEqual(item.window(24.5, SONG, 8), "RAM  62%")
        self.assertEqual(item.window(21.0, SONG, 16), "CPU 37% RAM 62% ")

    def test_internet_mode_is_still(self):
        item = composer()
        item.set_mode("net", 0.0, announce=False)
        self.assertEqual(item.window(0.0, SONG, 8), "DL  1.2M")
        self.assertEqual(item.window(3.6, SONG, 8), "UP  120K")
        self.assertEqual(item.window(0.0, SONG, 16), "DL 1.2M UP 120K ")

    def test_long_notice_is_cut_not_scrolled(self):
        item = composer()
        item.notify("VOLUME MAXIMO ATINGIDO", 1.0, 5.0)
        self.assertEqual({item.window(1.0 + t / 2, SONG, 8) for t in range(8)}, {"VOLUME M"})

    def test_music_without_song_shows_clock(self):
        self.assertEqual(composer().window(0.0, MediaInfo(), 8), "21:45:08")

    def test_mode_change_announces_its_name(self):
        item = composer()
        self.assertEqual(item.next_mode(5.0), "clock")
        self.assertEqual(item.window(5.1, SONG, 8), "RELOGIO ")
        self.assertEqual(item.window(7.0, SONG, 8), "21:45:08")

    def test_text_mode_only_with_text_and_cycle_order(self):
        item = composer()
        self.assertEqual(item.available_modes(), ["music", "clock", "pc", "net", "auto"])
        item.set_mode("text", 0.0)
        self.assertEqual(item.mode, "music")
        item.set_free_text("Boa noite", 0.0)
        self.assertEqual(item.available_modes(), ["music", "clock", "pc", "net", "text", "auto"])
        item.set_mode("text", 0.0, announce=False)
        self.assertEqual(item.window(0.0, SONG, 8), "Boa noit")
        self.assertEqual(item.previous_mode(1.0), "net")
        item.set_free_text("", 2.0)
        self.assertNotIn("text", item.available_modes())

    def test_auto_rotates_music_clock_pc(self):
        item = composer()
        item.set_mode("auto", 0.0, announce=False)
        item.auto_seconds = 8.0
        seen = {item.window(t, SONG, 8)[:3] for t in (1.0, 9.0, 21.0, 28.0)}
        self.assertEqual(seen, {"And", "21:", "CPU", "DL "})

    def test_notices_appear_at_once_and_expire(self):
        item = composer()
        item.window(0.0, SONG, 8)
        item.notify("VOL 45%", 1.0, 1.8)
        self.assertEqual(item.window(1.0, SONG, 8), "VOL 45% ")
        self.assertEqual(item.window(3.0, SONG, 8), "Andromed")
        item.notices_enabled = False
        item.notify("MUDO", 4.0)
        self.assertEqual(item.window(4.0, SONG, 8), "Andromed")

    def test_play_pause_notices(self):
        item = composer()
        item.observe_media(SONG, 0.0)
        item.observe_media(MediaInfo(title="Andromeda", status="paused"), 1.0)
        self.assertEqual(item.window(1.1, SONG, 8), " PAUSA  ")
        item.observe_media(SONG, 5.0)
        self.assertEqual(item.window(5.1, SONG, 8), "  PLAY  ")


class FakeHandle:
    def __init__(self):
        self.writes = []

    def controlWrite(self, request_type, request, value, index, payload, *, timeout):
        self.writes.append((index, bytes(payload)))
        return len(payload)

    def controlRead(self, request_type, request, value, index, length, *, timeout):
        return bytes(length)

    def frames(self):
        return [payload for index, payload in self.writes if index == 0xA4F4]


class FakeHost:
    def __init__(self, media=SONG):
        self.info = media

    def media(self):
        return self.info

    def media_command(self, _name):
        pass

    def volume_step(self, _direction):
        pass

    def toggle_mute(self):
        pass

    def set_volume(self, _percent):
        pass

    def volume_state(self):
        return (50, False)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class SessionIntegrationTests(unittest.TestCase):
    def make(self):
        clock = Clock()
        item = composer()
        sess = PcLinkSession(FakeHost(), composer=item, monotonic=clock, sleep=lambda _s: None)
        sess.state.mailbox_ready = True
        sess.on_activation(mailbox_installed=True)
        return sess, item, clock

    def test_playlist_buttons_switch_display_mode(self):
        sess, item, clock = self.make()
        handle = FakeHandle()
        message = sess.handle_request(handle, bytes.fromhex("80 14"))
        self.assertIn("modo clock", message)
        self.assertEqual(item.mode, "clock")
        sess.handle_request(handle, bytes.fromhex("80 15"))
        self.assertEqual(item.mode, "music")
        sess.handle_request(handle, bytes.fromhex("80 15"))
        self.assertEqual(item.mode, "auto")

    def test_display_changes_are_announced_rate_limited(self):
        sess, item, clock = self.make()
        handle = FakeHandle()
        sess.poll_events(handle)  # refresh after activation
        self.assertEqual(handle.frames(), [bytes.fromhex("02 A1 02")])
        handle.writes.clear()
        item.notify("VOL 45%", clock.now)
        clock.now += 0.1
        sess.poll_events(handle)  # notice changed the window, but too soon
        self.assertEqual(handle.frames(), [])
        clock.now += 0.25
        sess.poll_events(handle)
        self.assertEqual(handle.frames(), [bytes.fromhex("02 A1 02")])
        handle.writes.clear()
        sess.handle_request(handle, bytes.fromhex("80 40"))
        self.assertEqual(handle.frames(), [text_frame(0x40, "VOL 45% ", exact=True)])

    def test_display_disabled_shows_idle_text(self):
        clock = Clock()
        sess = PcLinkSession(FakeHost(), composer=composer(), display_enabled=lambda: False, monotonic=clock)
        self.assertEqual(sess.current_display(), "PC LINK")


if __name__ == "__main__":
    unittest.main()


class NotificationDisplayTests(unittest.TestCase):
    def test_notification_pages_show_once_then_mode_resumes(self):
        from display_modes import notification_duration, notification_window

        pages = ["Discord", "Rafael enviou uma mensagem", "oi"]
        self.assertEqual(notification_window(pages, 8, 0.5), "Discord ")
        self.assertEqual(notification_window(pages, 8, 2.1), "Rafael e")
        self.assertEqual(notification_window(pages, 8, notification_duration(pages, 8) - 0.1), "   oi   ")
        item = composer()
        item.window(0.0, SONG, 8)
        item.notify_pages(pages, 1.0)
        self.assertEqual(item.window(1.1, SONG, 8), "Discord ")
        end = 1.0 + notification_duration(pages, 8)
        self.assertEqual(item.window(end + 0.1, SONG, 8), "Andromed")

    def test_notifications_are_independent_of_volume_notices(self):
        item = composer()
        item.window(0.0, SONG, 8)
        item.notices_enabled = False
        item.notify_pages(["Discord", "Rafael"], 1.0)
        self.assertEqual(item.window(1.1, SONG, 8), "Discord ")
