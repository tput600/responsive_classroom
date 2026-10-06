import wave
import queue
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np

from classroom_audio import CommandParser, NoiseAnalyzer, SenseVoiceSpeech
from classroom_core import BaseMode, ClassroomController, Overlay, Settings


ROOT = Path(__file__).resolve().parents[1] / "resources"


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16)
        samples = samples.astype(np.float32) / 32768
        rate = source.getframerate()
    if rate != 16000:
        import soxr

        samples = soxr.resample(samples, rate, 16000)
    return samples


class AudioRuntimeQueueTests(unittest.TestCase):
    def test_pending_speech_queue_replaces_oldest_with_latest(self):
        from classroom_audio import AudioRuntime

        jobs = queue.Queue(maxsize=1)
        self.assertFalse(AudioRuntime._enqueue_latest_job(jobs, ("first", 1)))
        self.assertTrue(AudioRuntime._enqueue_latest_job(jobs, ("latest", 2)))
        self.assertEqual(jobs.qsize(), 1)
        self.assertEqual(jobs.get_nowait(), ("latest", 2))

    def test_stale_job_epoch_revision_and_age_are_rejected_before_decode(self):
        from classroom_audio import AudioRuntime

        revision = [4]
        runtime = AudioRuntime("", ROOT / "models" / "sensevoice",
                               Settings(voice_enabled=True), None, lambda: False,
                               lambda: revision[0], lambda *_: None, lambda *_: None,
                               lambda *_: None, lambda *_: None)
        now = time.monotonic()
        epoch = runtime._epoch
        self.assertTrue(runtime._job_is_current(4, epoch, now - 0.1, now=now))
        self.assertFalse(runtime._job_is_current(4, epoch, now - 3.01, now=now))
        self.assertFalse(runtime._job_is_current(4, epoch - 1, now, now=now))
        revision[0] = 5
        self.assertFalse(runtime._job_is_current(4, epoch, now, now=now))
        runtime.set_listening(False)
        self.assertFalse(runtime._job_is_current(5, runtime._epoch, now, now=now))

    def test_decoder_discards_expired_job_before_calling_inference(self):
        from classroom_audio import AudioRuntime

        checked, decode_calls = threading.Event(), []

        class CountingSpeech:
            def __init__(self, _model_dir):
                pass

            def decode(self, _samples):
                decode_calls.append(True)
                raise AssertionError("expired audio must not reach inference")

        runtime = AudioRuntime("", ROOT / "models" / "sensevoice",
                               Settings(voice_enabled=True), None, lambda: False,
                               lambda: 4, lambda *_: None, lambda *_: None,
                               lambda *_: None, lambda *_: None)
        original_check = runtime._job_is_current

        def observe_check(revision, epoch, queued_at, now=None):
            valid = original_check(revision, epoch, queued_at, now)
            checked.set()
            return valid

        runtime._job_is_current = observe_check
        with patch("classroom_audio.SenseVoiceSpeech", CountingSpeech):
            worker = threading.Thread(target=runtime._decode_loop)
            worker.start()
            deadline = time.monotonic() + 2
            while runtime._vad is None and time.monotonic() < deadline:
                time.sleep(.005)
            runtime._jobs.put((np.ones(16000, dtype=np.float32), 4,
                               runtime._epoch, time.monotonic() - 3.1))
            self.assertTrue(checked.wait(2))
            runtime._stop.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(decode_calls, [])


@unittest.skipUnless((ROOT / 'fixtures/en-command.wav').is_file() and
                     (ROOT / 'models/sensevoice/model.int8.onnx').is_file(),
                     'Private SAPI fixtures are not distributed in the public repository')
class SenseVoiceRuntimeTests(unittest.TestCase):
    def test_local_sensevoice_decodes_commands_and_ignores_silence(self):
        speech = SenseVoiceSpeech(ROOT / "models" / "sensevoice")
        parser = CommandParser(Settings())

        for filename, expected, intent in (("zh-command.wav", "课堂请注意老师。", "NOTICE"),
                                           ("en-command.wav", "Class question.", "QUESTION")):
            samples = read_wav(ROOT / "fixtures" / filename)
            self.assertEqual(speech.decode(samples), expected)
            self.assertEqual(parser.parse(expected, now=100 + len(filename)).intent, intent)
            if filename == "en-command.wav":
                self.assertEqual(speech.decode(samples * 0.05), expected)
            for amplitude in (1.0, 0.1):
                speech.reset()
                stream_samples = np.concatenate((np.zeros(16000, np.float32), samples * amplitude,
                                                 np.zeros(16000, np.float32)))
                segments = []
                for start in range(0, len(stream_samples), 160):
                    segments.extend(speech.accept(stream_samples[start:start + 160]))
                silence = np.zeros(160, dtype=np.float32)
                for _ in range(80):
                    segments.extend(speech.accept(silence))
                self.assertEqual(len(segments), 1, f"{filename} at amplitude {amplitude}")
                recognized = speech.decode(segments[0])
                self.assertEqual(recognized, expected,
                                 f"{filename} at {amplitude}, with one-second leading/trailing silence")
                result = parser.parse(recognized, now=200 + len(filename) + (1 - amplitude) * 10)
                self.assertEqual(result.intent, intent, f"{filename} at {amplitude}: {recognized!r}")
                speech.reset()
        silence = np.zeros(16000, dtype=np.float32)
        self.assertEqual(speech.decode(silence), "")
        self.assertEqual(speech.decode(np.full(16000, 1e-6, dtype=np.float32)), "")
        self.assertEqual(speech.accept(silence), [])

    def test_quiet_background_does_not_create_vad_segments_or_clip_normal_audio(self):
        speech = SenseVoiceSpeech(ROOT / "models" / "sensevoice")
        rng = np.random.default_rng(73)
        background = (rng.normal(0.0, 0.0018, 4 * 16000)).astype(np.float32)
        segments = []
        for start in range(0, len(background), 160):
            segments.extend(speech.accept(background[start:start + 160]))
        self.assertEqual(segments, [])
        normal = read_wav(ROOT / "fixtures" / "en-command.wav")
        class CaptureVad:
            def __init__(self):
                self.peaks = []

            def accept_waveform(self, chunk):
                self.peaks.append(float(np.max(np.abs(chunk), initial=0.0)))

            def empty(self):
                return True

        capture = CaptureVad()
        speech.vad = capture
        for start in range(0, len(normal), 160):
            speech.accept(normal[start:start + 160])
        self.assertLessEqual(max(capture.peaks), 0.95001)

    def test_450ms_pause_between_words_still_triggers_question(self):
        speech = SenseVoiceSpeech(ROOT / "models" / "sensevoice")
        samples = read_wav(ROOT / "fixtures" / "en-command.wav")
        split = int(0.55 * 16000)  # Quiet boundary between “Class” and “question” in the fixture.
        samples = np.concatenate((np.zeros(16000, np.float32), samples[:split],
                                  np.zeros(7200, np.float32), samples[split:],
                                  np.zeros(16000, np.float32)))
        segments = []
        for start in range(0, len(samples), 160):
            segments.extend(speech.accept(samples[start:start + 160]))
        for _ in range(80):
            segments.extend(speech.accept(np.zeros(160, dtype=np.float32)))
        self.assertGreaterEqual(len(segments), 1)
        parser = CommandParser(Settings())
        results = [parser.parse(speech.decode(segment), now=10 + index)
                   for index, segment in enumerate(segments)]
        self.assertIn("QUESTION", [result.intent for result in results])

    def test_synthetic_full_sentences_decode_and_wait_only_for_vad_tail(self):
        speech = SenseVoiceSpeech(ROOT / "models" / "sensevoice")
        parser = CommandParser(Settings(), cooldown_seconds=0)
        cases = (("en-sentence-question.wav", "QUESTION"),
                 ("en-sentence-wrong.wav", "WRONG"))
        for filename, intent in cases:
            with self.subTest(filename=filename):
                samples = read_wav(ROOT / "fixtures" / filename)
                # SAPI files contain a long padded tail; trim it to measure the VAD endpoint.
                frame_rms = [float(np.sqrt(np.mean(samples[i:i + 160] ** 2)))
                             for i in range(0, len(samples), 160)]
                last_speech = max(i for i, value in enumerate(frame_rms) if value > .002)
                samples = np.concatenate((np.zeros(16000, np.float32),
                                          samples[:(last_speech + 1) * 160]))
                segments = []
                for start in range(0, len(samples), 160):
                    segments.extend(speech.accept(samples[start:start + 160]))
                silence_frames = 0
                tail_started = time.monotonic()
                while not segments and silence_frames < 80:
                    time.sleep(.01)
                    segments.extend(speech.accept(np.zeros(160, np.float32)))
                    silence_frames += 1
                tail_seconds = time.monotonic() - tail_started
                self.assertEqual(len(segments), 1)
                self.assertGreaterEqual(silence_frames, 30)
                self.assertLess(tail_seconds, 0.45)
                decode_started = time.monotonic()
                recognized = speech.decode(segments[0])
                decode_seconds = time.monotonic() - decode_started
                actual_intent = parser.parse(recognized, now=10).intent
                self.assertEqual(actual_intent, intent, recognized)
                print(f"{filename}: intent={actual_intent}, tail={tail_seconds:.3f}s, "
                      f"decode={decode_seconds:.3f}s")
                speech.reset()

    def test_unclassified_noise_time_does_not_satisfy_next_entry_hold(self):
        analyzer = NoiseAnalyzer(Settings(speech_noise_guard=False, noise_rising_db=8,
                                          noise_loud_db=18, noise_rising_exit_db=5,
                                          noise_loud_exit_db=15,
                                          noise_rising_enter_seconds=1.5,
                                          noise_smoothing_ms=750), baseline_dbfs=-60)
        tone = np.sin(np.arange(1600) * 2 * np.pi * 440 / 16000).astype(np.float32) * 0.0045
        analyzer.feed(tone, now=0.0)
        analyzer.feed(tone, now=20.0, classify=False)
        analyzer.feed(tone, now=20.1)
        for t in range(201, 216):
            analyzer.feed(tone, now=t / 10)
        self.assertEqual(analyzer.state.name, "QUIET")
        analyzer.feed(tone, now=21.7)
        self.assertEqual(analyzer.state.name, "RISING")

    def test_decode_completed_after_listening_stops_is_discarded(self):
        from classroom_audio import AudioRuntime

        entered, release = threading.Event(), threading.Event()
        results = []

        class SlowSpeech:
            def __init__(self, _model_dir):
                pass

            def decode(self, _samples):
                entered.set()
                release.wait(2)
                return "class question"

        runtime = AudioRuntime("", ROOT / "models" / "sensevoice", Settings(voice_enabled=True),
                               None, lambda: False, lambda: 0, lambda *_: None,
                               lambda *_: None, lambda *_: None,
                               lambda result, revision: results.append((result, revision)))
        with patch("classroom_audio.SenseVoiceSpeech", SlowSpeech):
            worker = threading.Thread(target=runtime._decode_loop)
            worker.start()
            deadline = time.monotonic() + 2
            while runtime._vad is None and time.monotonic() < deadline:
                time.sleep(.01)
            runtime._jobs.put((np.ones(16000, dtype=np.float32), 0, runtime._epoch))
            self.assertTrue(entered.wait(2))
            runtime.set_listening(False)
            runtime._stop.set()
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [])

    def test_result_expiring_during_decode_is_discarded_before_command_parsing(self):
        from classroom_audio import AudioRuntime
        import queue
        for elapsed,expected in ((.1,1),(4.0,0)):
            with self.subTest(decode_seconds=elapsed):
                now=[10.0]
                results=[]
                runtime=AudioRuntime("",ROOT/"models"/"sensevoice",Settings(voice_enabled=True),
                                     None,lambda:False,lambda:0,lambda *_:None,
                                     lambda *_:None,lambda *_:None,lambda *r:results.append(r))
                class Speech:
                    def __init__(self,_): pass
                    def decode(self,_):
                        now[0]+=elapsed
                        return "question"
                job=(np.ones(16000,np.float32),0,runtime._epoch,10.0)
                def no_more_jobs(*_,**__):
                    runtime._stop.set()
                    raise queue.Empty
                get_calls=[0]
                def next_job(*args,**kwargs):
                    get_calls[0]+=1
                    return job if get_calls[0]==1 else no_more_jobs()
                with patch("classroom_audio.SenseVoiceSpeech",Speech), \
                     patch("classroom_audio.time.monotonic",side_effect=lambda:now[0]), \
                     patch.object(runtime._jobs,"get",side_effect=next_job):
                    runtime._decode_loop()
                self.assertEqual(len(results),expected)

    def test_default_device_falls_back_and_retries_after_all_candidates_fail(self):
        from classroom_audio import AudioRuntime

        runtime = AudioRuntime("", ROOT / "models" / "sensevoice", Settings(voice_enabled=False),
                               None, lambda: False, lambda: 0, lambda *_: None,
                               lambda *_: None, lambda *_: None, lambda *_: None)

        class FakeSoundDevice:
            default = SimpleNamespace(device=(0, -1))

            @staticmethod
            def query_devices():
                return [
                    {"name": "Array Mic", "max_input_channels": 1},
                    {"name": "Array Mic", "max_input_channels": 1},
                ]

        with patch.object(AudioRuntime, "microphones", return_value=[("0|Array Mic", "Array Mic")]):
            self.assertEqual(runtime._device(FakeSoundDevice), 0)
            runtime._failed_devices.add(0)
            self.assertEqual(runtime._device(FakeSoundDevice), 1)
            runtime._failed_devices.add(1)
            self.assertEqual(runtime._device(FakeSoundDevice), 0)
            self.assertEqual(runtime._failed_devices, set())

    def test_saved_microphone_missing_on_another_computer_uses_current_default(self):
        from classroom_audio import AudioRuntime

        runtime = AudioRuntime("7|Old laptop mic", ROOT / "models" / "sensevoice",
                               Settings(voice_enabled=False), None, lambda: False, lambda: 0,
                               lambda *_: None, lambda *_: None, lambda *_: None, lambda *_: None)

        class FakeSoundDevice:
            default = SimpleNamespace(device=(1, -1))

            @staticmethod
            def query_devices():
                return [{"name": "Laptop Array", "max_input_channels": 2},
                        {"name": "USB Mic", "max_input_channels": 1}]

        with patch.object(AudioRuntime, "microphones", return_value=[("1|USB Mic", "USB Mic")]):
            self.assertEqual(runtime._device(FakeSoundDevice), 1)
        self.assertTrue(runtime._device_fallback)

    def test_saved_microphone_that_cannot_open_falls_back_to_another_input(self):
        from classroom_audio import AudioRuntime

        runtime = AudioRuntime("0|USB Mic", ROOT / "models" / "sensevoice",
                               Settings(voice_enabled=False), None, lambda: False, lambda: 0,
                               lambda *_: None, lambda *_: None, lambda *_: None, lambda *_: None)
        runtime._failed_devices.add(0)

        class FakeSoundDevice:
            default = SimpleNamespace(device=(1, -1))

            @staticmethod
            def query_devices():
                return [{"name": "USB Mic", "max_input_channels": 1},
                        {"name": "Laptop Array", "max_input_channels": 2}]

        with patch.object(AudioRuntime, "microphones", return_value=[
                ("0|USB Mic", "USB Mic"), ("1|Laptop Array", "Laptop Array")]):
            self.assertEqual(runtime._device(FakeSoundDevice), 1)
        self.assertTrue(runtime._device_fallback)

    def test_stereo_capture_uses_the_active_channel_if_the_first_is_silent(self):
        from classroom_audio import AudioRuntime

        samples = np.column_stack((np.zeros(8, np.float32), np.full(8, .05, np.float32)))
        runtime = AudioRuntime("", ROOT / "models" / "sensevoice", Settings(), None,
                               lambda: False, lambda: 0, lambda *_: None, lambda *_: None,
                               lambda *_: None, lambda *_: None)
        np.testing.assert_array_equal(runtime._mono_capture_samples(samples), samples[:, 1])
        np.testing.assert_array_equal(runtime._mono_capture_samples(samples[:, :1]), samples[:, 0])

        runtime._capture_channel = None
        first = np.column_stack((np.full(8, .03), np.full(8, .02))).astype(np.float32)
        louder_other = np.column_stack((np.full(8, .015), np.full(8, .025))).astype(np.float32)
        np.testing.assert_array_equal(runtime._mono_capture_samples(first), first[:, 0])
        np.testing.assert_array_equal(runtime._mono_capture_samples(louder_other), louder_other[:, 0])

    def test_custom_cjk_prefix_and_embedded_complete_triggers(self):
        parser = CommandParser(Settings(command_prefix="小燈"))
        self.assertEqual(parser.parse("小燈提問", now=1).intent, "QUESTION")
        self.assertEqual(parser.parse("小燈 提問", now=4).intent, "QUESTION")
        self.assertEqual(parser.parse("小燈請問是否有提問", now=7).intent, "QUESTION")
        self.assertEqual(parser.parse("glassware question", now=10).intent, "QUESTION")

    def test_bare_english_and_chinese_mode_aliases_apply_to_controller(self):
        cases = (
            ("standby", "待機", "STANDBY", BaseMode.STANDBY, None),
            ("notice", "請注意老師", "NOTICE", BaseMode.NOTICE, None),
            ("question", "提問", "QUESTION", BaseMode.NOTICE, Overlay.QUESTION),
            ("Rest.", "休息。", "REST", BaseMode.REST, None),
            ("Correct.", "答對", "CORRECT", BaseMode.NOTICE, Overlay.CORRECT),
            ("Wrong.", "答錯", "WRONG", BaseMode.NOTICE, Overlay.WRONG),
        )
        for english, chinese, intent, base_mode, overlay in cases:
            for phrase in (english, chinese):
                with self.subTest(phrase=phrase):
                    parser = CommandParser(Settings())
                    result = parser.parse(phrase, now=1)
                    self.assertEqual(result.intent, intent)
                    controller = ClassroomController(Settings(), lambda *_: lambda: None)
                    controller.set_base_mode(BaseMode.NOTICE, source="test")
                    self.assertTrue(controller.submit_intent(
                        result.intent, source="voice", revision=controller.manual_revision))
                    self.assertEqual(controller.state.base_mode, base_mode)
                    self.assertEqual(controller.state.overlay, overlay)

    def test_embedded_aliases_share_intent_cooldown_and_last_trigger_wins(self):
        parser = CommandParser(Settings(), cooldown_seconds=2)
        self.assertEqual(parser.parse("class question", now=1).intent, "QUESTION")
        self.assertEqual(parser.parse("課堂 提問", now=2).reason, "command cooldown")
        self.assertEqual(parser.parse("課堂 提問", now=3.1).intent, "QUESTION")
        self.assertEqual(parser.parse("please ask a question now", now=6).intent, "QUESTION")
        self.assertEqual(parser.parse("wrong, the answer is correct", now=9).intent, "CORRECT")

    def test_stopped_model_load_does_not_publish_ready(self):
        from classroom_audio import AudioRuntime

        entered, release = threading.Event(), threading.Event()
        statuses = []

        class LoadingSpeech:
            def __init__(self, _model_dir):
                entered.set()
                release.wait(2)

        runtime = AudioRuntime("", ROOT / "models" / "sensevoice", Settings(voice_enabled=True),
                               None, lambda: False, lambda: 0,
                               lambda component, ok, text: statuses.append((component, ok, text)),
                               lambda *_: None, lambda *_: None, lambda *_: None)
        with patch("classroom_audio.SenseVoiceSpeech", LoadingSpeech), \
             patch.object(runtime, "_run", lambda: runtime._stop.wait()):
            runtime.start()
            self.assertTrue(entered.wait(2))
            runtime.stop(timeout=0)
            self.assertTrue(runtime.is_running)
            release.set()
            runtime._decoder.join(2)
            runtime._thread.join(2)
        self.assertFalse(runtime.is_running)
        self.assertFalse(any("已就緒" in message for _, _, message in statuses))
