from __future__ import annotations

import os
import time
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app
import philips_pc_link_core as core
from pc_link_protocol import MediaInfo
from PySide6.QtWidgets import QApplication


class FakeSettings:
    values: dict[str, object] = {}

    def __init__(self, *_args):
        pass

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class FakeListener:
    def __init__(self, *_args, **_kwargs):
        self.running = False
        self.buttons_enabled = True
        self.active_profile = type("Profile", (), {"profile_id": "test-profile"})()

    def set_learned_map(self, _mapping):
        pass

    def set_buttons_enabled(self, enabled):
        self.buttons_enabled = enabled

    def set_device_family(self, _family):
        pass

    def set_profile(self, _profile):
        pass

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class FakeMediaMonitor:
    last_error = ""

    def __init__(self, *_args, **_kwargs):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def snapshot(self):
        return MediaInfo(title="Song", artist="Band", status="playing")

    def command(self, _name):
        return "fake"


class FakeSoftwareVolume:
    def __init__(self, *_args, **_kwargs):
        self.enabled = False
        self.stopped = False

    def set_enabled(self, enabled):
        self.enabled = enabled

    def stop(self):
        self.stopped = True


class AppModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        FakeSettings.values = {}
        self.originals = (
            app.QSettings,
            app.RadioButtonListener,
            app.get_start_with_windows,
            app.detect_profile,
            app.MainWindow.poll_status,
            app.MediaMonitor,
            app.SoftwareVolume,
            app.find_philips_render_endpoint_fast,
        )
        app.QSettings = FakeSettings
        # Never touch the real Philips chip from the tests.
        app.find_philips_render_endpoint_fast = lambda: None
        app.RadioButtonListener = FakeListener
        app.get_start_with_windows = lambda: False
        app.detect_profile = lambda: None
        app.MainWindow.poll_status = lambda _self: None
        app.MediaMonitor = FakeMediaMonitor
        app.SoftwareVolume = FakeSoftwareVolume

    def tearDown(self):
        (
            app.QSettings,
            app.RadioButtonListener,
            app.get_start_with_windows,
            app.detect_profile,
            app.MainWindow.poll_status,
            app.MediaMonitor,
            app.SoftwareVolume,
            app.find_philips_render_endpoint_fast,
        ) = self.originals

    def test_mcm_selection_is_persisted_and_used_for_activation(self):
        window = app.MainWindow()
        window.auto_checkbox.setChecked(False)
        mcm_index = window.family_combo.findData("MCM")
        window.family_combo.setCurrentIndex(mcm_index)

        calls = []
        original_send = app.send_pc_link_enable
        app.send_pc_link_enable = lambda **kwargs: calls.append(kwargs) or "ack"
        window.run_bg = lambda _label, fn, **_kwargs: fn()
        try:
            window.activate_now()
        finally:
            app.send_pc_link_enable = original_send
            window._force_quit = True
            window.close()

        self.assertEqual(FakeSettings.values["device_family"], "MCM")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["device_family"], "MCM")
        self.assertIs(calls[0]["pc_link"], window.pc_link)
        self.assertTrue(window._pc_link_active)

    def test_pc_link_channel_stays_on_for_title_display_without_buttons(self):
        window = app.MainWindow()
        window.auto_checkbox.setChecked(False)
        window._control_ready = True
        window.buttons_checkbox.setChecked(False)
        window.display_checkbox.setChecked(True)
        window.sync_button_listener()
        try:
            self.assertTrue(window.radio_listener.running)
            self.assertFalse(window.radio_listener.buttons_enabled)
        finally:
            window._force_quit = True
            window.close()

    def test_software_volume_is_on_by_default_and_toggles(self):
        window = app.MainWindow()
        window.auto_checkbox.setChecked(False)
        try:
            self.assertTrue(window.software_volume.enabled)
            window.soft_volume_checkbox.setChecked(False)
            self.assertFalse(window.software_volume.enabled)
            self.assertFalse(FakeSettings.values["software_volume"])
        finally:
            window._force_quit = True
            window.close()
        self.assertTrue(window.software_volume.stopped)

    def test_display_and_volume_options_are_persisted(self):
        window = app.MainWindow()
        window.auto_checkbox.setChecked(False)
        try:
            window.display_checkbox.setChecked(False)
            window.volume_combo.setCurrentIndex(window.volume_combo.findData("philips"))
            self.assertFalse(window._show_title)
            self.assertFalse(window.pc_link._display_enabled())
            self.assertEqual(FakeSettings.values["volume_mode"], "philips")
            self.assertEqual(core.get_volume_mode(), "philips")
            window.refresh_now_playing()
            self.assertIn("Song - Band", window.now_playing_label.text())
        finally:
            window.volume_combo.setCurrentIndex(window.volume_combo.findData("windows"))
            window._force_quit = True
            window.close()

    def test_mcm_control_channel_stays_on_when_media_buttons_are_disabled(self):
        window = app.MainWindow()
        window.auto_checkbox.setChecked(False)
        window._control_ready = True
        window.buttons_checkbox.setChecked(False)
        window.family_combo.setCurrentIndex(window.family_combo.findData("MCM"))
        window.sync_button_listener()
        try:
            self.assertTrue(window.radio_listener.running)
        finally:
            window._force_quit = True
            window.close()

    def test_mcm_activation_failure_schedules_retry_and_keeps_button_available(self):
        window = app.MainWindow()
        window.auto_checkbox.setChecked(False)
        window._control_ready = True
        window.family_combo.setCurrentIndex(window.family_combo.findData("MCM"))
        original_send = app.send_pc_link_enable
        app.send_pc_link_enable = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("USB timeout"))
        window.run_bg = lambda _label, fn, **_kwargs: fn()
        try:
            with self.assertRaisesRegex(RuntimeError, "USB timeout"):
                window.activate_now()
            self.assertFalse(window._pc_link_active)
            self.assertGreater(window._next_mcm_retry_at, time.time())
        finally:
            app.send_pc_link_enable = original_send
            window._force_quit = True
            window.close()

    def test_due_mcm_retry_auto_activates_without_reconnect(self):
        window = app.MainWindow()
        window.auto_checkbox.setChecked(False)
        window.family_combo.setCurrentIndex(window.family_combo.findData("MCM"))
        window.auto_checkbox.setChecked(True)
        window.last_ready = True
        window._control_ready = True
        window._last_usb_connection_key = "same-connection"
        window._next_mcm_retry_at = time.time() - 1
        calls = []
        window.activate_now = lambda auto=False: calls.append(auto)
        status = SimpleNamespace(
            summary="pronto",
            profile=None,
            interface3_service="WinUSB",
            present=True,
            interface3_ok=True,
            audio_present=True,
            default_output=False,
            usb_connection_key="same-connection",
        )
        try:
            window.apply_status(status)
            self.assertEqual(calls, [True])
        finally:
            window._force_quit = True
            window.close()

    def test_mcm_response_error_is_logged_and_report_is_consumed(self):
        errors = []
        listener = core.RadioButtonListener(lambda *_args: None, errors.append)
        listener.set_device_family("MCM")
        original_handler = core.handle_mcm_notification
        core.handle_mcm_notification = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("USB timeout")
        )
        try:
            consumed = listener._dispatch_mcm_notification(object(), bytes.fromhex("80 6b"))
        finally:
            core.handle_mcm_notification = original_handler

        self.assertTrue(consumed)
        self.assertEqual(len(errors), 1)
        self.assertIn("Leitura mantida ativa", errors[0])
        self.assertIn("80 6b", errors[0])


if __name__ == "__main__":
    unittest.main()
