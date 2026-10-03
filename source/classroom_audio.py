"""Shared microphone, relative-noise classification, Silero VAD, and commands."""

from __future__ import annotations

import math
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from classroom_core import AudioHealth, BaseMode, NoiseState, NoiseProfile, Settings, normalize_phrase


@dataclass(frozen=True)
class NoiseReading:
    dbfs: float
    smoothed_dbfs: float
    relative_db: float | None
    state: NoiseState
    clipped: bool
    calibration_progress: float
    estimated_spl: float | None
    raw_rms: float = 0.0
    context_revision: int = 0
    health: AudioHealth = AudioHealth.OK
    calibration_result: dict | None = None
    speech_excluded: bool = False
    speech_guard_ready: bool = True
    captured_at: float = 0.0


class NoiseAnalyzer:
    """Fixed quiet baseline with time-based hysteresis; no automatic baseline drift."""

    def __init__(self, settings: Settings, baseline_dbfs: float | None = None,
                 spl_offset_db: float | None = None) -> None:
        self.settings = settings
        self.baseline_dbfs = baseline_dbfs
        self.spl_offset_db = spl_offset_db
        self.state = NoiseState.QUIET if baseline_dbfs is not None else NoiseState.UNKNOWN
        self._smooth: deque[tuple[float, float]] = deque()
        self._calibration_started: float | None = None
        self._calibration_values: list[float] = []
        self._calibration_speech: list[bool] = []
        self._calibration_last_feed: float | None = None
        self._calibration_valid_seconds = 0.0
        self._calibration_clipped = False
        self.calibration_result: dict | None = None
        self._calibration_seconds = float(settings.calibration.get("duration_sec", 12))
        self._candidate: NoiseState | None = None
        self._candidate_since = 0.0
        self._mode: BaseMode | None = None
        self._profile: NoiseProfile | None = None
        self._context_revision = 0
        self._grace_until = 0.0
        self._last_feed: float | None = None
        self._speech_started: float | None = None
        self._speech_last: float | None = None
        self._speech_resume_after = 0.0
        self._lock = threading.Lock()

    def start_calibration(self, now: float | None = None, seconds: float | None = None) -> None:
        with self._lock:
            self._calibration_started = time.monotonic() if now is None else now
            self._calibration_values.clear()
            self._calibration_speech.clear()
            self._calibration_last_feed = None
            self._calibration_valid_seconds = 0.0
            self._calibration_clipped = False
            self._calibration_seconds = float(seconds if seconds is not None else
                                               self.settings.calibration.get("duration_sec", 12))
            self.calibration_result = None
            self._candidate = None
            self.state = NoiseState.UNKNOWN

    @property
    def is_calibrating(self) -> bool:
        with self._lock:
            return self._calibration_started is not None

    def set_spl_reference(self, reference_db: float, measured_dbfs: float) -> None:
        if not 20 <= reference_db <= 140 or not -120 <= measured_dbfs <= 0:
            raise ValueError("Reference and microphone measurements are out of range")
        self.spl_offset_db = reference_db - measured_dbfs

    @staticmethod
    def _dbfs(samples) -> tuple[float, bool, float, AudioHealth]:
        import numpy as np

        values = np.asarray(samples, dtype=np.float32).reshape(-1)
        if values.size == 0:
            return -120.0, False, 0.0, AudioHealth.DISCONNECTED
        if not np.all(np.isfinite(values)):
            return -120.0, False, 0.0, AudioHealth.DISCONNECTED
        clipped = float(np.mean(np.abs(values) >= .98)) > .001
        values = values - float(np.mean(values))
        rms = float(np.sqrt(np.mean(values * values)))
        dbfs = max(-120.0, 20.0 * math.log10(max(rms, 1e-6)))
        health = AudioHealth.SILENT if dbfs <= -110 else AudioHealth.CLIPPING if clipped else AudioHealth.OK
        return dbfs, clipped, rms, health

    def feed(self, samples, now: float | None = None, classify: bool = True,
             mode: BaseMode = BaseMode.NOTICE, context_revision: int = 0,
             speech_active: bool | None = None) -> NoiseReading:
        now = time.monotonic() if now is None else now
        dbfs, clipped, raw_rms, health = self._dbfs(samples)
        profile = self.settings.noise_profile(mode)
        had_context = self._mode is not None
        if (mode is not self._mode or profile != self._profile or
                context_revision != self._context_revision):
            self._candidate = None
            self._speech_started = self._speech_last = None
            self._speech_resume_after = 0.0
            self.state = NoiseState.QUIET if self.baseline_dbfs is not None else NoiseState.UNKNOWN
            self._mode, self._profile = mode, profile
            self._context_revision = context_revision
            self._grace_until = (now + self.settings.mode_change_grace_ms / 1000.0
                                 if had_context else 0.0)
        if self._last_feed is not None and now - self._last_feed > .5:
            self._candidate = None
            self._speech_started = self._speech_last = None
            self._speech_resume_after = 0.0
            self.state = NoiseState.UNKNOWN
            self._smooth.clear()
        self._last_feed = now
        window_seconds = self.settings.noise_smoothing_ms / 1000.0
        speech_guard = (self.settings.speech_noise_guard and
                        mode in (BaseMode.NOTICE, BaseMode.DISCUSSION))
        guard_unavailable = speech_guard and speech_active is None
        speech_frame = speech_guard and speech_active is True
        # Listening and discussion have different purposes. Listening excludes
        # the entire detected voice; discussion must still measure conversation.
        # Neither policy can identify a teacher or separate overlapping sources.
        if self._speech_last is not None and now - self._speech_last >= 1.0:
            self._speech_started = None
        strong_input = (self.baseline_dbfs is not None and
                        dbfs - self.baseline_dbfs >= profile.loud_db)
        if speech_frame:
            if self._speech_started is None:
                self._speech_started = now
            self._speech_last = now
        listening_guard = speech_guard and mode is BaseMode.NOTICE
        speech_buffered = bool(speech_frame and (listening_guard or
                               (not strong_input and now - self._speech_started < 1.0)))
        if speech_buffered:
            self._speech_resume_after = (now + .35 if listening_guard else
                                         min(now + .35, self._speech_started + 1.35))
        elif speech_frame or (strong_input and not listening_guard) or guard_unavailable or not speech_guard:
            self._speech_resume_after = 0.0
        speech_recovery = (speech_guard and not speech_frame and (listening_guard or not strong_input) and
                           not guard_unavailable and now < self._speech_resume_after)
        excluded = speech_buffered or speech_recovery
        measurable = health in (AudioHealth.OK, AudioHealth.CLIPPING)
        if not measurable:
            self._smooth.clear()
        if excluded:
            self._candidate = None
            if listening_guard:
                # Do not let speech onset samples leak into the next noise hold.
                self._smooth.clear()
        else:
            self._smooth.append((now, dbfs if measurable else -120.0))
        while self._smooth and self._smooth[0][0] < now - window_seconds:
            self._smooth.popleft()
        # Average linear power before converting back to dB to avoid averaging log values.
        if self._smooth:
            mean_power = sum(10 ** (value / 10) for _, value in self._smooth) / len(self._smooth)
            smoothed = max(-120.0, 10.0 * math.log10(max(mean_power, 1e-12)))
        else:
            smoothed = dbfs
        relative = None if self.baseline_dbfs is None else smoothed - self.baseline_dbfs
        progress = 0.0
        completed_calibration = None
        with self._lock:
            if self._calibration_started is not None:
                elapsed = now - self._calibration_started
                if elapsed >= float(self.settings.calibration.get("discard_initial_sec", 2)):
                    self._calibration_values.append(dbfs if health is AudioHealth.OK else float("nan"))
                    self._calibration_speech.append(bool(speech_active) if speech_active is not None else True)
                    self._calibration_clipped |= health is AudioHealth.CLIPPING
                    start_valid = self._calibration_started + float(
                        self.settings.calibration.get("discard_initial_sec", 2))
                    if self._calibration_last_feed is not None and now - self._calibration_last_feed <= .5:
                        self._calibration_valid_seconds += max(0.0, min(.25, now - max(
                            self._calibration_last_feed, start_valid))) if health is AudioHealth.OK else 0.0
                    self._calibration_last_feed = now
                progress = min(1.0, max(0.0, (now - self._calibration_started) / self._calibration_seconds))
                if progress >= 1.0:
                    import numpy as np
                    values = np.asarray(self._calibration_values, dtype=float)
                    finite = values[np.isfinite(values)]
                    speech_ratio = (sum(self._calibration_speech) / len(self._calibration_speech)
                                    if self._calibration_speech else 1.0)
                    spread = float(np.percentile(finite, 90) - np.percentile(finite, 10)) if finite.size else None
                    required_seconds = max(0.0, self._calibration_seconds - float(
                        self.settings.calibration.get("discard_initial_sec", 2))) * .8
                    valid = (not self._calibration_clipped and
                             self._calibration_valid_seconds >= required_seconds and
                             len(finite) >= max(1, math.ceil(len(values) * .8)) and
                             spread is not None and spread <= float(self.settings.calibration.get("max_spread_db", 8)) and
                             speech_ratio <= float(self.settings.calibration.get("max_speech_ratio", .1)) and
                             float(np.median(finite)) > -110)
                    baseline = float(np.median(finite)) if valid else None
                    self.calibration_result = {
                        **self.settings.calibration, "device_id": getattr(self, "device_id", ""),
                        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "baseline_dbfs": baseline, "spread_db": spread,
                        "speech_ratio": speech_ratio, "quality": "PASS" if valid else "REJECTED",
                        "sample_rate": getattr(self, "sample_rate", None),
                    }
                    if valid:
                        self.baseline_dbfs = baseline
                        self.state = NoiseState.UNKNOWN
                    self._calibration_started = None
                    self._calibration_values.clear()
                    self._calibration_speech.clear()
                    self._calibration_last_feed = None
                    self._candidate = None
                    completed_calibration = self.calibration_result
                    relative = None if self.baseline_dbfs is None else smoothed - self.baseline_dbfs
        if self.is_calibrating:
            self._candidate = None
            self.state = NoiseState.UNKNOWN
            health = AudioHealth.UNCALIBRATED if health is AudioHealth.OK else health
        # Clipping invalidates quiet calibration, but still represents a real
        # high input level. Keep measuring it rather than masking a loud shout.
        invalid = health not in (AudioHealth.OK, AudioHealth.CLIPPING) or self.is_calibrating
        if invalid:
            self._candidate = None
            self.state = NoiseState.UNKNOWN
            relative = None
        elif excluded:
            self._candidate = None
        elif now < self._grace_until:
            self._candidate = None
            self.state = NoiseState.UNKNOWN
        elif classify and self.baseline_dbfs is not None and relative is not None:
            self._advance_state(relative, now, profile)
        elif not classify:
            self._candidate = None
        estimated = None if self.spl_offset_db is None else smoothed + self.spl_offset_db
        return NoiseReading(dbfs, smoothed, relative, self.state, clipped, progress, estimated,
                            raw_rms, context_revision,
                            health if health is not AudioHealth.OK or self.baseline_dbfs is not None
                            else AudioHealth.UNCALIBRATED,
                            completed_calibration,
                            excluded,
                            not guard_unavailable, now)

    def _advance_state(self, relative_db: float, now: float, profile: NoiseProfile) -> None:
        settings = self.settings
        target, duration = None, 0.0
        if self.state in (NoiseState.UNKNOWN, NoiseState.QUIET):
            if relative_db >= profile.loud_db:
                target, duration = NoiseState.LOUD, settings.noise_loud_enter_seconds
            elif relative_db >= profile.rising_db:
                target, duration = NoiseState.RISING, settings.noise_rising_enter_seconds
            elif self.state is NoiseState.UNKNOWN:
                self.state = NoiseState.QUIET
        elif self.state is NoiseState.RISING:
            if relative_db >= profile.loud_db:
                target, duration = NoiseState.LOUD, settings.noise_loud_enter_seconds
            elif relative_db < profile.rising_exit_db:
                target, duration = NoiseState.QUIET, settings.noise_exit_seconds
        elif self.state is NoiseState.LOUD and relative_db < profile.loud_exit_db:
            target, duration = NoiseState.RISING, settings.noise_exit_seconds

        if target is None or target is self.state:
            self._candidate = None
            return
        if target is not self._candidate:
            self._candidate, self._candidate_since = target, now
        elif now - self._candidate_since >= duration:
            self.state = target
            self._candidate = None


@dataclass(frozen=True)
class CommandResult:
    text: str
    intent: str | None
    reason: str
    corrected_text: str = ""


class CommandParser:
    """Trigger on complete aliases while preserving English word boundaries."""

    def __init__(self, settings: Settings, cooldown_seconds: float = 2.0) -> None:
        self.settings = settings
        self.cooldown_seconds = cooldown_seconds
        self._last: dict[str, float] = {}
        self._aliases = {name: {normalize_phrase(alias) for alias in aliases}
                         for name, aliases in settings.command_aliases.items()}

    def parse(self, text: str, now: float | None = None, *, commit: bool = True) -> CommandResult:
        now = time.monotonic() if now is None else now
        phrase = normalize_phrase(text)
        corrections = {normalize_phrase(a): normalize_phrase(b)
                       for a, b in self.settings.command_corrections.items()}
        corrected = phrase
        for source in sorted(corrections, key=len, reverse=True):
            if not source:
                continue
            expression = (re.escape(source) if any("\u3400" <= char <= "\u9fff" for char in source)
                          else r"(?<!\w)" + re.escape(source) + r"(?!\w)")
            corrected = re.sub(expression, lambda _match, value=corrections[source]: value, corrected)
        if not phrase:
            return CommandResult(text, None, "沒有辨識到文字", corrected)
        matches = []
        for name, aliases in self._aliases.items():
            for alias in aliases:
                if any("\u3400" <= char <= "\u9fff" for char in alias):
                    matches.extend((m.start(), m.end(), len(alias), name)
                                   for m in re.finditer(re.escape(alias), corrected))
                else:
                    pattern = re.compile(r"(?<!\w)" + re.escape(alias) + r"(?!\w)")
                    matches.extend((m.start(), m.end(), len(alias), name)
                                   for m in pattern.finditer(corrected))
        if not matches:
            return CommandResult(text, None, "no exact command match", corrected)
        matches = [match for match in matches if not any(
            other[0] <= match[0] and other[1] >= match[1] and other[2] > match[2]
            for other in matches)]
        # The last complete trigger wins; at the same start, prefer its longest alias.
        _start, _end, _length, intent = max(matches, key=lambda item: (item[0], item[2], item[1]))
        if commit:
            if now - self._last.get(intent, -math.inf) < self.cooldown_seconds:
                return CommandResult(text, None, "command cooldown", corrected)
            self._last[intent] = now
        return CommandResult(text, intent, "accepted", corrected)


class SenseVoiceSpeech:
    """Local SenseVoiceSmall recognizer and the bundled Silero streaming VAD."""

    def __init__(self, model_dir: Path, threads: int = 2):
        model, tokens, vad_model = (model_dir / name for name in
                                    ("model.int8.onnx", "tokens.txt", "silero_vad.onnx"))
        missing = [str(path) for path in (model, tokens, vad_model) if not path.is_file()]
        if missing:
            raise FileNotFoundError("離線語音資源缺少：" + ", ".join(missing))
        import sherpa_onnx

        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(vad_model)
        config.silero_vad.threshold = 0.25
        config.silero_vad.min_speech_duration = 0.20
        config.silero_vad.min_silence_duration = 0.32
        config.silero_vad.max_speech_duration = 4.0
        config.silero_vad.window_size = 512
        config.sample_rate = 16000
        config.num_threads = 1
        if not config.validate():
            raise RuntimeError("Silero VAD 設定無效")
        self.vad = sherpa_onnx.VoiceActivityDetector(config, 8.0)
        # Noise exclusion must not inherit the sensitive command VAD's gain.
        # A separate Silero detector observes the original microphone waveform.
        config.silero_vad.threshold = 0.5
        # Meter guarding needs fast onset, not the command endpoint delay.
        # NoiseAnalyzer adds its own 350 ms recovery hold after this detector.
        config.silero_vad.min_speech_duration = 0.096
        config.silero_vad.min_silence_duration = 0.16
        config.silero_vad.max_speech_duration = 30.0
        self.noise_vad = sherpa_onnx.VoiceActivityDetector(config, 32.0)
        # Keep quiet speech audible to Silero while limiting the gain of already
        # loud blocks. The release is smoothed across capture frames to avoid
        # frame-by-frame level pumping; metering continues to use untouched audio.
        self._vad_gain = 10.0
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model), tokens=str(tokens), num_threads=threads,
            decoding_method="greedy_search", debug=False, provider="cpu", use_itn=True,
        )

    @property
    def speech_active(self) -> bool:
        return self.vad.is_speech_detected()

    @property
    def active(self) -> bool:
        return self.speech_active

    @property
    def noise_speech_active(self) -> bool:
        return self.noise_vad.is_speech_detected()

    def reset(self) -> None:
        self.vad.reset()
        self.noise_vad.reset()
        self._vad_gain = 10.0

    def accept(self, samples):
        import numpy as np

        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        self.noise_vad.accept_waveform(samples)
        # Only the boolean activity is needed; discard completed raw segments.
        while not self.noise_vad.empty():
            self.noise_vad.pop()
        if samples.size:
            peak = float(np.max(np.abs(samples), initial=0.0))
            safe_gain = min(10.0, 0.95 / peak) if peak > 0.0 else 10.0
            if safe_gain < self._vad_gain:
                self._vad_gain = safe_gain
            else:
                # Smooth only recovery toward the quiet-speech gain ceiling.
                frame_seconds = samples.size / 16000.0
                alpha = 1.0 - math.exp(-frame_seconds / 0.20)
                self._vad_gain += (safe_gain - self._vad_gain) * alpha
            samples = samples * self._vad_gain
        self.vad.accept_waveform(samples)
        completed = []
        while not self.vad.empty():
            completed.append(np.asarray(self.vad.front.samples, dtype=np.float32).copy())
            self.vad.pop()
        return completed

    def decode(self, samples) -> str:
        import numpy as np

        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        peak = float(np.max(np.abs(samples), initial=0))
        rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
        if samples.size < 5120 or peak < 1e-5 or rms < 1e-5:
            return ""
        if peak < 0.35:
            samples = samples * min(10.0, 0.35 / peak)
        stream = self.recognizer.create_stream()
        stream.accept_waveform(16000, samples)
        self.recognizer.decode_stream(stream)
        return str(stream.result.text).strip()


@dataclass(frozen=True)
class CaptureFrame:
    samples: object
    captured_at: float
    sequence: int


@dataclass(frozen=True)
class SpeechJob:
    samples: object
    revision: int
    epoch: int
    queued_at: float
    utterance: int
    partial: bool = False


class SpeechJobQueue(queue.Queue):
    """Keep one final and one live probe; live traffic cannot erase an endpoint."""

    def __init__(self):
        super().__init__(maxsize=2)

    def put_latest(self, job):
        with self.not_empty:
            pending = list(self.queue)
            if isinstance(job, SpeechJob):
                # A final replaces its own speculative window. A newer final
                # supersedes the older final, so overload remains bounded.
                keep = [item for item in pending if isinstance(item, SpeechJob)
                        and not item.partial and job.partial]
                if keep and (keep[0].epoch, keep[0].utterance) == (job.epoch, job.utterance):
                    return True
                self.queue.clear()
                self.queue.extend(keep[-1:])
            else:
                self.queue.clear()
            self.queue.append(job)
            self.not_empty.notify()
            return bool(pending)


class RollingCommandBuffer:
    """Bounded raw-audio context for command checks during continuous speech."""

    sample_rate = 16000
    window_samples = 19200  # 1.2 seconds; do not wait for the whole window.
    hop_samples = 4000      # 250 ms between checks.
    minimum_samples = 5120  # Same 320 ms minimum as the ordinary decoder.
    preroll_samples = 7680  # 480 ms preserves consonants before VAD onset.

    def __init__(self):
        self._chunks = deque()
        self._size = self._total = 0
        self._last_probe = -self.hop_samples
        self._last_active = None
        self.utterance = 0
        self.revision = 0

    def finish(self):
        """Close the endpoint without lending its command to the next window."""
        self._chunks.clear()
        self._size = 0
        self._last_active = None

    def push(self, samples, active: bool, revision: int):
        import numpy as np

        values = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not values.size:
            return None
        self._chunks.append(values.copy())
        self._size += values.size
        self._total += values.size
        # During a long pause keep onset context, not the previous command.
        limit = (self.window_samples if active or
                 (self._last_active is not None and
                  self._total - self._last_active <= int(.45 * self.sample_rate))
                 else self.preroll_samples if self.utterance else self.window_samples)
        while self._size > limit:
            excess = self._size - limit
            head = self._chunks.popleft()
            removed = min(excess, head.size)
            self._size -= removed
            if removed < head.size:
                self._chunks.appendleft(head[removed:])
        if not active:
            return None
        if (self._last_active is None or
                self._total - self._last_active > int(.45 * self.sample_rate)):
            self.utterance += 1
            self.revision = revision
            self._last_probe = -self.hop_samples
        self._last_active = self._total
        if self._size < self.minimum_samples or self._total - self._last_probe < self.hop_samples:
            return None
        self._last_probe = self._total
        return np.concatenate(tuple(self._chunks)), self.utterance, self.revision


class AudioRuntime:
    """One microphone. Noise capture never waits for SenseVoice to load or decode."""

    def __init__(self, device_id: str, model_dir: Path, settings: Settings,
                 baseline_dbfs: float | None,
                 noise_enabled: Callable[[], bool], manual_revision: Callable[[], int],
                 on_status: Callable[[str, bool, str], None],
                 on_noise: Callable[[NoiseReading], None],
                 on_voice: Callable[[bool], None],
                 on_command: Callable[[CommandResult, int], None],
                 on_calibrated: Callable[[float], None] | None = None,
                 noise_context: Callable[[], tuple[BaseMode, int]] | None = None):
        self.device_id, self.model_dir, self.settings = device_id, model_dir, settings
        self.noise_enabled, self.manual_revision = noise_enabled, manual_revision
        self.on_status, self.on_noise = on_status, on_noise
        self.on_voice, self.on_command = on_voice, on_command
        self.on_calibrated = on_calibrated or (lambda _baseline: None)
        self.noise_context = noise_context or (lambda: (BaseMode.NOTICE, 0))
        self.sample_rate = 48000
        self.noise = NoiseAnalyzer(settings, baseline_dbfs)
        self.noise.device_id = device_id
        self.noise.sample_rate = self.sample_rate
        self.latest: NoiseReading | None = None
        self._queue: queue.Queue = queue.Queue(maxsize=12)
        self._jobs: queue.Queue = SpeechJobQueue()
        self._commands: queue.Queue = queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self._listen = threading.Event()
        self._epoch = 0
        self._failed_devices: set[int] = set()
        self._thread = self._decoder = None
        self._stream = self._vad = None
        self._capture_sequence = 0
        self.capture_dropped = 0
        self.capture_lag_seconds = 0.0
        self.capture_overloaded = False
        self._capture_recovery_until = 0.0
        self.set_listening(settings.voice_enabled)

    @staticmethod
    def microphones() -> list[tuple[str, str]]:
        import sounddevice as sd

        devices = sd.query_devices()
        apis = sd.query_hostapis()
        preferred = next((i for i, a in enumerate(apis) if a['name'] in ('Windows WASAPI', 'Core Audio')), None)
        return [(f"{i}|{d['name']}", str(d['name'])) for i, d in enumerate(devices)
                if d['max_input_channels'] > 0 and (preferred is None or d['hostapi'] == preferred)]

    def _device(self, sd):
        devices = sd.query_devices()
        preferred = self.microphones()
        index, _, name = self.device_id.partition('|') if self.device_id else ('', '', '')
        if not self.device_id:
            default = int(sd.default.device[0])
            if 0 <= default < len(devices):
                name = str(devices[default]['name'])
            else:
                default = next((int(token.split('|', 1)[0]) for token, _ in preferred), None)
                name = str(devices[default]['name']) if default is not None else ''
        if name:
            matches = [i for i, item in enumerate(devices)
                       if item['max_input_channels'] > 0 and
                       (item['name'] == name or (len(item['name']) >= 20 and
                        (item['name'].startswith(name) or name.startswith(item['name']))))]
            if matches:
                preferred_ids = [int(token.split('|', 1)[0]) for token, label in preferred if label == name]
                order = [i for i in preferred_ids + [i for i in matches if i not in preferred_ids]
                         if i in matches]
                available = [i for i in order if i not in self._failed_devices]
                if not available:
                    self._failed_devices.difference_update(matches)
                    available = order
                if available:
                    return available[0]
                raise RuntimeError('選定的麥克風無法開啟，請重新選擇輸入裝置')
        if not self.device_id:
            return default if default is not None and devices[default]['max_input_channels'] > 0 else None
        index = int(index)
        if index < 0 or index >= len(devices) or devices[index]['max_input_channels'] <= 0 or (
                name and devices[index]['name'] != name):
            raise RuntimeError('選定的麥克風已移除，請重新選擇輸入裝置')
        return index

    def start_calibration(self, seconds: float | None = None):
        self._commands.put_nowait(('calibrate', seconds))

    def set_listening(self, enabled: bool):
        self._epoch += 1
        if enabled:
            self._listen.set()
        else:
            self._listen.clear()

    def update_settings(self, settings: Settings):
        self.settings = settings
        self.noise.settings = settings
        self._epoch += 1

    def _invalidate_calibration_for_rate(self, sample_rate: int) -> bool:
        calibration = self.settings.calibration
        stored_rate = calibration.get("sample_rate")
        if (calibration.get("quality") != "PASS" or stored_rate is None or
                stored_rate == sample_rate):
            return False
        self.noise.baseline_dbfs = None
        self.noise.state = NoiseState.UNKNOWN
        self.noise._candidate = None
        self.on_status("calibration", False, "麥克風格式已變更，請重新校準")
        return True

    @property
    def is_running(self):
        return any(thread and thread.is_alive() for thread in (self._thread, self._decoder))

    def start(self):
        if self.is_running:
            return
        self._stop.clear()
        self._decoder = threading.Thread(target=self._decode_loop, name='SenseVoice-CPU', daemon=True)
        self._thread = threading.Thread(target=self._run, name='Classroom-Microphone', daemon=True)
        self._decoder.start()
        self._thread.start()

    def stop(self, timeout: float = 0):
        self._stop.set()
        self._listen.clear()
        self._epoch += 1
        deadline = time.monotonic() + max(0.0, timeout)
        for thread in (self._thread, self._decoder):
            if thread and thread.is_alive():
                thread.join(max(0.0, deadline - time.monotonic()))

    @staticmethod
    def _enqueue_latest_job(jobs: queue.Queue, job) -> bool:
        """Keep final/live work bounded; legacy queues retain latest-only behavior."""
        if isinstance(jobs, SpeechJobQueue):
            return jobs.put_latest(job)
        replaced = False
        while True:
            try:
                jobs.put_nowait(job)
                return replaced
            except queue.Full:
                try:
                    jobs.get_nowait()
                    replaced = True
                except queue.Empty:
                    # The decoder took the old job between Full and get_nowait.
                    continue

    def _job_is_current(self, revision: int, epoch: int, queued_at: float,
                         now: float | None = None) -> bool:
        current_time = time.monotonic() if now is None else now
        age = current_time - queued_at
        return (not self._stop.is_set() and not self.capture_overloaded and
                0.0 <= age <= 3.0 and epoch == self._epoch and
                revision == self.manual_revision() and self._listen.is_set() and
                not self.noise.is_calibrating)

    def _decode_loop(self):
        recognizer, retry_after = None, 0.0
        parser = CommandParser(self.settings)
        accepted_windows: dict[tuple[int, int], set[str]] = {}
        last_partial_text = None
        while not self._stop.is_set():
            guard_needed = self.settings.speech_noise_guard and self.noise_enabled()
            if not self._listen.is_set() and not self.noise.is_calibrating and not guard_needed:
                self._stop.wait(.1)
                continue
            if recognizer is None:
                if time.monotonic() < retry_after:
                    self._stop.wait(.2)
                    continue
                self.on_status('speech', False, '正在載入離線 SenseVoice…')
                try:
                    recognizer = SenseVoiceSpeech(self.model_dir)
                    if self._stop.is_set():
                        return
                    self._vad = recognizer
                    self.on_status('speech', True, 'SenseVoice 已就緒，等待您說出指令')
                except Exception as exc:
                    recognizer = None
                    if self._stop.is_set():
                        return
                    retry_after = time.monotonic() + 15
                    self.on_status('speech', False, f'SenseVoice 未能啟動：{exc}')
                    continue
            try:
                job = self._jobs.get(timeout=.1)
            except queue.Empty:
                continue
            streaming = isinstance(job, SpeechJob)
            if streaming:
                samples, revision, epoch, queued_at = (job.samples, job.revision, job.epoch, job.queued_at)
            elif len(job) == 3:  # Compatibility for callers and existing queued test jobs.
                samples, revision, epoch = job
                queued_at = time.monotonic()
            else:
                samples, revision, epoch, queued_at = job
            def current_job():
                return (self._job_is_current(revision, epoch, queued_at) and
                        (not streaming or not job.partial or time.monotonic() - queued_at <= .75))
            if not current_job():
                continue
            if not streaming:
                self.on_status('speech', True, '正在辨識…')
            try:
                recognizer.settings = self.settings
                if parser.settings != self.settings:
                    parser = CommandParser(self.settings)
                    accepted_windows.clear()
                    last_partial_text = None
                text = recognizer.decode(samples)
                if not current_job():
                    continue
                if streaming:
                    key = (epoch, job.utterance)
                    candidate = parser.parse(text, commit=False)
                    # The endpoint decode is a fallback, not a replay of a
                    # command already acted upon in an overlapping live window.
                    if not job.partial and candidate.intent in accepted_windows.get(key, set()):
                        continue
                    if job.partial and not text:
                        last_partial_text = None
                        continue
                    if (job.partial and last_partial_text is not None and
                            last_partial_text[:2] == (key, text) and
                            queued_at - last_partial_text[2] < 1.0):
                        continue
                result = parser.parse(text)
                if streaming:
                    if result.intent:
                        accepted_windows.setdefault(key, set()).add(result.intent)
                    if job.partial:
                        last_partial_text = (key, text, queued_at)
                    # Keep bounded bookkeeping just like the bounded audio queue.
                    while len(accepted_windows) > 8:
                        del accepted_windows[next(iter(accepted_windows))]
            except Exception as exc:
                result = CommandResult('', None, f'辨識失敗：{exc}')
            if (not self._stop.is_set() and
                    current_job()):
                self.on_command(result, revision)
                if not streaming:
                    self.on_status('speech', True, '等待下一個指令')

    def _run(self):
        import sounddevice as sd
        import soxr

        last_status = None
        retry = 0
        def status(ok, text):
            nonlocal last_status
            if (ok, text) != last_status:
                last_status = (ok, text)
                self.on_status('microphone', ok, text)

        def callback(indata, _frames, _time, flags):
            self._capture_sequence += 1
            # A timestamp survives queueing: old audio must never become a new
            # command merely because the consumer has only just dequeued it.
            if flags.input_overflow:
                self._capture_sequence += 1  # force a discontinuity reset
                try:
                    self._commands.put_nowait(('overflow', 0))
                except queue.Full:
                    pass
            frame = CaptureFrame(indata[:, 0].copy(), time.monotonic(), self._capture_sequence)
            try:
                self._queue.put_nowait(frame)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self.capture_dropped += 1
                    self._queue.put_nowait(frame)
                except (queue.Empty, queue.Full):
                    pass

        while not self._stop.is_set():
            device = None
            stream_opened = False
            try:
                device = self._device(sd)
                channels = self._input_channels(sd, device)
                supported = [rate for rate in (48000, 44100, 32000, 16000)
                             if self._supports(sd, device, rate, channels)]
                if not supported:
                    raise RuntimeError('此麥克風無法使用支援的輸入格式，請選擇其他麥克風')
                self.sample_rate = supported[0]
                self.noise.sample_rate = self.sample_rate
                with sd.InputStream(device=device, samplerate=self.sample_rate, channels=channels,
                                    blocksize=self.sample_rate // 100, dtype='float32', callback=callback) as stream:
                    stream_opened = True
                    self._stream = stream
                    if self._vad is not None:
                        self._vad.reset()
                    self._invalidate_calibration_for_rate(self.sample_rate)
                    status(True, '麥克風正在收音')
                    resampler = soxr.ResampleStream(self.sample_rate, 16000, 1, dtype='float32')
                    last_block = last_meter = time.monotonic()
                    last_stale = last_block
                    silence_started = None
                    previous_active = False
                    vad_was_running = False
                    calibration_vad_active = False
                    epoch = self._epoch
                    revision = self.manual_revision()
                    rolling = RollingCommandBuffer()
                    last_sequence = None
                    retry = 0
                    while not self._stop.is_set():
                        try:
                            frame = self._queue.get(timeout=.15)
                        except queue.Empty:
                            idle = time.monotonic() - last_block
                            if idle > 2 or not stream.active:
                                raise RuntimeError('收音中斷；請確認麥克風連接與系統權限')
                            if idle > .5 and time.monotonic() - last_stale >= .5:
                                self.noise._candidate = None
                                self.noise.state = NoiseState.UNKNOWN
                                self.noise._smooth.clear()
                                mode, context_revision = self.noise_context()
                                self.latest = NoiseReading(-120, -120, None, NoiseState.UNKNOWN, False,
                                    0, None, 0, context_revision, AudioHealth.DISCONNECTED)
                                self.on_noise(self.latest)
                                last_stale = time.monotonic()
                            continue
                        now = last_block = time.monotonic()
                        last_stale = now
                        raw = frame.samples
                        self.capture_lag_seconds = max(0.0, now - frame.captured_at)
                        discontinuous = (last_sequence is not None and
                                         frame.sequence != last_sequence + 1)
                        last_sequence = frame.sequence
                        if discontinuous or self.capture_lag_seconds > .15:
                            self._capture_recovery_until = now + .5
                            self.capture_overloaded = True
                            status(False, '收音處理落後；請降低系統負載或重新選擇麥克風')
                            # Dropped samples cannot be concatenated into a word,
                            # a speech hold, or a calibration interval.
                            self._epoch += 1
                            rolling = RollingCommandBuffer()
                            resampler = soxr.ResampleStream(self.sample_rate, 16000, 1,
                                                           dtype='float32')
                            if self._vad is not None:
                                self._vad.reset()
                            epoch = self._epoch
                            if previous_active:
                                self.on_voice(False)
                            previous_active = False
                            self.noise._last_feed = None
                            self.noise._smooth.clear()
                            self.noise._candidate = None
                            self.noise.state = NoiseState.UNKNOWN
                            self.noise._speech_started = self.noise._speech_last = None
                            self.noise._speech_resume_after = 0.0
                            self.noise._calibration_last_feed = None
                            if self.capture_lag_seconds > .15:
                                self.capture_dropped += 1
                                continue
                        self.capture_overloaded = now < self._capture_recovery_until
                        try:
                            action, value = self._commands.get_nowait()
                            if action == 'calibrate':
                                self.noise.start_calibration(seconds=value)
                            elif action == 'overflow':
                                status(True, '收音曾短暫落後；正在恢復')
                        except queue.Empty:
                            pass
                        was_calibrating = self.noise.is_calibrating
                        mode, context_revision = self.noise_context()
                        should_classify = self.noise_enabled()
                        vad = self._vad
                        needs_vad = (self._listen.is_set() or was_calibrating or
                                     (self.settings.speech_noise_guard and should_classify and
                                      mode in (BaseMode.NOTICE, BaseMode.DISCUSSION)))
                        segments = []
                        if vad is not None and needs_vad:
                            vad_was_running = True
                            if epoch != self._epoch:
                                vad.reset()
                                if previous_active and self._listen.is_set():
                                    self.on_voice(False)
                                previous_active = False
                                epoch = self._epoch
                                rolling = RollingCommandBuffer()
                            if not was_calibrating and calibration_vad_active:
                                # A new rolling identity must not collide with
                                # the decoder's pre-calibration deduplication key.
                                self._epoch += 1
                                epoch = self._epoch
                                vad.reset()
                                calibration_vad_active = False
                                previous_active = False
                                rolling = RollingCommandBuffer()
                            if self._listen.is_set() and not vad.active:
                                revision = self.manual_revision()
                            resampled = resampler.resample_chunk(raw, last=False)
                            segments = vad.accept(resampled)
                            speech_active = None if self.capture_overloaded else vad.noise_speech_active
                            partial = (rolling.push(resampled, vad.active, self.manual_revision())
                                       if self._listen.is_set() and not was_calibrating else None)
                            if was_calibrating:
                                calibration_vad_active = True
                                previous_active = vad.active
                            elif not previous_active and vad.active and self._listen.is_set():
                                self.on_voice(True)
                                previous_active = True
                            elif previous_active and not vad.active:
                                if self._listen.is_set():
                                    self.on_voice(False)
                                previous_active = False
                        else:
                            speech_active = None
                            partial = None
                            if vad is not None and vad_was_running:
                                vad.reset()
                                resampler = soxr.ResampleStream(self.sample_rate, 16000, 1,
                                                               dtype='float32')
                                vad_was_running = False
                                if self._listen.is_set():
                                    self.on_voice(False)
                                previous_active = False
                                epoch = self._epoch
                                rolling = RollingCommandBuffer()
                        try:
                            self.latest = self.noise.feed(raw, now=frame.captured_at, classify=should_classify,
                                                          mode=mode, context_revision=context_revision,
                                                          speech_active=speech_active)
                        except ValueError as exc:
                            self.on_status('calibration', False, str(exc))
                            continue
                        if was_calibrating and not self.noise.is_calibrating:
                            if self.noise.calibration_result and self.noise.calibration_result.get("quality") == "PASS":
                                self.on_calibrated(self.noise.baseline_dbfs)
                            else:
                                self.on_status('calibration', False, '校準未通過：請在安靜環境且麥克風有正常輸入時重試')
                        if self.latest.dbfs <= -110:
                            silence_started = now if silence_started is None else silence_started
                            if now - silence_started > 3:
                                status(False, '沒有收到聲音，請取消麥克風靜音或改選輸入')
                        else:
                            silence_started = None
                            if not self.capture_overloaded:
                                status(True, '麥克風正在收音' if not self.latest.clipped else '聲音削波，請降低系統麥克風音量')
                        if now - last_meter >= .1:
                            self.on_noise(self.latest)
                            last_meter = now
                        if vad is None or was_calibrating or not needs_vad:
                            continue
                        if not self._listen.is_set():
                            continue
                        if partial is not None:
                            samples, utterance, captured_revision = partial
                            self._enqueue_latest_job(self._jobs, SpeechJob(
                                samples, captured_revision, epoch, frame.captured_at, utterance, True))
                        for samples in segments:
                            self._enqueue_latest_job(
                                self._jobs,
                                SpeechJob(samples, rolling.revision if rolling.utterance else revision, epoch,
                                          frame.captured_at, rolling.utterance))
                        if segments:
                            rolling.finish()
            except Exception as exc:
                if not self._stop.is_set():
                    self._epoch += 1  # Results captured before a disconnect are stale.
                    if not stream_opened and device is not None:
                        self._failed_devices.add(device)
                    status(False, f'收音未成功，將自動重試：{exc}')
                    retry += 1
                    self._stop.wait(min(10, 2 * retry))
            finally:
                self._stream = None
                while True:
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        break

    @staticmethod
    def _supports(sd, device, sample_rate, channels=1):
        try:
            sd.check_input_settings(device=device, channels=channels, samplerate=sample_rate, dtype='float32')
            return True
        except Exception:
            return False

    @staticmethod
    def _input_channels(sd, device):
        maximum = int(sd.query_devices(device)['max_input_channels'])
        for channels in (1, 2, 4, 3):
            if channels <= maximum and any(AudioRuntime._supports(sd, device, rate, channels)
                                            for rate in (48000, 44100, 32000, 16000)):
                return channels
        return maximum
