"""Both chip variants side by side: 0471:0111 (FWM/FWC/MCM) and 0471:0110 (FW-C577)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import admin_tasks
import equalizer
import equalizer_setup
import philips_pc_link_core as core
from pc_link_protocol import MediaInfo, PcLinkSession
from profiles import (
    SUPPORTED_PROFILES,
    default_profile,
    find_profile_by_usb_id,
    is_philips_output_name,
    uses_legacy_transport,
)
from sound_agent import LEGACY_MCM_SETUP, SoundAgentError, send_frame_legacy


class FakeDevice:
    def __init__(self, product_id):
        self.product_id = product_id

    def getProductID(self):
        return self.product_id


class ChipHandle:
    """Chip memory model for either variant."""

    def __init__(self, product_id: int, *, legacy_ack: bool = True):
        self.device = FakeDevice(product_id)
        self.writes: list[tuple[int, bytes]] = []
        self.memory: dict[int, bytes] = {}
        self.legacy_ack = legacy_ack

    def getDevice(self):
        return self.device

    def controlWrite(self, request_type, request, value, index, payload, *, timeout):
        assert (request_type, request, value) == (0x40, 0x04, 0)
        self.writes.append((index, bytes(payload)))
        self.memory[index] = bytes(payload)
        return len(payload)

    def controlRead(self, request_type, request, value, index, length, *, timeout):
        assert (request_type, request, value) == (0xC0, 0x05, 0)
        if index == 0xA544:
            return b"\x00"
        if index == 0xA4F2 and self.legacy_ack:
            # The 0110 unit counts the bytes it took from A4F4.
            frame = self.memory.get(0xA4F4, b"\x00")
            return bytes((frame[0],))
        return self.memory.get(index, bytes(length))[:length].ljust(length, b"\x00")

    def addresses(self) -> list[int]:
        return [address for address, _payload in self.writes]


class Host:
    def media(self):
        return MediaInfo(title="Song", artist="Band", status="playing")

    def media_command(self, _name):
        pass

    def volume_step(self, _d):
        pass

    def toggle_mute(self):
        pass

    def set_volume(self, _p):
        pass

    def volume_state(self):
        return (50, False)


def make_session(**kwargs) -> PcLinkSession:
    return PcLinkSession(Host(), sleep=lambda _s: None, **kwargs)


class ProfileTests(unittest.TestCase):
    def test_existing_0111_profile_is_unchanged_and_first(self):
        first = default_profile()
        self.assertEqual((first.product_id, first.control_interface, first.control_hardware_id_suffix), (0x0111, 3, "MI_03"))
        self.assertEqual(first.driver_package, "winusb-philips-mi03")
        self.assertIs(find_profile_by_usb_id(0x0471, 0x0111), first)
        self.assertIn("MCM595", first.known_models)

    def test_0110_profile_uses_interface_2(self):
        profile = find_profile_by_usb_id(0x0471, 0x0110)
        self.assertIsNotNone(profile)
        self.assertEqual((profile.control_interface, profile.control_hardware_id_suffix), (2, "MI_02"))
        self.assertEqual(profile.interrupt_endpoint, 0x83)
        self.assertEqual(len(SUPPORTED_PROFILES), 2)

    def test_only_0110_uses_the_legacy_transport(self):
        self.assertTrue(uses_legacy_transport(0x0110))
        self.assertFalse(uses_legacy_transport(0x0111))

    def test_output_names(self):
        self.assertTrue(is_philips_output_name("Alto-falantes (Philips Audio Set)"))
        self.assertTrue(is_philips_output_name("Speakers (UAC3553B)"))
        self.assertFalse(is_philips_output_name("Realtek High Definition Audio"))


class TransportTests(unittest.TestCase):
    def test_legacy_frame_sequence(self):
        handle = ChipHandle(0x0110)
        polls = send_frame_legacy(handle, bytes((0x02, 0x4A, 0x82)), sleep=lambda _s: None)
        self.assertEqual(polls, 1)
        self.assertEqual(handle.writes, [(0xA4F4, b"\x02\x4a\x82"), (0xA4F2, b"\x00"), (0xB0A0, b"\xc0")])

    def test_legacy_frame_times_out_without_ack(self):
        handle = ChipHandle(0x0110, legacy_ack=False)
        ticks = iter(range(100))
        with self.assertRaises(SoundAgentError):
            send_frame_legacy(handle, bytes((0x02, 0x4A, 0x82)), sleep=lambda _s: None,
                              monotonic=lambda: next(ticks), ready_timeout_s=2)

    def test_0111_session_uses_mailbox_and_installs_setup_block(self):
        handle = ChipHandle(0x0111)
        sess = make_session(ensure_mailbox=core.ensure_pc_link_mailbox)
        sess.handle_request(handle, bytes.fromhex("80 4a"))
        self.assertIn(0xA556, handle.addresses())  # original Philips setup block
        self.assertIn(0xA544, handle.addresses())  # mailbox handshake
        self.assertNotIn(0xB0A0, handle.addresses())

    def test_0110_session_uses_legacy_and_never_writes_the_0111_block(self):
        handle = ChipHandle(0x0110)
        sess = make_session(ensure_mailbox=core.ensure_pc_link_mailbox)
        message = sess.handle_request(handle, bytes.fromhex("80 4a"))
        self.assertIn("respondido", message)
        addresses = handle.addresses()
        self.assertIn(0xB0A0, addresses)
        for address, _payload in LEGACY_MCM_SETUP:
            if address not in (0xA4EF, 0xA4F0):
                self.assertNotIn(address, addresses)
        self.assertNotIn(0xA544, addresses)

    def test_mailbox_helper_skips_0110(self):
        self.assertEqual(core.ensure_pc_link_mailbox(ChipHandle(0x0110)), [])


class DriverTests(unittest.TestCase):
    SAMPLE = (
        '[Strings]\nDeviceName = "x"\nDeviceID   = "{id}"\n'
        "[Philips_WinUSB.NTamd64]\n%DeviceName% = USB_Install, USB\\%DeviceID%\n"
    )

    def test_both_control_interfaces_are_allowed(self):
        admin_tasks.check_inf_targets_control_only(self.SAMPLE.format(id="VID_0471&PID_0111&MI_03"))
        admin_tasks.check_inf_targets_control_only(self.SAMPLE.format(id="VID_0471&PID_0110&MI_02"))

    def test_audio_and_other_interfaces_are_refused(self):
        for bad in ("VID_0471&PID_0111&MI_00", "VID_0471&PID_0110&MI_01", "VID_0471&PID_0110&MI_03",
                    "VID_0471&PID_0111&MI_02", "VID_1234&PID_0111&MI_03"):
            with self.assertRaises(RuntimeError, msg=bad):
                admin_tasks.check_inf_targets_control_only(self.SAMPLE.format(id=bad))
        mentions_audio = self.SAMPLE.format(id="VID_0471&PID_0110&MI_02") + "; USB\\VID_0471&PID_0110&MI_01\n"
        with self.assertRaises(RuntimeError):
            admin_tasks.check_inf_targets_control_only(mentions_audio)

    def test_targets_follow_the_profiles(self):
        self.assertEqual(admin_tasks.CONTROL_TARGETS, (("0111", "MI_03"), ("0110", "MI_02")))
        self.assertIn("USB\\VID_0471&PID_0110&MI_00\\", admin_tasks.AUDIO_PREFIXES)

    def test_winusb_name_is_case_insensitive(self):
        self.assertTrue(admin_tasks.is_winusb("WINUSB"))
        self.assertTrue(admin_tasks.is_winusb("WinUSB"))
        self.assertFalse(admin_tasks.is_winusb("HidUsb"))
        self.assertFalse(admin_tasks.is_winusb(None))

    def test_every_profile_has_a_complete_driver_package(self):
        for profile in SUPPORTED_PROFILES:
            package = core.driver_dir(profile)
            inf = admin_tasks._package_file(package, "*.inf")
            admin_tasks._package_file(package, "*.cat")
            admin_tasks._package_file(package, "*.cer")
            text = admin_tasks.read_inf(inf)
            admin_tasks.check_inf_targets_control_only(text)
            self.assertIn(profile.control_hardware_id.split("\\", 1)[1], text.upper())

    def test_0110_catalog_matches_its_inf_bytes(self):
        # The catalog hashes the INF exactly as committed; a line-ending
        # conversion (git autocrlf) would break the driver signature.
        import hashlib

        package = core.driver_dir(find_profile_by_usb_id(0x0471, 0x0110))
        inf = admin_tasks._package_file(package, "*.inf").read_bytes()
        cat = admin_tasks._package_file(package, "*.cat").read_bytes()
        self.assertIn(hashlib.sha1(inf).digest(), cat)

    def test_missing_driver_package_explains_zadig(self):
        profile = find_profile_by_usb_id(0x0471, 0x0110)
        original = core.driver_dir
        core.driver_dir = lambda _profile=None: Path(tempfile.gettempdir()) / "no-such-philips-driver"
        try:
            with self.assertRaisesRegex(FileNotFoundError, "MI_02"):
                core.install_winusb_driver(profile)
        finally:
            core.driver_dir = original


class EqualizerFileTests(unittest.TestCase):
    def test_device_line_names_both_outputs(self):
        self.assertEqual(equalizer.DEVICE_PATTERN, "Philips Audio Set; UAC3553B")

    def test_repair_double_carriage_returns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.txt"
            path.write_bytes(b"# x\r\r\nInclude: philips-pc-link.txt\r\r\n")
            self.assertTrue(equalizer_setup.repair_line_endings(path))
            self.assertEqual(path.read_bytes(), b"# x\r\nInclude: philips-pc-link.txt\r\n")
            self.assertFalse(equalizer_setup.repair_line_endings(path))

    def test_saving_the_curve_repairs_config_txt(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.txt"
            config.write_bytes(b"Include: philips-pc-link.txt\r\r\n")
            equalizer.write_config(equalizer.EqualizerSettings(), Path(folder))
            self.assertEqual(config.read_bytes(), b"Include: philips-pc-link.txt\r\n")
            curve = (Path(folder) / equalizer.CONFIG_FILE_NAME).read_bytes()
            self.assertNotIn(b"\r\r\n", curve)


if __name__ == "__main__":
    unittest.main()
