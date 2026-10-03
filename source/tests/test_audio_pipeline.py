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

from classroom_audio import (AudioRuntime, CaptureFrame, NoiseAnalyzer, RollingCommandBuffer,
                             SpeechJob, SpeechJobQueue)
from classroom_core import BaseMode, NoiseState, Settings


class PipelineTests(unittest.TestCase):
    def runtime(self, results, revision=lambda: 0):
        return AudioRuntime('', Path('.'), Settings(voice_enabled=True), -60,
                            lambda: True, revision, lambda *_: None, lambda *_: None,
                            lambda *_: None, lambda result, _: results.append(result))

    def test_long_pause_retains_only_bounded_onset_preroll(self):
        buffer = RollingCommandBuffer()
        for _ in range(120):
            buffer.push(np.ones(160, np.float32), True, 1)
        for _ in range(60):
            buffer.push(np.zeros(160, np.float32), False, 2)
        item = buffer.push(np.full(160, .1, np.float32), True, 2)
        self.assertEqual(item[1:], (2, 2))
        self.assertLessEqual(len(item[0]), buffer.preroll_samples + 160)
        self.assertLess(float(np.max(item[0])), .11)  # no previous command waveform

    def test_endpoint_closes_identity_even_for_short_gap(self):
        buffer = RollingCommandBuffer()
        for _ in range(40):
            buffer.push(np.ones(160, np.float32), True, 1)
        buffer.finish()
        for _ in range(40):
            item = buffer.push(np.full(160, .2, np.float32), True, 2)
            if item is not None:
                self.assertEqual(item[1:], (2, 2))
                np.testing.assert_array_equal(item[0], np.full(len(item[0]), .2, np.float32))

    def test_final_survives_live_backpressure_and_keeps_bounded_queue(self):
        jobs = SpeechJobQueue()
        final = SpeechJob(None, 0, 1, 10, 1)
        jobs.put_latest(final)
        for i in range(100):
            jobs.put_latest(SpeechJob(i, 0, 1, 10, 2, True))
        self.assertEqual(jobs.qsize(), 2)
        self.assertIs(jobs.get_nowait(), final)
        self.assertEqual(jobs.get_nowait().samples, 99)
        with self.assertRaises(queue.Empty):
            jobs.get_nowait()

    def test_same_utterance_endpoint_replaces_partial_and_cannot_be_erased(self):
        jobs = SpeechJobQueue()
        jobs.put_latest(SpeechJob('live', 0, 1, 10, 1, True))
        jobs.put_latest(SpeechJob('final', 0, 1, 10, 1))
        jobs.put_latest(SpeechJob('late live', 0, 1, 10, 1, True))
        self.assertEqual(jobs.qsize(), 1)
        self.assertEqual(jobs.get_nowait().samples, 'final')

    def test_overload_rejects_even_fresh_jobs_until_recovery(self):
        runtime = self.runtime([])
        now = time.monotonic()
        self.assertTrue(runtime._job_is_current(0, runtime._epoch, now, now=now))
        runtime.capture_overloaded = True
        self.assertFalse(runtime._job_is_current(0, runtime._epoch, now, now=now))
        runtime.capture_overloaded = False
        self.assertTrue(runtime._job_is_current(0, runtime._epoch, now, now=now))

    def test_manual_revision_during_decode_does_not_consume_next_command(self):
        results, revision = [], [1]
        runtime = self.runtime(results, lambda: revision[0])
        class Speech:
            def __init__(self, _): pass
            def decode(self, _):
                revision[0] = 2
                return 'question'
        jobs = iter(SpeechJob(None, rev, runtime._epoch, 10, rev) for rev in (1, 2))
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
            runtime._jobs.put_latest(SpeechJob(np.ones(5120), 1, runtime._epoch,
                                              time.monotonic(), 1))
            self.assertTrue(entered.wait(2))
            revision[0] = 2
            started = time.monotonic()
            runtime.stop()
            self.assertLess(time.monotonic() - started, .1)
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [])

    def test_stale_capture_is_dropped_and_gap_resets_vad_before_new_samples(self):
        runtime = self.runtime([])
        accepted, resets = [], []

        class Vad:
            active = False
            noise_speech_active = False
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
        frames = iter((CaptureFrame(np.ones(160, np.float32), 9, 1),
                       CaptureFrame(np.full(160, .1, np.float32), 10, 2),
                       CaptureFrame(np.full(160, .2, np.float32), 10, 4)))
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
        self.assertEqual(len(accepted), 2)
        self.assertEqual(runtime.capture_dropped, 1)
        self.assertEqual(len(resets), 3)  # open, stale capture, missing sequence
        self.assertEqual(runtime.latest.captured_at, 10)

    def test_sustained_overload_is_visible_and_recovers_after_fresh_audio(self):
        runtime = self.runtime([])
        statuses, readings, accepted = [], [], []
        runtime.on_status = lambda *args: statuses.append(args)
        runtime.on_noise = readings.append
        now = [10.0]
        class Vad:
            active = False
            noise_speech_active = False
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
                                now[0] - lag, count[0])
        with patch.dict('sys.modules', {'sounddevice': SimpleNamespace(InputStream=Stream)}), \
                patch.object(runtime, '_device', return_value=0), \
                patch.object(runtime, '_input_channels', return_value=1), \
                patch.object(runtime, '_supports', side_effect=lambda _sd, _dev, rate, _ch: rate == 16000), \
                patch.object(runtime._queue, 'get', get), \
                patch('classroom_audio.time.monotonic', side_effect=lambda: now[0]):
            runtime._run()
        self.assertEqual(runtime.capture_dropped, 100)
        self.assertEqual(len(accepted), 70)  # fresh samples are not starved
        self.assertFalse(runtime.capture_overloaded)
        self.assertEqual(runtime.capture_lag_seconds, 0)
        self.assertTrue(any(not r.speech_guard_ready for r in readings))
        self.assertTrue(readings[-1].speech_guard_ready)
        self.assertTrue(any(not ok and '落後' in text for _, ok, text in statuses))
        self.assertTrue(statuses[-1][1])

    def test_notice_pause_recovery_discussion_and_unavailable_guard(self):
        settings = Settings(noise_smoothing_ms=200, noise_loud_enter_seconds=.2)
        samples = np.random.default_rng(42).normal(0, .1, 480).astype(np.float32)
        analyzer = NoiseAnalyzer(settings, -60)
        for i in range(200):
            reading = analyzer.feed(samples, now=i * .01, speech_active=True)
        self.assertTrue(reading.speech_excluded)
        self.assertEqual(reading.state, NoiseState.QUIET)
        # A short inter-word pause must not turn noise classification on.
        for i in range(200, 220):
            reading = analyzer.feed(samples, now=i * .01, speech_active=False)
            self.assertTrue(reading.speech_excluded)
        for i in range(220, 300):
            reading = analyzer.feed(samples, now=i * .01, speech_active=False)
        self.assertEqual(reading.state, NoiseState.LOUD)
        self.assertAlmostEqual(reading.captured_at, 2.99)
        for i in range(300, 450):
            reading = analyzer.feed(samples, now=i * .01, speech_active=True,
                                    mode=BaseMode.DISCUSSION)
        self.assertFalse(reading.speech_excluded)
        self.assertEqual(reading.state, NoiseState.LOUD)
        reading = analyzer.feed(samples, now=4.5, speech_active=None,
                                mode=BaseMode.DISCUSSION)
        self.assertFalse(reading.speech_guard_ready)
        self.assertFalse(reading.speech_excluded)


if __name__ == '__main__':
    unittest.main()
