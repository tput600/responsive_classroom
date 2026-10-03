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


class RollingBufferTests(unittest.TestCase):
    def test_probes_start_before_full_window_and_keep_bounded_context(self):
        buffer = RollingCommandBuffer()
        windows = []
        for step in range(250):
            item = buffer.push(np.full(160, step, np.float32), step >= 20, 3)
            if item is not None:
                windows.append((step, item))
        self.assertEqual(windows[0][0], 31)  # 320 ms, not a 1.2 second startup wait.
        self.assertEqual(windows[1][0] - windows[0][0], 25)
        self.assertTrue(all(len(item[0]) <= 19200 for _, item in windows))
        self.assertEqual(len(windows[-1][1][0]), 19200)
        self.assertEqual(buffer._size, 19200)
        self.assertTrue(all(item[1:] == (1, 3) for _, item in windows))

    def test_quiet_has_no_jobs_and_manual_revision_stays_with_started_utterance(self):
        buffer = RollingCommandBuffer()
        frame = np.ones(160, np.float32)
        for _ in range(100):
            self.assertIsNone(buffer.push(frame, False, 3))
        for _ in range(40):
            buffer.push(frame, True, 3)
        for _ in range(100):
            item = buffer.push(frame, True, 4)
            if item is not None:
                self.assertEqual(item[2], 3)
        for _ in range(60):
            buffer.push(frame, False, 4)
        item = buffer.push(frame, True, 4)
        self.assertEqual(item[1:], (2, 4))

    def test_preview_does_not_consume_command_cooldown(self):
        parser = CommandParser(Settings())
        self.assertEqual(parser.parse('question', now=10, commit=False).intent, 'QUESTION')
        self.assertEqual(parser.parse('question', now=10).intent, 'QUESTION')
        self.assertIsNone(parser.parse('question', now=10.1).intent)

    def test_delayed_live_window_is_dropped_before_inference(self):
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

        jobs = iter((SpeechJob(np.ones(5120, np.float32), 0, runtime._epoch, 9.1, 1, True),))

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

    def test_endpoint_does_not_replay_command_accepted_in_live_window(self):
        results = []
        runtime = AudioRuntime('', RESOURCES / 'models' / 'sensevoice',
                               Settings(voice_enabled=True), None, lambda: False,
                               lambda: 0, lambda *_: None, lambda *_: None,
                               lambda *_: None, lambda result, _: results.append(result))
        texts = iter(('question', 'question', 'wrong'))

        class Speech:
            def __init__(self, _):
                pass

            def decode(self, _):
                return next(texts)

        samples = np.ones(5120, np.float32)
        jobs = iter(SpeechJob(samples, 0, runtime._epoch, time.monotonic(), utterance, partial)
                    for utterance, partial in ((1, True), (1, False), (2, True)))

        def get(*_, **__):
            try:
                return next(jobs)
            except StopIteration:
                runtime._stop.set()
                raise queue.Empty

        with patch('classroom_audio.SenseVoiceSpeech', Speech), patch.object(runtime._jobs, 'get', get):
            runtime._decode_loop()
        self.assertEqual([r.intent for r in results], ['QUESTION', 'WRONG'])


@unittest.skipUnless((RESOURCES / 'fixtures/en-command.wav').is_file() and
                     (RESOURCES / 'models/sensevoice/model.int8.onnx').is_file(),
                     'Private SAPI fixtures are not distributed in the public repository')
class StreamingModelTests(unittest.TestCase):
    def test_noise_vad_detects_quiet_voice_and_rejects_louder_fixed_background(self):
        speech = SenseVoiceSpeech(RESOURCES / 'models' / 'sensevoice')
        for filename in ('en-command.wav', 'zh-command.wav'):
            speech.reset()
            samples = read_wav(RESOURCES / 'fixtures' / filename) * .03
            detected = 0
            for offset in range(0, len(samples), 160):
                speech.accept(samples[offset:offset + 160])
                detected += speech.noise_speech_active
            self.assertGreater(detected, 10, filename)
        speech.reset()
        background = np.random.default_rng(73).normal(0, .03, 32000).astype(np.float32)
        for offset in range(0, len(background), 160):
            speech.accept(background[offset:offset + 160])
            self.assertFalse(speech.noise_speech_active)

    def test_raw_noise_vad_receives_unamplified_samples(self):
        speech = SenseVoiceSpeech(RESOURCES / 'models' / 'sensevoice')

        class Capture:
            def __init__(self):
                self.waveform = None

            def accept_waveform(self, values):
                self.waveform = values.copy()

            def empty(self):
                return True

        speech.noise_vad, speech.vad = Capture(), Capture()
        samples = np.sin(np.arange(512) * .12).astype(np.float32) * .015
        speech.accept(samples)
        np.testing.assert_array_equal(speech.noise_vad.waveform, samples)
        self.assertGreater(np.max(np.abs(speech.vad.waveform)), .1)

    def test_rolling_fixture_commands_arrive_before_continuous_speech_endpoint(self):
        speech = SenseVoiceSpeech(RESOURCES / 'models' / 'sensevoice')
        cases = (('en-command.wav', 'QUESTION'),
                 ('en-sentence-question.wav', 'QUESTION'),
                 ('en-sentence-wrong.wav', 'WRONG'),
                 ('zh-command.wav', 'NOTICE'))
        continuation = read_wav(RESOURCES / 'fixtures' / 'en-discussion.wav')
        # Remove silence padding before appending more speech: the decoder must
        # act while the utterance is ongoing rather than wait for a final pause.
        voiced = np.flatnonzero(np.abs(continuation) > .002)
        continuation = continuation[voiced[0]:voiced[-1] + 1]
        for filename, intent in cases:
            with self.subTest(fixture=filename):
                speech.reset()
                audio = read_wav(RESOURCES / 'fixtures' / filename)
                voiced = np.flatnonzero(np.abs(audio) > .002)
                end = int(voiced[-1] + 1)
                stream = np.concatenate((audio[:end], continuation, continuation))
                buffer = RollingCommandBuffer()
                parser = CommandParser(Settings(), cooldown_seconds=0)
                accepted = None
                for offset in range(0, len(stream), 160):
                    frame = stream[offset:offset + 160]
                    speech.accept(frame)
                    item = buffer.push(frame, speech.active, 0)
                    if item is None:
                        continue
                    started = time.perf_counter()
                    text = speech.decode(item[0])
                    decoding = time.perf_counter() - started
                    if parser.parse(text).intent == intent:
                        audio_end = (offset + len(frame)) / 16000
                        accepted = audio_end, decoding, text
                        break
                self.assertIsNotNone(accepted, filename)
                self.assertLess(accepted[0], len(stream) / 16000 - .2)
                # Relative to the acoustic end of the target fixture; an early
                # decision is allowed. This is a local synthetic-audio bound.
                lag = max(0, accepted[0] - end / 16000) + accepted[1]
                self.assertLess(lag, 1.0, (filename, accepted))
                print(f'rolling {filename}: first={accepted[0]:.3f}s, '
                      f'decode={accepted[1]:.3f}s, post-fixture lag={lag:.3f}s, '
                      f'text={accepted[2]!r}')

    def test_replayed_capture_and_decoder_apply_command_without_waiting_for_endpoint(self):
        ready, finished, accepted = threading.Event(), threading.Event(), threading.Event()
        input_started = [0.0]
        first_result = []
        readings = []
        statuses = []
        transcripts = []
        played, queued = [0], []
        fixture = read_wav(RESOURCES / 'fixtures' / 'en-command.wav')
        voiced = np.flatnonzero(np.abs(fixture) > .002)
        target_end = int(voiced[-1] + 1)
        continuation = read_wav(RESOURCES / 'fixtures' / 'en-sentence-question.wav')
        voiced = np.flatnonzero(np.abs(continuation) > .002)
        continuation = continuation[voiced[0]:voiced[-1] + 1]
        waveform = np.concatenate((fixture[:target_end], continuation, continuation))

        def status(kind, ok, _message):
            statuses.append((kind, ok, _message))
            if kind == 'speech' and ok:
                ready.set()

        def command(result, _revision):
            transcripts.append((result.text, result.intent, result.reason))
            if result.intent == 'QUESTION' and not first_result:
                first_result.append(time.perf_counter())
                accepted.set()

        runtime = AudioRuntime('', RESOURCES / 'models' / 'sensevoice',
                               Settings(voice_enabled=True), -60, lambda: True,
                               lambda: 0, status, readings.append, lambda *_: None, command)
        enqueue = runtime._enqueue_latest_job
        def observe_enqueue(jobs, job):
            queued.append((len(job.samples), job.partial, job.revision, job.epoch))
            return enqueue(jobs, job)
        runtime._enqueue_latest_job = observe_enqueue

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
                self.assertTrue(accepted.wait(3), f'live queue never applied QUESTION: {statuses}; {transcripts}; readings={len(readings)}; played={played}; queued={queued}; listening={runtime._listen.is_set()}')
                lag = first_result[0] - input_started[0] - target_end / 16000
                self.assertLess(lag, 1.0)
                self.assertTrue(finished.wait(5))
            finally:
                runtime.stop(2)
        self.assertFalse(runtime.is_running)
        speech_readings = [item for item in readings if item.speech_excluded]
        self.assertTrue(speech_readings)
        self.assertTrue(all(item.state.name == 'QUIET' for item in speech_readings))
        print(f'replayed capture: QUESTION response after fixture end={lag:.3f}s; '
              f'{len(speech_readings)} voice-excluded meter updates')


if __name__ == '__main__':
    unittest.main()
