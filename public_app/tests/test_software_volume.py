from __future__ import annotations

import unittest

from software_volume import SessionLevels, SoftwareVolume, endpoint_gain


class GainTests(unittest.TestCase):
    def test_endpoint_db_becomes_linear_gain(self):
        self.assertAlmostEqual(endpoint_gain(0.0, 1.0), 1.0)
        self.assertAlmostEqual(endpoint_gain(-6.0, 0.65), 0.501, places=3)
        self.assertAlmostEqual(endpoint_gain(-20.0, 0.2), 0.1, places=3)
        self.assertEqual(endpoint_gain(-41.0, 0.0), 0.0)


class SessionLevelTests(unittest.TestCase):
    def test_levels_follow_gain_and_keep_mixer_tweaks(self):
        levels = SessionLevels()
        self.assertAlmostEqual(levels.target("spotify", 1.0, 0.5), 0.5)
        self.assertAlmostEqual(levels.target("spotify", 0.5, 0.25), 0.25)
        # The user lowers Spotify in the mixer to 0.1 while gain is 0.25.
        self.assertAlmostEqual(levels.target("spotify", 0.1, 0.25), 0.1)
        self.assertAlmostEqual(levels.target("spotify", 0.1, 1.0), 0.4)
        self.assertAlmostEqual(levels.restore_level("spotify"), 0.4)

    def test_new_session_keeps_its_own_level(self):
        levels = SessionLevels()
        self.assertAlmostEqual(levels.target("chrome", 0.8, 0.5), 0.4)
        levels.forget_missing(set())
        self.assertEqual(levels.restore_level("chrome"), 1.0)


class FakeVolume:
    def __init__(self, level):
        self.level = level

    def GetMasterVolume(self):
        return self.level

    def SetMasterVolume(self, level, _context):
        self.level = level


class FakeSession:
    def __init__(self, key, level):
        self.InstanceIdentifier = key
        self.SimpleAudioVolume = FakeVolume(level)


class FakeEndpoint:
    def __init__(self, db, scalar):
        self.db = db
        self.scalar = scalar

    def GetMasterVolumeLevel(self):
        return self.db

    def GetMasterVolumeLevelScalar(self):
        return self.scalar


class FakeDevice:
    def __init__(self, sessions, endpoint):
        self.sessions = sessions
        self.EndpointVolume = endpoint


class ApplyTests(unittest.TestCase):
    def test_apply_and_restore_sessions(self):
        sessions = [FakeSession("a", 1.0), FakeSession("b", 0.5)]
        device = FakeDevice(sessions, FakeEndpoint(-20.0, 0.2))
        volume = SoftwareVolume(lambda: device)
        volume._sessions = lambda _device: device.sessions
        volume._apply(device)
        self.assertAlmostEqual(sessions[0].SimpleAudioVolume.level, 0.1, places=3)
        self.assertAlmostEqual(sessions[1].SimpleAudioVolume.level, 0.05, places=3)
        volume._restore(device)
        self.assertAlmostEqual(sessions[0].SimpleAudioVolume.level, 1.0)
        self.assertAlmostEqual(sessions[1].SimpleAudioVolume.level, 0.5)


if __name__ == "__main__":
    unittest.main()
