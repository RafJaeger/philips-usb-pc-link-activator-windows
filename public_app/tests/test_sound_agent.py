from __future__ import annotations

import unittest

from sound_agent import (
    LEGACY_MCM_SETUP,
    SoundAgentError,
    SoundAgentState,
    _send_framed_packet,
    handle_mcm_notification,
    initialize_mcm_sound_agent,
    is_device_status_report,
)


class FakeHandle:
    def __init__(self, *, a544: list[int] | None = None, short_first_write: bool = False):
        self.writes: list[tuple[int, int, int, int, bytes, int]] = []
        self.reads: list[tuple[int, int, int, int, int, int]] = []
        self.a544 = list(a544 or [0])
        self.short_first_write = short_first_write

    def controlWrite(self, request_type, request, value, index, payload, *, timeout):
        payload = bytes(payload)
        self.writes.append((request_type, request, value, index, payload, timeout))
        if self.short_first_write and len(self.writes) == 1:
            return max(0, len(payload) - 1)
        return len(payload)

    def controlRead(self, request_type, request, value, index, length, *, timeout):
        self.reads.append((request_type, request, value, index, length, timeout))
        if index == 0xA4C1:
            return b"\x4a"
        if index == 0xA544:
            value = self.a544.pop(0) if len(self.a544) > 1 else self.a544[0]
            return bytes((value,))
        raise AssertionError(f"unexpected read 0x{index:04X}")


class AdvancingClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        self.now += 0.1
        return self.now


class SoundAgentTests(unittest.TestCase):
    def test_exact_mcm_startup_and_ack(self):
        handle = FakeHandle(a544=[2, 2, 0])
        trace = initialize_mcm_sound_agent(
            handle,
            revision=0x0402,
            sleep=lambda _seconds: None,
        )

        self.assertIn("revisao USB 0402", trace)
        self.assertIn("A544=0x00", trace)
        self.assertEqual(
            [(item[3], item[4]) for item in handle.writes[: len(LEGACY_MCM_SETUP)]],
            list(LEGACY_MCM_SETUP),
        )
        self.assertIn((0xB08C, b""), [(item[3], item[4]) for item in handle.writes])
        self.assertEqual(
            [(item[3], item[4]) for item in handle.writes[len(LEGACY_MCM_SETUP) :]],
            [
                (0xA4EF, b"\x01"),
                (0xA4F0, b"\x02"),
                (0xA4EF, b"\x01"),
                (0xA4F0, b"\x02"),
                (0xA4F4, b"\x02\xb0\x01"),
                (0xA544, b"\x02"),
                (0xA535, b"\x01"),
            ],
        )
        self.assertTrue(all(item[0:3] == (0x40, 0x04, 0) for item in handle.writes))
        self.assertTrue(all(item[0:3] == (0xC0, 0x05, 0) for item in handle.reads))

    def test_short_usb_write_is_failure(self):
        with self.assertRaisesRegex(SoundAgentError, "Transferencia incompleta"):
            initialize_mcm_sound_agent(
                FakeHandle(short_first_write=True),
                sleep=lambda _seconds: None,
            )

    def test_a544_always_receives_the_frame_length_byte(self):
        packets = (
            bytes.fromhex("02 B0 01"),
            bytes.fromhex("03 6B A1 01"),
            bytes.fromhex("06 6D 82 03 04 05 06"),
        )
        for packet in packets:
            with self.subTest(packet=packet.hex(" ")):
                handle = FakeHandle(a544=[0])
                _send_framed_packet(
                    handle,
                    packet,
                    timeout_ms=1000,
                    ready_timeout_s=1.0,
                    sleep=lambda _seconds: None,
                    monotonic=AdvancingClock().monotonic,
                )
                writes = [(item[3], item[4]) for item in handle.writes]
                self.assertEqual(
                    writes,
                    [(0xA4F4, packet), (0xA544, packet[0:1])],
                )

    def test_missing_device_ack_is_failure(self):
        clock = AdvancingClock()
        with self.assertRaisesRegex(SoundAgentError, "nao confirmou"):
            initialize_mcm_sound_agent(
                FakeHandle(a544=[2]),
                ready_timeout_s=0.15,
                sleep=lambda _seconds: None,
                monotonic=clock.monotonic,
            )

    def test_periodic_status_reports_are_not_buttons(self):
        # 80 4a is GET_PC_PLAYER1_STATUS (answered by pc_link_protocol).
        self.assertFalse(is_device_status_report(bytes.fromhex("80 4a")))
        self.assertTrue(is_device_status_report(bytes.fromhex("80 80")))
        self.assertFalse(is_device_status_report(bytes.fromhex("01 00")))

    def test_fixed_runtime_queries_receive_original_sound_agent_responses(self):
        expected = {
            "80 6b": [(0xA535, b"\x01"), (0xA4F4, bytes.fromhex("03 6B A1 01")), (0xA544, b"\x03")],
            "80 6c": [(0xA4F4, bytes.fromhex("02 6C 82")), (0xA544, b"\x02")],
            "80 6d": [(0xA4F4, bytes.fromhex("06 6D 82 03 04 05 06")), (0xA544, b"\x06")],
        }
        for report, writes in expected.items():
            with self.subTest(report=report):
                handle = FakeHandle(a544=[0])
                result = handle_mcm_notification(
                    handle,
                    bytes.fromhex(report),
                    sleep=lambda _seconds: None,
                )
                self.assertIn("respondido", result or "")
                self.assertEqual([(item[3], item[4]) for item in handle.writes], writes)

    def test_status_is_not_treated_as_runtime_query(self):
        handle = FakeHandle()
        self.assertIsNone(handle_mcm_notification(handle, bytes.fromhex("80 4a")))
        self.assertEqual(handle.writes, [])

    def test_all_sound_agent_notifications_are_consumed(self):
        mailbox_commands = {0x61: 2, 0x62: 2, 0x63: 3, 0x64: 2, 0x65: 2, 0x66: 3, 0x6F: 2}
        for command in range(0x60, 0x70):
            with self.subTest(command=command):
                handle = FakeHandle(a544=[0])
                original_read = handle.controlRead

                def read(request_type, request, value, index, length, *, timeout):
                    if index == 0xA4EE:
                        return b"\x01"
                    if index == 0xA4C1 and command in mailbox_commands:
                        return bytes([1] * length)
                    return original_read(request_type, request, value, index, length, timeout=timeout)

                handle.controlRead = read
                result = handle_mcm_notification(
                    handle,
                    bytes((0x80, command)),
                    state=SoundAgentState(),
                    sleep=lambda _seconds: None,
                )
                self.assertIsNotNone(result)

    def test_effect_toggle_updates_b0_flags_from_mailbox(self):
        state = SoundAgentState()
        handle = FakeHandle(a544=[0])

        def read(_request_type, _request, _value, index, length, *, timeout):
            if index == 0xA4EE:
                return b"\x00"
            if index == 0xA4C1:
                return b"\x01"
            if index == 0xA544:
                return b"\x00"
            raise AssertionError(hex(index))

        handle.controlRead = read
        handle_mcm_notification(
            handle,
            bytes.fromhex("80 62"),
            state=state,
            sleep=lambda _seconds: None,
        )
        self.assertTrue(state.effects_enabled)
        self.assertIn((0xA4F4, bytes.fromhex("02 B0 05")), [(x[3], x[4]) for x in handle.writes])

    def test_ui_lifecycle_updates_original_b0_ui_flag(self):
        state = SoundAgentState()
        handle = FakeHandle(a544=[0])

        mailbox_value = 1

        def read(_request_type, _request, _value, index, length, *, timeout):
            if index == 0xA4EE:
                return b"\x00"
            if index == 0xA4C1:
                return bytes((mailbox_value,))
            if index == 0xA544:
                return b"\x00"
            raise AssertionError(hex(index))

        handle.controlRead = read
        handle_mcm_notification(
            handle,
            bytes.fromhex("80 61"),
            state=state,
            sleep=lambda _seconds: None,
        )
        self.assertTrue(state.ui_visible)
        self.assertIn((0xA4F4, bytes.fromhex("02 B0 03")), [(x[3], x[4]) for x in handle.writes])

        handle.writes.clear()
        mailbox_value = 0
        handle_mcm_notification(
            handle,
            bytes.fromhex("80 61"),
            state=state,
            sleep=lambda _seconds: None,
        )
        self.assertFalse(state.ui_visible)
        self.assertIn((0xA4F4, bytes.fromhex("02 B0 01")), [(x[3], x[4]) for x in handle.writes])

    def test_default_parameter_mask_acknowledges_original_boolean_controls(self):
        state = SoundAgentState()
        handle = FakeHandle(a544=[0])

        def read(_request_type, _request, _value, index, length, *, timeout):
            if index == 0xA4EE:
                return b"\x00"
            if index == 0xA4C1:
                return bytes.fromhex("05 80")[:length]
            if index == 0xA544:
                return b"\x00"
            raise AssertionError(hex(index))

        handle.controlRead = read
        handle_mcm_notification(
            handle,
            bytes.fromhex("80 63"),
            state=state,
            sleep=lambda _seconds: None,
        )
        self.assertIn((0xA4F4, bytes.fromhex("02 B1 05")), [(x[3], x[4]) for x in handle.writes])


if __name__ == "__main__":
    unittest.main()
