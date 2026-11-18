from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import admin_tasks
import equalizer_setup
import pnp_devices

APP_ROOT = Path(__file__).resolve().parents[1]


class InfCheckTests(unittest.TestCase):
    def test_bundled_driver_targets_only_mi03(self):
        inf = APP_ROOT / "driver" / "winusb-philips-mi03" / admin_tasks.INF_NAME
        admin_tasks.check_inf_targets_mi03_only(admin_tasks.read_inf(inf))

    def test_inf_touching_audio_is_refused(self):
        inf = admin_tasks.read_inf(APP_ROOT / "driver" / "winusb-philips-mi03" / admin_tasks.INF_NAME)
        with self.assertRaises(RuntimeError):
            admin_tasks.check_inf_targets_mi03_only(inf + "\n; VID_0471&PID_0111&MI_00\n")
        with self.assertRaises(RuntimeError):
            admin_tasks.check_inf_targets_mi03_only(inf.replace("MI_03", "MI_02"))


class AdminEntryTests(unittest.TestCase):
    def test_flag_detection(self):
        self.assertTrue(admin_tasks.is_admin_invocation(["app.exe", "--admin-task", "restore-driver"]))
        self.assertFalse(admin_tasks.is_admin_invocation(["app.exe", "--minimized"]))

    def test_errors_go_to_the_log(self):
        log = Path(tempfile.gettempdir()) / "philips_pc_link_test_admin.log"
        stdout, stderr = sys.stdout, sys.stderr
        try:
            code = admin_tasks.main(["app.exe", "--admin-task", "nao-existe", "--admin-log", str(log)])
        finally:
            sys.stdout, sys.stderr = stdout, stderr
        self.assertEqual(code, 1)
        self.assertIn("ERRO:", log.read_text(encoding="utf-8"))
        log.unlink()


class SecurityTextTests(unittest.TestCase):
    def test_admin_entry_goes_first_and_only_once(self):
        dacl = "D:P(A;CI;KR;;;BU)(A;CI;KA;;;SY)"
        added = equalizer_setup.with_admin_full_control(dacl)
        self.assertEqual(added, "D:P(A;;KA;;;BA)(A;CI;KR;;;BU)(A;CI;KA;;;SY)")
        self.assertEqual(equalizer_setup.with_admin_full_control(added), added)


class PnpTests(unittest.TestCase):
    def test_inf_name_from_path(self):
        device = pnp_devices.PnpDevice("USB\\X", "x", "USBDevice", "WinUSB", "C:\\Windows\\INF\\oem62.inf", True)
        self.assertEqual(device.inf_name, "oem62.inf")

    def test_listing_works_without_powershell(self):
        self.assertIsInstance(pnp_devices.list_devices("USB", present_only=True), list)


if __name__ == "__main__":
    unittest.main()
