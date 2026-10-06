"""Model-independent regressions: these must run without private speech assets."""
import queue
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soxr  # load extension before sys.modules mocks to avoid re-registering it

from classroom_audio import AudioRuntime, CaptureFrame, NoiseAnalyzer, SpeechJob
from classroom_core import BaseMode, NoiseState, Settings


class PipelineTests(unittest.TestCase):
    def runtime(self, results, revision=lambda: 0):
        return AudioRuntime('', Path('.'), Settings(voice_enabled=True), -60,
                            lambda: True, revision, lambda *_: None, lambda *_: None,
                            lambda *_: None, lambda result, _: results.append(result))

    def test_capture_lag_does_not_invalidate_a_completed_segment(self):
        runtime = self.runtime([])
        now = time.monotonic()
        self.assertTrue(runtime._job_is_current(0, runtime._epoch, now, now=now))
        runtime.capture_lag_seconds = 1.0
        self.assertTrue(runtime._job_is_current(0, runtime._epoch, now, now=now))

    def test_manual_revision_during_decode_does_not_consume_next_command(self):
        results, revision = [], [1]
        runtime = self.runtime(results, lambda: revision[0])
        class Speech:
            def __init__(self, _): pass
            def decode(self, _):
                revision[0] = 2
                return 'question'
        jobs = iter(SpeechJob(None, rev, runtime._epoch, 10) for rev in (1, 2))
        def get(*_, **__):
            try:
                return next(jobs)
            except StopIteration:
                runtime._stop.set()
                raise queue.Empty
        with patch('classroom_audio.SenseVoiceSpeech', Speech), \
                patch.object(runtime._jobs, 'get', get), \
                patch('classroom_audio.time.monotonic', return_value=10):
            runtime._decode_loop()
        self.assertEqual([r.intent for r in results], ['QUESTION'])

    def test_manual_override_during_decode_and_nonblocking_stop(self):
        entered, release = threading.Event(), threading.Event()
        results, revision = [], [1]
        runtime = self.runtime(results, lambda: revision[0])

        class Speech:
            def __init__(self, _): pass
            def decode(self, _):
                entered.set()
                release.wait(2)
                return 'question'

        with patch('classroom_audio.SenseVoiceSpeech', Speech):
            worker = threading.Thread(target=runtime._decode_loop)
            worker.start()
            runtime._jobs.put_nowait(SpeechJob(np.ones(5120), 1, runtime._epoch,
                                               time.monotonic()))
            self.assertTrue(entered.wait(2))
            revision[0] = 2
            started = time.monotonic()
            runtime.stop()
            self.assertLess(time.monotonic() - started, .1)
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [])

    def test_lagged_and_discontinuous_capture_keeps_vad_running(self):
        runtime = self.runtime([])
        accepted, resets = [], []

        class Vad:
            active = False
            def reset(self): resets.append(True)
            def accept(self, samples):
                accepted.append(samples.copy())
                return []

        class Stream:
            active = True
            def __init__(self, **_): pass
            def __enter__(self): return self
            def __exit__(self, *_): pass

        runtime._vad = Vad()
        frames = iter((CaptureFrame(np.ones(160, np.float32), 9),
                       CaptureFrame(np.full(160, .1, np.float32), 10),
                       CaptureFrame(np.full(160, .2, np.float32), 10)))
        def get(*_, **__):
            try:
                return next(frames)
            except StopIteration:
                runtime._stop.set()
                raise queue.Empty
        with patch.dict('sys.modules', {'sounddevice': SimpleNamespace(InputStream=Stream)}), \
                patch.object(runtime, '_device', return_value=0), \
                patch.object(runtime, '_input_channels', return_value=1), \
                patch.object(runtime, '_supports', side_effect=lambda _sd, _dev, rate, _ch: rate == 16000), \
                patch.object(runtime._queue, 'get', get), \
                patch('classroom_audio.time.monotonic', return_value=10):
            runtime._run()
        self.assertEqual(len(accepted), 3)
        self.assertEqual(runtime.capture_dropped, 0)
        self.assertEqual(len(resets), 1)  # only the initial stream setup resets the VAD
        self.assertEqual(runtime.latest.captured_at, 10)

    def test_lagged_audio_keeps_meter_and_vad_live_without_error_status(self):
        runtime = self.runtime([])
        statuses, readings, accepted = [], [], []
        runtime.on_status = lambda *args: statuses.append(args)
        runtime.on_noise = readings.append
        now = [10.0]
        class Vad:
            active = False
            def reset(self): pass
            def accept(self, samples):
                accepted.append(len(samples))
                return []
        class Stream:
            active = True
            def __init__(self, **_): pass
            def __enter__(self): return self
            def __exit__(self, *_): pass
        runtime._vad = Vad()
        count = [0]
        def get(*_, **__):
            count[0] += 1
            now[0] = 10 + count[0] / 100
            if count[0] > 170:
                runtime._stop.set()
                raise queue.Empty
            lag = 1 if count[0] <= 100 else 0
            return CaptureFrame(np.tile([.01, -.01], 80).astype(np.float32),
                                now[0] - lag)
        with patch.dict('sys.modules', {'sounddevice': SimpleNamespace(InputStream=Stream)}), \
                patch.object(runtime, '_device', return_value=0), \
                patch.object(runtime, '_input_channels', return_value=1), \
                patch.object(runtime, '_supports', side_effect=lambda _sd, _dev, rate, _ch: rate == 16000), \
                patch.object(runtime._queue, 'get', get), \
                patch('classroom_audio.time.monotonic', side_effect=lambda: now[0]):
            runtime._run()
        self.assertEqual(runtime.capture_dropped, 0)
        self.assertEqual(len(accepted), 170)
        self.assertEqual(runtime.capture_lag_seconds, 0)
        self.assertFalse(any(not ok and '落後' in text for _, ok, text in statuses))
        self.assertTrue(statuses[-1][1])

    def test_noise_level_detection_counts_all_measured_audio(self):
        settings = Settings(noise_smoothing_ms=200, noise_loud_enter_seconds=.2)
        samples = np.random.default_rng(42).normal(0, .1, 480).astype(np.float32)
        analyzer = NoiseAnalyzer(settings, -60)
        for i in range(40):
            reading = analyzer.feed(samples, now=i * .01, mode=BaseMode.NOTICE)
        self.assertEqual(reading.state, NoiseState.LOUD)
        self.assertGreater(reading.relative_db, settings.noise_loud_db)
        self.assertAlmostEqual(reading.captured_at, .39)


if __name__ == '__main__':
    unittest.main()
