"""The chip bass/treble controls survive the radio leaving and coming back."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui_equalizer import STEPS_PER_DB, EqualizerPanel  # noqa: E402


class FakeSettings:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class FakeChip:
    """One Windows audio endpoint; 'gone' after the radio left PC Link."""

    def __init__(self, bass=0.0, treble=0.0):
        self.bass, self.treble = bass, treble
        self.gone = False
        self.available = True
        self.has_agc = True
        self.agc = False

    def set_bass(self, value):
        if self.gone:
            raise OSError("endpoint removido")
        self.bass = value
        return value

    def set_treble(self, value):
        if self.gone:
            raise OSError("endpoint removido")
        self.treble = value
        return value

    def set_agc(self, enabled):
        self.agc = enabled


class Radio:
    """Factory: returns the current endpoint, or None while unplugged."""

    def __init__(self):
        self.chip = FakeChip(4.5, 4.0)
        self.plugged = True

    def __call__(self):
        return self.chip if self.plugged else None

    def replug(self):
        self.chip.gone = True
        self.chip = FakeChip(0.0, 0.0)  # Windows recreates the output


class ChipTonePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def make(self, radio):
        return EqualizerPanel(FakeSettings(), chip_tone_factory=radio)

    def test_sliders_start_with_the_chip_values(self):
        radio = Radio()
        panel = self.make(radio)
        self.assertEqual(panel.bass_slider.value(), int(4.5 * STEPS_PER_DB))
        self.assertEqual(panel.treble_slider.value(), int(4.0 * STEPS_PER_DB))

    def test_moving_a_slider_sets_the_chip(self):
        radio = Radio()
        panel = self.make(radio)
        panel.bass_slider.setValue(int(-3 * STEPS_PER_DB))
        self.assertEqual(radio.chip.bass, -3.0)

    def test_replugged_radio_is_reconnected_on_the_next_move(self):
        radio = Radio()
        panel = self.make(radio)
        radio.replug()  # the old endpoint is gone; the panel still holds it
        panel.treble_slider.setValue(int(6 * STEPS_PER_DB))
        self.assertEqual(radio.chip.treble, 6.0)
        self.assertEqual(radio.chip.bass, 4.5)  # the user's bass kept too

    def test_app_started_before_the_radio(self):
        radio = Radio()
        radio.plugged = False
        panel = self.make(radio)
        self.assertTrue(panel.bass_slider.isEnabled())
        panel.bass_slider.setValue(int(2 * STEPS_PER_DB))  # radio still away
        radio.plugged = True
        panel.radio_returned()
        self.assertEqual(radio.chip.bass, 2.0)
        self.assertIn("chip", panel.tone_status.text())


if __name__ == "__main__":
    unittest.main()
