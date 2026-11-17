from __future__ import annotations

import unittest

import philips_pc_link_core as core
from media_session import MediaMonitor
from pc_link_protocol import (
    DisplayScroller,
    GET_DATA_REPLY,
    MediaInfo,
    PcLinkSession,
    display_text,
    is_pc_link_request,
    text_frame,
)
from sound_agent import LEGACY_MCM_SETUP


class FakeHandle:
    """Chip memory model: A544 is cleared by the unit after each frame."""

    def __init__(self, *, mailbox: bytes = b"\x00\x00\x00", memory: dict[int, bytes] | None = None, ack: bool = True):
        self.writes: list[tuple[int, bytes]] = []
        self.reads: list[tuple[int, int]] = []
        self.mailbox = mailbox
        self.memory = dict(memory or {})
        self.ack = ack

    def controlWrite(self, request_type, request, value, index, payload, *, timeout):
        assert (request_type, request, value) == (0x40, 0x04, 0)
        self.writes.append((index, bytes(payload)))
        self.memory[index] = bytes(payload)
        return len(payload)

    def controlRead(self, request_type, request, value, index, length, *, timeout):
        assert (request_type, request, value) == (0xC0, 0x05, 0)
        self.reads.append((index, length))
        if index == 0xA544:
            return b"\x00" if self.ack else b"\x02"
        if index == 0xA4EE:
            return self.mailbox[:1]
        if index == 0xA4C1:
            return self.mailbox[1 : 1 + length].ljust(length, b"\x00")
        if index in self.memory:
            return self.memory[index][:length].ljust(length, b"\x00")
        return bytes(length)

    def frames(self) -> list[bytes]:
        return [payload for index, payload in self.writes if index == 0xA4F4]


class FakeHost:
    def __init__(self, media: MediaInfo | None = None):
        self.info = media or MediaInfo(title="Água de Março", artist="Elis & Tom", album='"Live"', status="playing", position_s=3725)
        self.calls: list[tuple] = []
        self.volume = (40, False)

    def media(self):
        return self.info

    def media_command(self, name):
        self.calls.append(("media", name))

    def volume_step(self, direction):
        self.calls.append(("step", direction))

    def toggle_mute(self):
        self.calls.append(("mute",))

    def set_volume(self, percent):
        self.calls.append(("set", percent))

    def volume_state(self):
        return self.volume


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def session(host=None, **kwargs) -> PcLinkSession:
    kwargs.setdefault("sleep", lambda _s: None)
    item = PcLinkSession(host or FakeHost(), **kwargs)
    item.state.mailbox_ready = True
    return item


class TextTests(unittest.TestCase):
    def test_display_text_is_ascii_without_quotes_and_truncated(self):
        self.assertEqual(display_text('Água "de" Março'), "Agua de Marco")
        self.assertEqual(len(display_text("x" * 80)), 37)
        self.assertEqual(display_text("  a \n b "), "a b")
        self.assertEqual(display_text(None), "")

    def test_text_frame_matches_philipsremote_format(self):
        self.assertEqual(text_frame(0x40, "Song"), bytes((6, 0x40, 0x82)) + b"Song")
        self.assertEqual(text_frame(0x41, ""), bytes((2, 0x41, 0x81)))
        frame = text_frame(0x40, "y" * 80)
        self.assertEqual(len(frame), 40)
        self.assertEqual(frame[0] + 1, len(frame))

    def test_request_detection_excludes_sound_agent_and_status(self):
        self.assertTrue(is_pc_link_request(bytes.fromhex("80 4a")))
        self.assertTrue(is_pc_link_request(bytes.fromhex("80 06")))
        self.assertFalse(is_pc_link_request(bytes.fromhex("80 6b")))
        self.assertFalse(is_pc_link_request(bytes.fromhex("80 80")))
        self.assertFalse(is_pc_link_request(bytes.fromhex("04 00")))


class RequestTests(unittest.TestCase):
    def test_player_status_poll_is_answered_through_a544(self):
        handle = FakeHandle()
        result = session().handle_request(handle, bytes.fromhex("80 4a"))
        self.assertIn("respondido com 02 4a 82", result)
        self.assertEqual(handle.writes, [(0xA4F4, bytes.fromhex("02 4A 82")), (0xA544, b"\x02")])

    def test_title_artist_album_requests(self):
        handle = FakeHandle()
        item = session()
        for report in ("80 40", "80 41", "80 42"):
            item.handle_request(handle, bytes.fromhex(report))
        self.assertEqual(
            handle.frames(),
            [text_frame(0x40, "Agua de ", exact=True), text_frame(0x41, "Elis & Tom"), text_frame(0x42, "Live")],
        )

    def test_cursor_text_is_the_display_line(self):
        # Confirmed on a real FWM589: the unit shows this text on its display.
        handle = FakeHandle()
        item = session(FakeHost(MediaInfo(title="19-2000", artist="Gorillaz", status="playing")))
        item.handle_request(handle, bytes.fromhex("80 52"))
        self.assertEqual(handle.frames(), [text_frame(0x52, "19-2000 ", exact=True)])
        handle = FakeHandle()
        session(FakeHost(MediaInfo())).handle_request(handle, bytes.fromhex("80 52"))
        self.assertEqual(handle.frames(), [bytes.fromhex("02 52 81")])

    def test_player_state_query_returns_last_event_values(self):
        host = FakeHost(MediaInfo(title="A", status="playing"))
        item = session(host)
        item.state.event_values[0xA8] = 0x03
        handle = FakeHandle(mailbox=bytes((0x51, 0xA8)))
        item.handle_request(handle, bytes.fromhex("80 51"))
        handle.mailbox = bytes((0x51, 0xA2))
        item.handle_request(handle, bytes.fromhex("80 51"))
        handle.mailbox = bytes((0x51, 0xA3))
        item.handle_request(handle, bytes.fromhex("80 51"))
        self.assertEqual(
            handle.frames(),
            [bytes.fromhex("02 A8 03"), bytes.fromhex("02 A2 01"), bytes.fromhex("02 A3 00")],
        )

    def test_title_hidden_when_display_disabled(self):
        handle = FakeHandle()
        session(display_enabled=lambda: False).handle_request(handle, bytes.fromhex("80 40"))
        self.assertEqual(handle.frames(), [bytes.fromhex("02 40 81")])

    def test_transport_and_volume_commands_reach_windows(self):
        host = FakeHost()
        handle = FakeHandle()
        item = session(host)
        for report in ("80 01", "80 0a", "80 03", "80 04", "80 02", "80 06", "80 07", "80 05"):
            item.handle_request(handle, bytes.fromhex(report))
        self.assertEqual(
            host.calls,
            [
                ("media", "play"),
                ("media", "pause"),
                ("media", "next"),
                ("media", "previous"),
                ("media", "stop"),
                ("step", 1),
                ("step", -1),
                ("mute",),
            ],
        )
        self.assertEqual(handle.writes, [])

    def test_absolute_volume_reads_mailbox(self):
        host = FakeHost()
        handle = FakeHandle(mailbox=bytes((0x08, 0x00, 0x80)))
        session(host).handle_request(handle, bytes.fromhex("80 08"))
        self.assertEqual(handle.reads, [(0xA4EE, 1), (0xA4C1, 2)])
        self.assertEqual(host.calls, [("set", 50)])

    def test_absolute_volume_ignores_mode_one_and_foreign_mailbox(self):
        host = FakeHost()
        session(host).handle_request(FakeHandle(mailbox=bytes((0x08, 0x01, 0xFF))), bytes.fromhex("80 08"))
        session(host).handle_request(FakeHandle(mailbox=bytes((0x40, 0x00, 0xFF))), bytes.fromhex("80 08"))
        self.assertEqual(host.calls, [])

    def test_uac_volume_control_writes_a4f3(self):
        handle = FakeHandle(mailbox=bytes((0x22, 0x01)))
        item = session()
        item.handle_request(handle, bytes.fromhex("80 22"))
        self.assertEqual(handle.writes, [(0xA4F3, b"\x01")])
        self.assertTrue(item.state.uac_volume_control)
        handle = FakeHandle(mailbox=bytes((0x22, 0x00)))
        item.handle_request(handle, bytes.fromhex("80 22"))
        self.assertEqual(handle.writes, [(0xA4F3, b"\x00")])

    def test_set_pc_events_bit0(self):
        item = session()
        item.handle_request(FakeHandle(mailbox=bytes((0x23, 0x00))), bytes.fromhex("80 23"))
        self.assertFalse(item.state.events_enabled)
        item.handle_request(FakeHandle(mailbox=bytes((0x23, 0x01))), bytes.fromhex("80 23"))
        self.assertTrue(item.state.events_enabled)

    def test_volume_status_playtime_version_and_fixed_replies(self):
        handle = FakeHandle(memory={0x1C4B: bytes.fromhex("01 02 03 04")})
        item = session()
        for report in ("80 4c", "80 47", "80 4b", "80 48", "80 4d", "80 4e", "80 4f", "80 43"):  # noqa: E501
            item.handle_request(handle, bytes.fromhex(report))
        self.assertEqual(
            handle.frames(),
            [
                bytes.fromhex("04 4C 82 00 66"),
                bytes.fromhex("05 47 82 01 02 05"),
                bytes.fromhex("02 4B 83"),
                bytes.fromhex("03 48 81 00"),
                bytes.fromhex("06 4D 82 08 00 01 19"),
                text_frame(0x4E, "Windows"),
                bytes.fromhex("06 4F 82 01 02 03 04"),
                bytes.fromhex("02 43 81"),
            ],
        )

    def test_muted_volume_reports_zero(self):
        host = FakeHost()
        host.volume = (70, True)
        handle = FakeHandle()
        session(host).handle_request(handle, bytes.fromhex("80 4c"))
        self.assertEqual(handle.frames(), [bytes.fromhex("04 4C 82 00 00")])

    def test_get_data_reproduces_original_frame(self):
        handle = FakeHandle()
        session().handle_request(handle, bytes.fromhex("80 50"))
        self.assertEqual(handle.frames(), [GET_DATA_REPLY])
        self.assertEqual(len(GET_DATA_REPLY), 0x28)
        self.assertIn((0xA544, b"\x29"), handle.writes)

    def test_mailbox_is_installed_once_before_first_reply(self):
        calls = []
        item = PcLinkSession(
            FakeHost(),
            ensure_mailbox=lambda handle: calls.append(handle) or ["Setup Philips: x"],
            sleep=lambda _s: None,
        )
        handle = FakeHandle()
        item.handle_request(handle, bytes.fromhex("80 4a"))
        item.handle_request(handle, bytes.fromhex("80 4a"))
        self.assertEqual(calls, [handle])
        notes = item.take_notes()
        self.assertIn("Setup Philips: x", notes)
        self.assertEqual(item.take_notes(), [])

    def test_repeated_missing_ack_pauses_replies(self):
        clock = Clock()

        def monotonic():
            clock.now += 0.5
            return clock.now

        item = session(monotonic=monotonic, ready_timeout_s=0.1)
        handle = FakeHandle(ack=False)
        messages = [item.handle_request(handle, bytes.fromhex("80 4a")) for _ in range(3)]
        self.assertIn("pausadas", messages[-1])
        self.assertTrue(item.paused)
        writes = len(handle.writes)
        self.assertIsNone(item.handle_request(handle, bytes.fromhex("80 4a")))
        self.assertEqual(len(handle.writes), writes)


class EventTests(unittest.TestCase):
    def test_no_events_before_the_unit_speaks_pc_link(self):
        handle = FakeHandle()
        self.assertEqual(session().poll_events(handle), [])
        self.assertEqual(handle.writes, [])

    def test_track_and_state_changes_notify_the_unit(self):
        host = FakeHost(MediaInfo(title="One", artist="A", status="playing"))
        handle = FakeHandle()
        item = session(host)
        item.handle_request(handle, bytes.fromhex("80 4a"))
        handle.writes.clear()
        self.assertEqual(item.poll_events(handle), [])  # first snapshot is the baseline

        host.info = MediaInfo(title="Two", artist="B", status="playing")
        messages = item.poll_events(handle)
        self.assertEqual(handle.frames(), [bytes.fromhex("02 A1 02")])
        self.assertIn("Two - B", messages[0])
        self.assertEqual(item.state.event_values[0xA1], 0x02)

        handle.writes.clear()
        host.info = MediaInfo(title="Two", artist="B", status="paused")
        item.poll_events(handle)
        self.assertEqual(handle.frames(), [bytes.fromhex("02 A2 04")])
        self.assertEqual(item.poll_events(handle), [])

    def test_events_respect_set_pc_events_and_display_option(self):
        host = FakeHost(MediaInfo(title="One"))
        show = {"value": True}
        item = session(host, display_enabled=lambda: show["value"])
        handle = FakeHandle(mailbox=bytes((0x23, 0x00)))
        item.handle_request(handle, bytes.fromhex("80 23"))
        item.state.last_track_key = ("Old", "", "")
        self.assertEqual(item.poll_events(handle), [])

        item.state.events_enabled = True
        show["value"] = False
        handle.writes.clear()
        item.poll_events(handle)
        self.assertEqual(handle.frames(), [])


class DisplayTests(unittest.TestCase):
    def test_short_text_is_static(self):
        scroller = DisplayScroller()
        scroller.set_text("Song", 0.0)
        self.assertEqual(scroller.window(), "Song")
        self.assertFalse(scroller.tick(100.0))

    def test_long_text_scrolls_with_hold_and_wraps(self):
        scroller = DisplayScroller(step_s=0.5, hold_s=2.0, gap="   ")
        scroller.set_text("Andromeda - Gorillaz", 0.0)
        self.assertEqual(scroller.window(), "Andromed")
        self.assertFalse(scroller.tick(1.0))  # holding the start
        self.assertTrue(scroller.tick(2.0))
        self.assertEqual(scroller.window(), "ndromeda")
        now = 2.0
        for _ in range(len("Andromeda - Gorillaz") + 3 - 1):
            now += 0.5
            self.assertTrue(scroller.tick(now))
        self.assertEqual(scroller.window(), "Andromed")
        self.assertFalse(scroller.tick(now + 0.5))  # hold again at the start

    def test_marquee_steps_ask_unit_to_refetch(self):
        clock = Clock()
        host = FakeHost(MediaInfo(title="Andromeda", artist="Gorillaz", status="playing"))
        item = session(host, monotonic=clock)
        handle = FakeHandle()
        item.on_activation(mailbox_installed=True)
        item.poll_events(handle)  # refresh after activation
        self.assertEqual(handle.frames(), [bytes.fromhex("02 A1 02")])
        handle.writes.clear()
        clock.now += 0.3
        self.assertEqual(item.poll_events(handle), [])
        self.assertEqual(handle.frames(), [])  # still holding
        clock.now += 2.0
        self.assertEqual(item.poll_events(handle), [])  # steps are not logged
        self.assertEqual(handle.frames(), [bytes.fromhex("02 A1 02")])
        handle.writes.clear()
        item.handle_request(handle, bytes.fromhex("80 40"))
        self.assertEqual(handle.frames(), [text_frame(0x40, "ndromeda", exact=True)])

    def test_exact_frame_keeps_spaces(self):
        self.assertEqual(text_frame(0x40, " Gorilla", exact=True), bytes((10, 0x40, 0x82)) + b" Gorilla")
        self.assertEqual(text_frame(0x40, "", exact=True), bytes((2, 0x40, 0x81)))

    def test_width_setting_changes_window(self):
        width = {"value": 8}
        item = session(FakeHost(MediaInfo(title="Andromeda", artist="Gorillaz")), display_width=lambda: width["value"])
        self.assertEqual(item.current_display(), "Andromed")
        width["value"] = 37
        self.assertEqual(item.current_display(), "Andromeda - Gorillaz")


class ListenerTests(unittest.TestCase):
    def test_listener_answers_pc_link_and_rate_limits_polls(self):
        events = []
        item = session()
        listener = core.RadioButtonListener(lambda name, raw: events.append(name), lambda _m: None, pc_link=item)
        handle = FakeHandle()
        for _ in range(5):
            self.assertTrue(listener._dispatch_pc_link(handle, bytes.fromhex("80 4a")))
        self.assertEqual(len([e for e in events if "0x4A" in e]), 1)
        self.assertEqual(len(handle.frames()), 5)

    def test_buttons_disabled_ignores_transport_but_answers_queries(self):
        host = FakeHost()
        listener = core.RadioButtonListener(lambda *_a: None, lambda _m: None, pc_link=session(host))
        listener.set_buttons_enabled(False)
        handle = FakeHandle()
        self.assertTrue(listener._dispatch_pc_link(handle, bytes.fromhex("80 06")))
        self.assertTrue(listener._dispatch_pc_link(handle, bytes.fromhex("80 40")))
        self.assertEqual(host.calls, [])
        self.assertEqual(len(handle.frames()), 1)


class MailboxSetupTests(unittest.TestCase):
    def test_setup_block_written_only_when_missing(self):
        handle = FakeHandle()
        lines = core.ensure_pc_link_mailbox(handle)
        self.assertEqual([write for write in handle.writes], list(LEGACY_MCM_SETUP))
        self.assertEqual(len(lines), len(LEGACY_MCM_SETUP))

        installed = FakeHandle(memory={0xA0DE: bytes.fromhex("56 A5"), 0xA556: LEGACY_MCM_SETUP[2][1]})
        self.assertEqual(core.ensure_pc_link_mailbox(installed), [])
        self.assertEqual(installed.writes, [])


class VolumeModeTests(unittest.TestCase):
    def tearDown(self):
        core.set_volume_mode("windows")

    def test_windows_mode_presses_volume_keys(self):
        pressed = []
        original = core.send_media_key
        core.send_media_key = pressed.append
        try:
            core.set_volume_mode("windows")
            host = core.WindowsPcLinkHost(None)
            host.volume_step(1)
            host.volume_step(-1)
            host.toggle_mute()
            core.handle_media_button(5, 0xAF, None)
        finally:
            core.send_media_key = original
        self.assertEqual(pressed, [0xAF, 0xAE, 0xAD, 0xAF])

    def test_invalid_mode_falls_back_to_windows(self):
        core.set_volume_mode("loud")
        self.assertEqual(core.get_volume_mode(), "windows")


class MediaMonitorTests(unittest.TestCase):
    def test_command_falls_back_to_media_keys_without_session_loop(self):
        pressed = []
        monitor = MediaMonitor(key_sender=pressed.append)
        self.assertEqual(monitor.command("next"), "tecla de midia")
        self.assertEqual(monitor.command("shuffle"), "sem equivalente no Windows")
        self.assertEqual(pressed, [0xB0])

    def test_change_callback_only_on_track_or_state(self):
        changes = []
        monitor = MediaMonitor(on_change=changes.append)
        monitor._publish(MediaInfo(title="A", status="playing", position_s=1))
        monitor._publish(MediaInfo(title="A", status="playing", position_s=2))
        monitor._publish(MediaInfo(title="B", status="playing", position_s=0))
        self.assertEqual([item.title for item in changes], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
