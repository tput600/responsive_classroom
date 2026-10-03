import unittest

import numpy as np

from classroom_audio import NoiseAnalyzer
from classroom_core import AudioHealth, BaseMode, NoiseState, Settings


def tone(dbfs, size=480):
    return (np.sin(np.arange(size) * .17) * 10 ** (dbfs / 20) * 2 ** .5).astype(np.float32)


class SpeechNoiseGuardTests(unittest.TestCase):
    def settings(self, **overrides):
        values = {
            'speech_noise_guard': True, 'noise_rising_db': 8, 'noise_loud_db': 18,
            'noise_rising_exit_db': 5, 'noise_loud_exit_db': 15,
            'noise_rising_enter_seconds': .2, 'noise_loud_enter_seconds': .2,
            'noise_exit_seconds': .2, 'noise_smoothing_ms': 200,
        }
        return Settings(**{**values, **overrides})

    def test_brief_normal_speech_is_buffered_and_does_not_build_entry_hold(self):
        analyzer = NoiseAnalyzer(self.settings(), baseline_dbfs=-60)
        for step in range(7):
            reading = analyzer.feed(tone(-47), now=step / 10, speech_active=True)
            self.assertEqual(reading.state, NoiseState.QUIET)
            self.assertTrue(reading.speech_excluded)
            self.assertIsNone(analyzer._candidate)
        for step in range(7, 10):
            reading = analyzer.feed(tone(-60), now=step / 10, speech_active=False)
            self.assertTrue(reading.speech_excluded)
        for step in range(10, 15):
            reading = analyzer.feed(tone(-60), now=step / 10, speech_active=False)
        self.assertEqual(reading.state, NoiseState.QUIET)
        self.assertFalse(reading.speech_excluded)

    def test_discussion_continuous_vad_cannot_freeze_activity_indefinitely(self):
        analyzer = NoiseAnalyzer(self.settings(), baseline_dbfs=-60)
        for step in range(31):
            reading = analyzer.feed(tone(-47), now=step / 10,
                                    mode=BaseMode.DISCUSSION, speech_active=True)
        self.assertEqual(reading.state, NoiseState.RISING)
        self.assertFalse(reading.speech_excluded)

    def test_notice_excludes_long_and_strong_speech_but_discussion_still_grades_it(self):
        for mode, expected in ((BaseMode.NOTICE, NoiseState.QUIET),
                               (BaseMode.DISCUSSION, NoiseState.LOUD)):
            analyzer = NoiseAnalyzer(Settings(noise_smoothing_ms=200), baseline_dbfs=-60)
            for step in range(41):
                reading = analyzer.feed(tone(-17), now=step / 10, mode=mode, speech_active=True)
                self.assertEqual(reading.speech_excluded, mode is BaseMode.NOTICE)
            self.assertEqual(reading.state, expected, mode)

    def test_notice_speech_tail_cannot_leak_power_or_hold_into_environment_noise(self):
        analyzer = NoiseAnalyzer(self.settings(), baseline_dbfs=-60)
        for step in range(50):
            analyzer.feed(tone(-30), now=step / 10, speech_active=True)
        for step in range(50, 53):
            reading = analyzer.feed(tone(-60), now=step / 10, speech_active=False)
            self.assertTrue(reading.speech_excluded)
        reading = analyzer.feed(tone(-60), now=5.3, speech_active=False)
        self.assertFalse(reading.speech_excluded)
        self.assertEqual(reading.state, NoiseState.QUIET)
        self.assertLess(reading.relative_db, .3)
        # A later non-speech input must still reach Loud normally.
        for step in range(54, 63):
            reading = analyzer.feed(tone(-30), now=step / 10, speech_active=False)
        self.assertEqual(reading.state, NoiseState.LOUD)

    def test_missing_vad_continues_noise_classification_with_unavailable_guard_flag(self):
        analyzer = NoiseAnalyzer(self.settings(), baseline_dbfs=-60)
        for step in range(5):
            reading = analyzer.feed(tone(-30), now=step / 10, speech_active=None)
        self.assertEqual(reading.state, NoiseState.LOUD)
        self.assertAlmostEqual(reading.dbfs, -30, delta=.2)
        self.assertFalse(reading.speech_guard_ready)
        self.assertFalse(reading.speech_excluded)

    def test_clipped_alternating_input_is_measured_instead_of_masked_as_invalid(self):
        samples = np.tile([1., -1.], 240).astype(np.float32)
        analyzer = NoiseAnalyzer(self.settings(), baseline_dbfs=-60)
        for step in range(5):
            reading = analyzer.feed(samples, now=step / 10,
                                    mode=BaseMode.DISCUSSION, speech_active=True)
        self.assertEqual(reading.health, AudioHealth.CLIPPING)
        self.assertEqual(reading.state, NoiseState.LOUD)
        self.assertIsNotNone(reading.relative_db)

    def test_guard_can_be_disabled_for_noise_only_operation(self):
        analyzer = NoiseAnalyzer(self.settings(speech_noise_guard=False), baseline_dbfs=-60)
        for now in (0, .1, .2, .3):
            reading = analyzer.feed(tone(-50), now=now, speech_active=True)
        self.assertEqual(reading.state, NoiseState.RISING)
        self.assertFalse(reading.speech_excluded)
        self.assertTrue(reading.speech_guard_ready)
