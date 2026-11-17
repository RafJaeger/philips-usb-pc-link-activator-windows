from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from equalizer import (
    BANDS_HZ,
    FACTORY_PRESETS,
    EqualizerSettings,
    build_config,
    preamp_db,
    write_config,
)
from tone_control import LevelRange


class EqualizerConfigTests(unittest.TestCase):
    def test_flat_curve_only_targets_the_philips_output(self):
        text = build_config(EqualizerSettings())
        lines = text.strip().splitlines()
        self.assertIn("Device: Philips Audio Set", lines)
        self.assertIn("Preamp: 0.0 dB", lines)
        self.assertFalse(any(line.startswith("Filter:") for line in lines))
        self.assertEqual(lines[-1], "Device: all")

    def test_bands_become_peaking_filters_with_headroom(self):
        settings = EqualizerSettings(gains=list(FACTORY_PRESETS["Rock"]))
        text = build_config(settings)
        self.assertIn("Filter: ON PK Fc 31 Hz Gain 5.0 dB Q 1.41", text)
        self.assertIn("Filter: ON PK Fc 16000 Hz Gain 5.0 dB Q 1.41", text)
        self.assertIn("Filter: ON PK Fc 500 Hz Gain -1.0 dB Q 1.41", text)
        self.assertIn("Preamp: -5.0 dB", text)
        self.assertEqual(text.count("Filter: ON PK"), sum(1 for g in FACTORY_PRESETS["Rock"] if g))

    def test_vbass_adds_low_shelf_and_more_headroom(self):
        settings = EqualizerSettings(gains=[2.0] + [0.0] * 9, vbass=True, vbass_level=100)
        text = build_config(settings)
        self.assertIn("Filter: ON LS Fc 80 Hz Gain 9.0 dB", text)
        self.assertEqual(preamp_db(settings), -11.0)

    def test_disabled_equalizer_writes_no_filters(self):
        text = build_config(EqualizerSettings(enabled=False, gains=[6.0] * 10))
        self.assertNotIn("Filter:", text)
        self.assertNotIn("Preamp:", text)

    def test_settings_roundtrip_and_clamping(self):
        settings = EqualizerSettings(gains=[20.0, -30.0] + [1.0] * 8, vbass=True, vbass_level=70, preset="Meu")
        again = EqualizerSettings.from_json(settings.to_json())
        self.assertEqual(again.gains[:2], [12.0, -12.0])
        self.assertEqual((again.vbass, again.vbass_level, again.preset), (True, 70.0, "Meu"))
        self.assertEqual(EqualizerSettings.from_json("lixo").gains, [0.0] * len(BANDS_HZ))

    def test_write_config_replaces_file_atomically(self):
        with tempfile.TemporaryDirectory() as folder:
            target = write_config(EqualizerSettings(gains=list(FACTORY_PRESETS["Pop"])), Path(folder))
            self.assertEqual(target.name, "philips-pc-link.txt")
            self.assertIn("Filter: ON PK Fc 250 Hz Gain 4.0 dB", target.read_text(encoding="utf-8"))
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["philips-pc-link.txt"])

    def test_factory_presets_have_ten_bands_in_range(self):
        for name, gains in FACTORY_PRESETS.items():
            with self.subTest(name=name):
                self.assertEqual(len(gains), 10)
                self.assertTrue(all(-12 <= g <= 12 for g in gains))


class ChipToneTests(unittest.TestCase):
    def test_level_range_snaps_to_half_db(self):
        chip = LevelRange(-12.0, 12.0, 0.5)
        self.assertEqual(chip.clamp(3.3), 3.5)
        self.assertEqual(chip.clamp(-20), -12.0)
        self.assertEqual(chip.clamp(12.24), 12.0)


if __name__ == "__main__":
    unittest.main()
