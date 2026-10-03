import unittest

import numpy as np

from classroom_audio import NoiseAnalyzer
from classroom_core import AudioHealth, BaseMode, NoiseState, Settings


def tone(amplitude=.03, size=480):
    return (np.sin(np.arange(size) * .17) * amplitude).astype(np.float32)


class AudioHealthTests(unittest.TestCase):
    def test_dc_silence_and_nonfinite_never_classify(self):
        analyzer = NoiseAnalyzer(Settings(), -40)
        cases = [(np.ones(1000, np.float32), AudioHealth.SILENT),
                 (np.zeros(1000, np.float32), AudioHealth.SILENT),
                 (np.array([np.nan], np.float32), AudioHealth.DISCONNECTED)]
        for i, (samples, health) in enumerate(cases):
            reading = analyzer.feed(samples, now=10 + i, context_revision=i)
            self.assertEqual(reading.health, health)
            self.assertEqual(reading.state, NoiseState.UNKNOWN)

    def test_calibration_discards_lead_in_and_persists_only_stable_result(self):
        settings = Settings(calibration={**Settings().calibration, "discard_initial_sec": 1,
                                         "max_speech_ratio": .1})
        analyzer = NoiseAnalyzer(settings, -50)
        analyzer.device_id, analyzer.sample_rate = "mic-1", 48000
        analyzer.start_calibration(now=0, seconds=3)
        result = None
        for t in range(31):
            reading = analyzer.feed(tone(.01 if t < 10 else .04), now=t / 10,
                                    speech_active=False)
            result = reading.calibration_result or result
            if t < 30:
                self.assertEqual(reading.state, NoiseState.UNKNOWN)
                self.assertEqual(reading.health, AudioHealth.UNCALIBRATED)
        self.assertEqual(result["quality"], "PASS")
        self.assertAlmostEqual(analyzer.baseline_dbfs, result["baseline_dbfs"])
        self.assertEqual(result["device_id"], "mic-1")
        self.assertEqual(result["sample_rate"], 48000)
        self.assertTrue(result["timestamp"].endswith("+0000") or "+" in result["timestamp"][-6:] or
                        result["timestamp"].endswith("-0000"))

    def test_unstable_or_speech_contaminated_calibration_keeps_baseline(self):
        settings = Settings(calibration={**Settings().calibration, "discard_initial_sec": 0,
                                         "max_speech_ratio": .1})
        for speech, varying in ((False, True), (True, False)):
            analyzer = NoiseAnalyzer(settings, -45)
            analyzer.start_calibration(now=0, seconds=2)
            result = None
            for t in range(21):
                reading = analyzer.feed(tone(.005 if varying and t % 2 else .1), now=t / 10,
                                        speech_active=speech)
                result = reading.calibration_result or result
            self.assertEqual(result["quality"], "REJECTED")
            self.assertEqual(analyzer.baseline_dbfs, -45)

    def test_sparse_or_clipped_calibration_is_rejected(self):
        settings = Settings(calibration={**Settings().calibration, "discard_initial_sec": 0})
        sparse = NoiseAnalyzer(settings, -45)
        sparse.start_calibration(now=0, seconds=3)
        result = sparse.feed(tone(), now=3, speech_active=False).calibration_result
        self.assertEqual(result["quality"], "REJECTED")
        clipped = NoiseAnalyzer(settings, -45)
        clipped.start_calibration(now=0, seconds=2)
        result = None
        for t in range(21):
            block = np.tile([1., -1.], 240).astype(np.float32) if t == 10 else tone()
            result = clipped.feed(block, now=t / 10, speech_active=False).calibration_result or result
        self.assertEqual(result["quality"], "REJECTED")
        self.assertEqual(clipped.baseline_dbfs, -45)

    def test_mode_change_grace_and_stale_gap_clear_candidate(self):
        settings = Settings(speech_noise_guard=False, noise_loud_enter_seconds=1, mode_change_grace_ms=800)
        analyzer = NoiseAnalyzer(settings, -50)
        loud = tone(.2)
        analyzer.feed(loud, now=0, mode=BaseMode.NOTICE, context_revision=1)
        first = analyzer.feed(loud, now=.1, mode=BaseMode.DISCUSSION, context_revision=2)
        self.assertEqual(first.state, NoiseState.UNKNOWN)
        analyzer.feed(loud, now=.9, mode=BaseMode.DISCUSSION, context_revision=2)
        analyzer.feed(loud, now=1.0, mode=BaseMode.DISCUSSION, context_revision=2)
        analyzer.feed(loud, now=1.1, mode=BaseMode.DISCUSSION, context_revision=2)
        stale = analyzer.feed(loud, now=2.0, mode=BaseMode.DISCUSSION, context_revision=2)
        self.assertNotEqual(stale.state, NoiseState.LOUD)


if __name__ == "__main__":
    unittest.main()
