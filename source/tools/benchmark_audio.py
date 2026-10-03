"""Offline reproducible SYNTHETIC audio smoke benchmark (never opens a microphone).

Optional development dependency: espeakng-loader==0.2.4 (GPL-3.0 eSpeak NG;
not bundled with the product). All utterance text below is original, with no
recorded speaker. Generated waveforms remain local. Run both revisions with
identical Python, models and machine, e.g.:
  python tools/benchmark_audio.py --module classroom_audio.py --models ... --output new.json
This measures synthetic fixtures, NOT classroom accuracy or hardware latency.
C API reference: https://github.com/espeak-ng/espeak-ng/blob/master/src/include/espeak-ng/speak_lib.h
"""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import sys
import time
import threading
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soxr


def fixtures():
    import espeakng_loader
    lib = espeakng_loader.load_library()
    if lib is None:
        raise RuntimeError('eSpeak NG library unavailable')
    lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.espeak_Synth.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint,
                                ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                ctypes.c_void_p, ctypes.c_void_p]
    root = str(Path(espeakng_loader.get_data_path()).parent).encode()
    rate = lib.espeak_Initialize(2, 0, root, 0)  # synchronous retrieval, no speaker
    if rate <= 0:
        raise RuntimeError('eSpeak NG initialization failed')
    chunks = []

    @ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)
    def callback(wav, count, _events):
        if wav and count:
            chunks.append(np.ctypeslib.as_array(wav, shape=(count,)).copy())
        return 0

    lib.espeak_SetSynthCallback(callback)
    if lib.espeak_SetVoiceByName(b'en-us') != 0:
        raise RuntimeError('eSpeak en-us voice unavailable')
    lib.espeak_SetParameter(1, 150, 0)
    for name, text, intent in (
        ('question', 'Question.', 'QUESTION'), ('notice', 'Notice.', 'NOTICE'),
        ('wrong', 'Wrong.', 'WRONG'), ('sentence', 'The answer is correct.', 'CORRECT'),
        ('continuous', 'Class question and please tell me what you think about this answer.', 'QUESTION'),
        ('pause', 'Class. Question.', 'QUESTION'),
    ):
        chunks.clear()
        data = text.encode()
        if lib.espeak_Synth(data, len(data) + 1, 0, 1, 0, 1, None, None) != 0:
            raise RuntimeError('eSpeak synthesis failed')
        samples = soxr.resample(np.concatenate(chunks).astype(np.float32) / 32768, rate, 16000)
        yield name, samples, intent


def replay_runtime(module, models, samples, expected):
    """Wall-clock synthetic callback-to-command test, not physical-device latency."""
    from classroom_core import Settings
    ready, accepted = threading.Event(), threading.Event()
    origin, response = [], []
    def status(kind, ok, text):
        if kind == 'speech' and ok and '已就緒' in text:
            ready.set()
    def command(result, _revision):
        if result.intent == expected and not response:
            response.append(time.perf_counter())
            accepted.set()
    runtime = module.AudioRuntime('', models, Settings(voice_enabled=True), -60,
                                  lambda: True, lambda: 0, status, lambda *_: None,
                                  lambda *_: None, command)
    class Stream:
        active = True
        def __init__(self, **kwargs):
            self.callback = kwargs['callback']
            self.stop = threading.Event()
        def __enter__(self):
            def run():
                flags = SimpleNamespace(input_overflow=False)
                while not ready.is_set() and not self.stop.is_set():
                    self.callback(np.zeros((160, 1), np.float32), 160, None, flags)
                    self.stop.wait(.01)
                origin.append(time.perf_counter())
                for offset in range(0, len(samples), 160):
                    if self.stop.is_set():
                        break
                    block = samples[offset:offset + 160]
                    self.callback(block[:, None], len(block), None, flags)
                    self.stop.wait(.01)
                while not self.stop.is_set():
                    self.callback(np.zeros((160, 1), np.float32), 160, None, flags)
                    self.stop.wait(.01)
            self.thread = threading.Thread(target=run)
            self.thread.start()
            return self
        def __exit__(self, *_):
            self.stop.set()
            self.thread.join(2)
            self.active = False
    # Avoid importing PortAudio: this is explicitly a synthetic callback source.
    with patch.dict('sys.modules', {'sounddevice': SimpleNamespace(InputStream=Stream)}), \
            patch.object(runtime, '_device', return_value=0), \
            patch.object(runtime, '_input_channels', return_value=1), \
            patch.object(runtime, '_supports', side_effect=lambda _sd, _dev, rate, _ch: rate == 16000):
        runtime.start()
        try:
            if not ready.wait(10):
                raise RuntimeError('Offline model did not load in replay')
            accepted.wait(len(samples) / 16000 + 3)
        finally:
            runtime.stop(5)
    if runtime.is_running:
        raise RuntimeError('Replay worker did not stop')
    return {'callback_to_first_command_s': response[0] - origin[0] if response and origin else None,
            'capture_dropped': getattr(runtime, 'capture_dropped', None)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', type=Path, default=Path(__file__).resolve().parents[1] / 'classroom_audio.py')
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.module.resolve().parent))
    # A baseline module may be copied beside the current source. Core Settings
    # remain the same across both runs to isolate audio implementation changes.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    spec = importlib.util.spec_from_file_location('bench_audio', args.module)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    import sherpa_onnx
    from classroom_core import Settings
    speech = module.SenseVoiceSpeech(args.models)
    cases = []
    for name, samples, intent in fixtures():
        waveform = np.concatenate((np.zeros(8000, np.float32), samples, np.zeros(16000, np.float32)))
        voiced = np.flatnonzero(np.abs(waveform) > .002)
        for variant in ('clean', 'quiet', 'snr10'):
            values = waveform.copy()
            if variant == 'quiet':
                values *= .1
            elif variant == 'snr10':
                rms = np.sqrt(np.mean(samples * samples))
                values += np.random.default_rng(73).normal(0, rms / np.sqrt(10), len(values)).astype(np.float32)
            cases.append((f'{name}/{variant}', values, intent, voiced[0] / 16000, voiced[-1] / 16000))
    t = np.arange(48000, dtype=np.float32) / 16000
    rng = np.random.default_rng(73)
    for name, samples in (
        ('white_noise', rng.normal(0, .03, len(t)).astype(np.float32)),
        ('fan_hum', (.05 * np.sin(2 * np.pi * 120 * t)).astype(np.float32)),
        ('impacts', (rng.normal(0, .1, len(t)) * ((t % .5) < .025)).astype(np.float32)),
        ('silence', np.zeros(len(t), np.float32)),
    ):
        cases.append((name, samples, None, None, None))
    results = []
    for name, samples, expected, acoustic_onset, acoustic_end in cases:
        speech.reset()
        rolling = module.RollingCommandBuffer()
        commands = module.CommandParser(Settings(), cooldown_seconds=0)
        first = onset = last_guard = endpoint = None
        transcripts, timings = [], []
        for offset in range(0, len(samples), 160):
            frame = samples[offset:offset + 160]
            segments = speech.accept(frame)
            at = (offset + len(frame)) / 16000
            if speech.noise_speech_active:
                onset = at if onset is None else onset
                last_guard = at
            item = rolling.push(frame, speech.active, 0)
            checks = ([] if item is None else [(item[0], True)]) + [(s, False) for s in segments]
            for values, partial in checks:
                started = time.perf_counter()
                text = speech.decode(values)
                elapsed = time.perf_counter() - started
                timings.append(elapsed)
                result = commands.parse(text)
                if result.intent:
                    transcripts.append({'at': round(at, 3), 'partial': partial, 'text': text, 'intent': result.intent})
                    if first is None and (expected is None or result.intent == expected):
                        first = at
                if not partial and endpoint is None:
                    endpoint = at
            if segments and hasattr(rolling, 'finish'):
                rolling.finish()
        row = dict(case=name, expected=expected, detected=first is not None,
                   fixture_sha256=hashlib.sha256(samples.tobytes()).hexdigest(),
                   first_command_audio_s=first, first_endpoint_audio_s=endpoint,
                   noise_guard_onset_s=onset, last_guard_s=last_guard,
                   onset_delay_s=None if onset is None or acoustic_onset is None else onset-acoustic_onset,
                   guard_tail_s=None if last_guard is None or acoustic_end is None else last_guard-acoustic_end,
                   decode_p50_s=float(np.median(timings)) if timings else None,
                   decode_p95_s=float(np.percentile(timings, 95)) if timings else None,
                   decode_calls=len(timings), transcripts=transcripts)
        results.append(row)
        print(name, 'detected', row['detected'], 'guard onset', onset, 'first command', first, flush=True)
    replays = {name: replay_runtime(module, args.models, samples, expected)
               for name, samples, expected, _, _ in cases
               if name in ('wrong/clean', 'continuous/clean')}
    output = dict(realtime_replays=replays, method='Synthetic eSpeak NG en-us 150 wpm; 16 kHz/10 ms frames; seed 73. Audio timestamps exclude inference and capture scheduling; decode timings are measured separately. Not natural classroom accuracy.',
                  python=platform.python_version(), platform=platform.platform(), sherpa_onnx=sherpa_onnx.__version__,
                  module_sha256=hashlib.sha256(args.module.read_bytes()).hexdigest(), results=results)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
