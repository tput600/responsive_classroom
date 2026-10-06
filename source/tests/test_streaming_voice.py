import queue
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from classroom_audio import (AudioRuntime, CommandParser, RollingCommandBuffer,
                             SenseVoiceSpeech, SpeechJob)
from classroom_core import Settings
from tests.test_audio_runtime import read_wav


RESOURCES = Path(__file__).resolve().parents[1] / 'resources'


class CommandParserTests(unittest.TestCase):
    def test_preview_does_not_consume_command_cooldown(self):
        parser = CommandParser(Settings())
        self.assertEqual(parser.parse('question', now=10, commit=False).intent, 'QUESTION')
        self.assertEqual(parser.parse('question', now=10).intent, 'QUESTION')
        self.assertIsNone(parser.parse('question', now=10.1).intent)

    def test_stale_completed_segment_is_dropped_before_inference(self):
        runtime = AudioRuntime('', RESOURCES / 'models' / 'sensevoice',
                               Settings(voice_enabled=True), None, lambda: False,
                               lambda: 0, lambda *_: None, lambda *_: None,
                               lambda *_: None, lambda *_: None)
        calls = []

        class Speech:
            def __init__(self, _):
                pass

            def decode(self, _):
                calls.append(True)
                return 'question'

        jobs = iter((SpeechJob(np.ones(5120, np.float32), 0, runtime._epoch, 6.9),))

        def get(*_, **__):
            try:
                return next(jobs)
            except StopIteration:
                runtime._stop.set()
                raise queue.Empty

        with patch('classroom_audio.SenseVoiceSpeech', Speech), \
                patch('classroom_audio.time.monotonic', return_value=10), \
                patch.object(runtime._jobs, 'get', get):
            runtime._decode_loop()
        self.assertEqual(calls, [])

    def test_completed_segments_are_decoded_separately(self):
        results = []
        runtime = AudioRuntime('', RESOURCES / 'models' / 'sensevoice',
                               Settings(voice_enabled=True), None, lambda: False,
                               lambda: 0, lambda *_: None, lambda *_: None,
                               lambda *_: None, lambda result, _: results.append(result))
        texts = iter(('question', 'wrong'))

        class Speech:
            def __init__(self, _):
                pass

            def decode(self, _):
                return next(texts)

        samples = np.ones(5120, np.float32)
        jobs = iter(SpeechJob(samples, 0, runtime._epoch, time.monotonic()) for _ in range(2))

        def get(*_, **__):
            try:
                return next(jobs)
            except StopIteration:
                runtime._stop.set()
                raise queue.Empty

        with patch('classroom_audio.SenseVoiceSpeech', Speech), patch.object(runtime._jobs, 'get', get):
            runtime._decode_loop()
        self.assertEqual([r.intent for r in results], ['QUESTION', 'WRONG'])

    def test_live_command_is_applied_before_sentence_endpoint_without_partial_noise(self):
        results = []
        runtime = AudioRuntime('', RESOURCES / 'models' / 'sensevoice',
                               Settings(voice_enabled=True), None, lambda: False,
                               lambda: 0, lambda *_: None, lambda *_: None,
                               lambda *_: None, lambda result, _: results.append(result))
        now = time.monotonic()
        jobs = iter((SpeechJob(np.ones(5120, np.float32), 0, runtime._epoch, now, 1, True),
                     SpeechJob(np.ones(5120, np.float32), 0, runtime._epoch, now, 1, True),
                     SpeechJob(np.ones(5120, np.float32), 0, runtime._epoch, now, 1, False)))

        class Speech:
            def __init__(self, _): pass
            def decode(self, _): return next(texts)

        texts = iter(('class', 'question', 'question'))

        def get(*_, **__):
            try:
                return next(jobs)
            except StopIteration:
                runtime._stop.set()
                raise queue.Empty

        with patch('classroom_audio.SenseVoiceSpeech', Speech), patch.object(runtime._jobs, 'get', get):
            runtime._decode_loop()
        self.assertEqual([(result.text, result.intent) for result in results], [('question', 'QUESTION')])

    def test_rolling_buffer_emits_bounded_windows_during_a_sentence(self):
        buffer = RollingCommandBuffer()
        frames = np.ones(1600, np.float32)
        probes = [buffer.push(frames, True, 3) for _ in range(12)]
        probes = [probe for probe in probes if probe is not None]
        self.assertGreaterEqual(len(probes), 2)
        self.assertTrue(all(5120 <= len(samples) <= buffer.window_samples and utterance == 1 and revision == 3
                            for samples, utterance, revision in probes))


@unittest.skipUnless((RESOURCES / 'fixtures/en-command.wav').is_file() and
                     (RESOURCES / 'models/sensevoice/model.int8.onnx').is_file(),
                     'Private SAPI fixtures are not distributed in the public repository')
class StreamingModelTests(unittest.TestCase):
    def test_replayed_capture_recognizes_live_and_keeps_sentence_endpoint(self):
        ready, finished, accepted, endpoint_queued = (threading.Event() for _ in range(4))
        input_started = [0.0]
        first_result = []
        current_job = [None]
        readings = []
        statuses = []
        transcripts = []
        played, queued = [0], []
        fixture = read_wav(RESOURCES / 'fixtures' / 'en-command.wav')
        voiced = np.flatnonzero(np.abs(fixture) > .002)
        target_end = int(voiced[-1] + 1)
        waveform = np.concatenate((fixture[:target_end], np.zeros(16000, np.float32)))

        def status(kind, ok, _message):
            statuses.append((kind, ok, _message))
            if kind == 'speech' and ok:
                ready.set()

        def command(result, _revision):
            transcripts.append((result.text, result.intent, result.reason))
            if result.intent == 'QUESTION' and not first_result:
                first_result.append((time.perf_counter(), current_job[0].partial))
                accepted.set()

        runtime = AudioRuntime('', RESOURCES / 'models' / 'sensevoice',
                               Settings(voice_enabled=True), -60, lambda: True,
                               lambda: 0, status, readings.append, lambda *_: None, command)
        enqueue = runtime._enqueue_latest_job
        def observe_enqueue(jobs, job):
            queued.append((len(job.samples), job.revision, job.epoch, job.partial))
            if not job.partial:
                endpoint_queued.set()
            return enqueue(jobs, job)
        runtime._enqueue_latest_job = observe_enqueue
        get_job = runtime._jobs.get
        def observe_get(*args, **kwargs):
            current_job[0] = get_job(*args, **kwargs)
            return current_job[0]
        runtime._jobs.get = observe_get

        class ReplayStream:
            active = True

            def __init__(self, **kwargs):
                self.callback = kwargs['callback']
                self.stop = threading.Event()

            def __enter__(self):
                def replay():
                    # A real microphone keeps sending frames while the model
                    # loads. Do not simulate a disconnected device during warmup.
                    while not ready.is_set() and not self.stop.is_set():
                        self.callback(np.full((160, 1), .00001, np.float32), 160, None,
                                      SimpleNamespace(input_overflow=False))
                        self.stop.wait(.01)
                    if self.stop.is_set():
                        return
                    input_started[0] = time.perf_counter()
                    for offset in range(0, len(waveform), 160):
                        if self.stop.is_set():
                            break
                        frame = waveform[offset:offset + 160]
                        played[0] += 1
                        self.callback(frame[:, None], len(frame), None,
                                      SimpleNamespace(input_overflow=False))
                        self.stop.wait(.01)
                    finished.set()
                self.thread = threading.Thread(target=replay)
                self.thread.start()
                return self

            def __exit__(self, *_):
                self.active = False
                self.stop.set()
                self.thread.join(2)

        with patch('sounddevice.InputStream', ReplayStream), \
                patch.object(runtime, '_device', return_value=0), \
                patch.object(runtime, '_input_channels', return_value=1), \
                patch.object(runtime, '_supports', side_effect=lambda _sd, _device, rate, _channels: rate == 16000):
            runtime.start()
            try:
                self.assertTrue(ready.wait(8), f'model not ready: {statuses}')
                self.assertTrue(accepted.wait(5), f'live probe never applied QUESTION: {statuses}; {transcripts}; readings={len(readings)}; played={played}; queued={queued}; listening={runtime._listen.is_set()}')
                self.assertTrue(endpoint_queued.wait(5), f'VAD endpoint was not queued: {statuses}; {transcripts}; played={played}; queued={queued}')
                self.assertTrue(finished.wait(5))
                lag = first_result[0][0] - input_started[0] - target_end / 16000
                self.assertGreater(lag, -1.0)
                self.assertLess(lag, 1.0)
                self.assertTrue(first_result[0][1])  # Command came from a rolling window, not the endpoint.
            finally:
                runtime.stop(2)
        self.assertFalse(runtime.is_running)
        self.assertTrue(queued)
        noise_readings = [item for item in readings if item.relative_db is not None]
        self.assertTrue(noise_readings)
        self.assertTrue(any(item.relative_db > 8 for item in noise_readings))
        print(f'replayed capture: QUESTION response after fixture end={lag:.3f}s; '
              f'{len(noise_readings)} level-only meter updates')


if __name__ == '__main__':
    unittest.main()
