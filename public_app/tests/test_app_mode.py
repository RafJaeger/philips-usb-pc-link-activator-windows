from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app
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
    def __init__(self, *_args):
        self.running = False
        self.active_profile = type("Profile", (), {"profile_id": "test-profile"})()

    def set_learned_map(self, _mapping):
        pass

    def set_device_family(self, _family):
        pass

    def set_profile(self, _profile):
        pass

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


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
        )
        app.QSettings = FakeSettings
        app.RadioButtonListener = FakeListener
        app.get_start_with_windows = lambda: False
        app.detect_profile = lambda: None
        app.MainWindow.poll_status = lambda _self: None

    def tearDown(self):
        (
            app.QSettings,
            app.RadioButtonListener,
            app.get_start_with_windows,
            app.detect_profile,
            app.MainWindow.poll_status,
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
        self.assertEqual(calls, [{"device_family": "MCM"}])
        self.assertTrue(window._pc_link_active)

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


if __name__ == "__main__":
    unittest.main()
