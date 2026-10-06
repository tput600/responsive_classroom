import unittest
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

import numpy as np

from classroom_audio import NoiseAnalyzer
from classroom_core import BaseMode, NoiseState, Settings, SettingsRepository


def tone(dbfs, size=480):
    return (np.sin(np.arange(size) * .17) * 10 ** (dbfs / 20) * 2 ** .5).astype(np.float32)


class NoiseClassificationTests(unittest.TestCase):
    def test_attention_threshold_controls_rising_state_with_responsive_timing(self):
        audio = tone(-54)
        sensitive = NoiseAnalyzer(Settings(noise_rising_db=2, noise_loud_db=8,
                                           noise_rising_exit_db=1, noise_loud_exit_db=4,
                                           noise_rising_enter_seconds=.45,
                                           noise_loud_enter_seconds=.75,
                                           noise_exit_seconds=1.2,
                                           noise_smoothing_ms=300), baseline_dbfs=-60)
        default = NoiseAnalyzer(Settings(noise_smoothing_ms=300,
                                         noise_rising_enter_seconds=.45), baseline_dbfs=-60)
        for step in range(61):
            now = step / 100
            quick = sensitive.feed(audio, now=now)
            unchanged = default.feed(audio, now=now)
        self.assertAlmostEqual(quick.relative_db, 6, delta=.1)
        self.assertEqual(quick.state, NoiseState.RISING)
        self.assertEqual(unchanged.state, NoiseState.QUIET)

    def test_schema_16_migrates_only_untouched_noise_response_timing(self):
        defaults = asdict(Settings())
        old = {**defaults, "schema_version": 16,
               "noise_rising_enter_seconds": 1.5,
               "noise_loud_enter_seconds": 2.0,
               "noise_exit_seconds": 3.0,
               "noise_smoothing_ms": 750}
        custom = {**old, "noise_rising_enter_seconds": .9}
        with tempfile.TemporaryDirectory() as directory:
            for name, source, expected in (("default", old, (.45, .75, 1.2, 300)),
                                           ("custom", custom, (.9, 2.0, 3.0, 750))):
                path = Path(directory) / f"{name}.json"
                original = json.dumps(source)
                path.write_text(original, encoding="utf-8")
                loaded = SettingsRepository(path).load()
                self.assertEqual(path.with_suffix(".json.v16.bak").read_text(encoding="utf-8"), original)
                self.assertEqual((loaded.noise_rising_enter_seconds, loaded.noise_loud_enter_seconds,
                                  loaded.noise_exit_seconds, loaded.noise_smoothing_ms), expected)

    def test_noise_classification_uses_level_in_both_modes(self):
        settings = Settings(noise_smoothing_ms=200, noise_loud_enter_seconds=.2)
        self.assertEqual(settings.noise_loud_db, 15)
        self.assertEqual(settings.discussion_noise["loud_db"], 19)
        loud = tone(-25)
        for mode in (BaseMode.NOTICE, BaseMode.DISCUSSION):
            analyzer = NoiseAnalyzer(settings, baseline_dbfs=-60)
            for step in range(40):
                reading = analyzer.feed(loud, now=step / 100, mode=mode)
            self.assertEqual(reading.state, NoiseState.LOUD, mode)
            self.assertGreater(reading.relative_db, settings.noise_profile(mode).loud_db)

    def test_mode_switch_preserves_live_level_and_smoothing(self):
        analyzer = NoiseAnalyzer(Settings(noise_smoothing_ms=500, noise_loud_enter_seconds=.2), baseline_dbfs=-60)
        loud = tone(-25)
        for step in range(35):
            reading = analyzer.feed(loud, now=step / 100, mode=BaseMode.NOTICE)
        before = reading.smoothed_dbfs
        switched = analyzer.feed(loud, now=.35, mode=BaseMode.DISCUSSION, context_revision=1)
        self.assertEqual(switched.state, NoiseState.LOUD)
        self.assertAlmostEqual(switched.smoothed_dbfs, before, delta=.5)


if __name__ == "__main__":
    unittest.main()
