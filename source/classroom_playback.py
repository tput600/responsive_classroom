"""Optional mode cues and looping rest music, kept independent of microphone capture."""

from __future__ import annotations

import math
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtMultimedia import QAudioBufferOutput, QAudioOutput, QAudioFormat, QMediaPlayer

from classroom_core import (BaseMode, ClassroomState, DEFAULT_REST_AUDIO, NoiseState,
                            Overlay, RestStage, Settings)

AUDIO_MODES = {"standby", "question", "correct", "wrong", "notice", "rest", "rest_end", "discussion"}
EXTENSIONS = {".wav", ".mp3"}


class AudioPlayback(QObject):
    """Play user-selected cues without touching input capture or system volume."""

    def __init__(self, data_dir: Path, settings: Settings,
                 on_status: Callable[[str], None] | None = None,
                 parent: QObject | None = None, enabled: bool = True,
                 bundled_rest_source: Path | None = None) -> None:
        super().__init__(parent)
        self.data_dir = Path(data_dir).resolve()
        self.settings = settings
        self.on_status = on_status
        self.enabled = enabled
        self.bundled_rest_source = Path(bundled_rest_source) if bundled_rest_source is not None else None
        self._player: QMediaPlayer | None = None
        self._output: QAudioOutput | None = None
        self._buffer_output: QAudioBufferOutput | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._fade_tick)
        self._fade_started = 0.0
        self._fade_from = 0.0
        self._fade_to = 0.0
        self._fade_stop = False
        self._playing_key: tuple[str, bool] | None = None
        self._playing_mode: str | None = None
        self._music_level = 0.0
        self._last_audio_buffer = 0.0
        self._looping = False
        self._reference_lock = threading.Lock()
        self._reference_mode: str | None = None
        self._reference_power = 0.0
        self._reference_at = 0.0
        self._closed = False
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(100)
        self._level_timer.timeout.connect(self._expire_music_level)
        self._level_timer.start()
        if enabled:
            self._player = QMediaPlayer(self)
            self._output = QAudioOutput(self)
            self._player.setAudioOutput(self._output)
            if hasattr(self._player, "setAudioBufferOutput"):
                self._buffer_output = QAudioBufferOutput(self)
                self._player.setAudioBufferOutput(self._buffer_output)
                self._buffer_output.audioBufferReceived.connect(self._on_audio_buffer)
            self._player.errorOccurred.connect(self._on_error)
            self._player.mediaStatusChanged.connect(self._on_media_status)
            playback_state_changed = getattr(self._player, "playbackStateChanged", None)
            if playback_state_changed is not None:
                playback_state_changed.connect(self._on_playback_state)
            self._set_volume()

    @property
    def suppress_detection(self) -> bool:
        state = getattr(self, "_last_state", None)
        return state is not None and state.base_mode is BaseMode.REST

    @property
    def is_playing(self) -> bool:
        return (self._player is not None and
                self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)

    @property
    def has_music_signal(self) -> bool:
        return (self._playing_mode is not None and self.is_playing and
                self._last_audio_buffer > 0 and
                time.monotonic() - self._last_audio_buffer <= 0.6)

    @property
    def music_level(self) -> float:
        return self._music_level if self.has_music_signal else 0.0

    @property
    def noise_reference_power(self) -> float:
        with self._reference_lock:
            if (self._reference_mode in ("notice", "discussion") and self._reference_at and
                    time.monotonic() - self._reference_at <= 0.6):
                return self._reference_power
            return 0.0

    @property
    def playing_mode(self) -> str | None:
        return self._playing_mode if self.is_playing else None

    def apply_settings(self, settings: Settings) -> None:
        previous = self.settings
        self.settings = settings
        state = getattr(self, "_last_state", None)
        mode = self._state_mode(state) if state is not None else None
        scenario = "rest" if mode == "rest_end" else mode
        retarget = (state is not None and mode is not None and
                    (settings.audio_files.get(mode) != previous.audio_files.get(mode) or
                     settings.audio_enabled_modes.get(scenario) != previous.audio_enabled_modes.get(scenario)))
        rest_priority_changed = (state is not None and state.base_mode is BaseMode.REST and
                                 previous.voice_enabled != settings.voice_enabled)
        if rest_priority_changed and settings.voice_enabled:
            self.stop()
        elif retarget or rest_priority_changed:
            self._playing_key = None
            self.handle_state(state)
        elif self._output and self._player and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and not self._fade_stop:
            self._fade(self._output.volume(), self._target_volume(mode, state))
        else:
            self._set_volume()

    def handle_state(self, state: ClassroomState) -> None:
        previous = getattr(self, "_last_state", None)
        self._last_state = state
        if (previous is not None and state != previous and
                (state.base_mode, state.overlay, state.rest_stage) ==
                (previous.base_mode, previous.overlay, previous.rest_stage)):
            if self._playing_mode in ("notice", "discussion") and self.is_playing and self._output:
                self._fade(self._output.volume(), self._target_volume(self._playing_mode, state))
            return
        if state.overlay is not None:
            mode = {Overlay.QUESTION: "question", Overlay.CORRECT: "correct",
                    Overlay.WRONG: "wrong"}[state.overlay]
            self._play(mode, loop=True, key=(mode, True))
            return
        if state.base_mode is BaseMode.REST:
            mode = "rest_end" if state.rest_stage is RestStage.REMINDER else "rest"
            self._play(mode, loop=True, key=(mode, True))
            return
        mode = {BaseMode.STANDBY: "standby", BaseMode.NOTICE: "notice",
                BaseMode.DISCUSSION: "discussion"}.get(state.base_mode)
        if mode:
            self._play(mode, loop=True, key=(mode, True))

    def _target_volume(self, mode: str | None, state: ClassroomState | None) -> float:
        volume = self.settings.audio_volume_percent / 100
        if mode in ("notice", "discussion"):
            level = getattr(state, "noise_state", NoiseState.UNKNOWN)
            volume *= {NoiseState.UNKNOWN: .25, NoiseState.QUIET: .25,
                       NoiseState.RISING: .6, NoiseState.LOUD: 1.0}.get(level, .25)
        return volume

    @staticmethod
    def _state_mode(state: ClassroomState) -> str | None:
        if state.overlay is not None:
            return {Overlay.QUESTION: "question", Overlay.CORRECT: "correct",
                    Overlay.WRONG: "wrong"}[state.overlay]
        if state.base_mode is BaseMode.REST:
            return "rest_end" if state.rest_stage is RestStage.REMINDER else "rest"
        return {BaseMode.STANDBY: "standby", BaseMode.NOTICE: "notice",
                BaseMode.DISCUSSION: "discussion"}.get(state.base_mode)

    def preview(self, mode: str) -> None:
        self._play(mode, loop=False, key=(f"preview:{mode}", False))

    def stop(self) -> None:
        if not self.enabled or self._player is None or self._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            self._playing_key = None
            self._looping = False
            self._clear_music_signal()
            return
        if self._fade_stop:
            return
        self._playing_key = None
        self._clear_music_signal()
        self._fade(self._output.volume() if self._output else 0.0, 0.0, stop=True)

    def close(self) -> None:
        self._closed = True
        self._timer.stop()
        self._level_timer.stop()
        self._clear_music_signal()
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
        self._playing_key = None

    def import_file(self, mode: str, path: str | Path, cancellation=None) -> str:
        if mode not in AUDIO_MODES:
            raise ValueError("Unknown audio mode")
        source = Path(path)
        if source.suffix.lower() not in EXTENSIONS or not source.is_file():
            raise ValueError("Choose an existing WAV or MP3 file")
        folder = self.data_dir / "audio" / mode
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{uuid.uuid4().hex}{source.suffix.lower()}"
        temporary = destination.with_suffix(destination.suffix + ".tmp")

        def check_cancelled() -> None:
            cancelled = (cancellation.is_set() if hasattr(cancellation, "is_set")
                         else cancellation() if callable(cancellation) else False)
            if cancelled:
                raise InterruptedError("Audio import cancelled")

        try:
            check_cancelled()
            with source.open("rb") as source_file, temporary.open("wb") as target_file:
                while True:
                    block = source_file.read(1024 * 1024)
                    if not block:
                        break
                    check_cancelled()
                    target_file.write(block)
                target_file.flush()
            check_cancelled()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination.relative_to(self.data_dir).as_posix()

    def _play(self, mode: str, loop: bool, key: tuple[str, bool]) -> None:
        if key == self._playing_key:
            return
        self._playing_key = key
        if not self.enabled or self._closed or self._player is None or self._output is None:
            return
        scenario = "rest" if mode == "rest_end" else mode
        if not key[0].startswith("preview:") and not self.settings.audio_enabled_modes.get(scenario, True):
            self.stop()
            return
        relative = self.settings.audio_files.get(mode, "")
        if not relative:
            self.stop()
            self._status(f"No audio selected for {mode}")
            return
        source = (self.data_dir / relative).resolve()
        try:
            source.relative_to(self.data_dir)
        except ValueError:
            self._stop_now()
            self._status("Audio error: selected path is outside the data folder")
            return
        if relative == DEFAULT_REST_AUDIO and not source.is_file() and self.bundled_rest_source:
            temporary = source.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.bundled_rest_source, temporary)
                temporary.replace(source)
            except OSError as exc:
                self._stop_now()
                self._status(f"Audio error: default rest music is unavailable: {exc}")
                return
            finally:
                temporary.unlink(missing_ok=True)
        if not source.is_file() or source.suffix.lower() not in EXTENSIONS:
            self._stop_now()
            self._status(f"Audio file is missing for {mode}")
            return
        self._timer.stop()
        self._clear_music_signal()
        self._player.stop()
        self._player.setLoops(QMediaPlayer.Loops.Infinite if loop else 1)
        self._looping = loop
        state = getattr(self, "_last_state", None)
        target_volume = (self._target_volume(mode, state)
                         if key[0] == mode else self.settings.audio_volume_percent / 100)
        self._output.setVolume(0.0 if self.settings.audio_fade_ms else target_volume)
        self._player.setSource(QUrl.fromLocalFile(str(source)))
        self._playing_mode = mode
        with self._reference_lock:
            self._reference_mode = mode if loop and mode in ("notice", "discussion") else None
            self._reference_power = 0.0
            self._reference_at = 0.0
        self._player.play()
        if self.settings.audio_fade_ms:
            self._fade(0.0, target_volume)
        self._status(f"Playing: {source.name}")

    def _fade(self, start: float, end: float, stop: bool = False) -> None:
        duration = self.settings.audio_fade_ms
        if not duration:
            if self._output:
                self._output.setVolume(end)
            if stop and self._player:
                self._player.stop()
            return
        self._fade_started = time.monotonic()
        self._fade_from = start
        self._fade_to = end
        self._fade_stop = stop
        self._timer.start()

    def _fade_tick(self) -> None:
        if self.settings.audio_fade_ms <= 0:
            self._timer.stop()
            if self._output:
                self._output.setVolume(self._fade_to)
            if self._fade_stop and self._player:
                self._player.stop()
                self._status("Stopped")
            self._fade_stop = False
            return
        progress = min(1.0, (time.monotonic() - self._fade_started) * 1000 / self.settings.audio_fade_ms)
        if self._output:
            self._output.setVolume(self._fade_from + (self._fade_to - self._fade_from) * progress)
        if progress >= 1:
            self._timer.stop()
            if self._fade_stop and self._player:
                self._player.stop()
                self._status("Stopped")
            self._fade_stop = False

    def _set_volume(self) -> None:
        if self._output and (self._player is None or self._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState):
            self._output.setVolume(self.settings.audio_volume_percent / 100)

    def _stop_now(self) -> None:
        self._timer.stop()
        self._fade_stop = False
        self._clear_music_signal()
        if self._player:
            self._player.stop()
        if self._output:
            self._output.setVolume(0.0)
        self._playing_key = None
        self._looping = False

    def _on_error(self, error, message: str = "") -> None:
        if error:
            self._playing_key = None
            self._looping = False
            self._clear_music_signal()
            self._status(f"Audio error: {message or error}")

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if not self._looping:
                self._playing_key = None
                self._clear_music_signal()
                self._status("Stopped")

    @staticmethod
    def _buffer_rms(buffer) -> float:
        """Return DC-corrected normalized RMS for an interleaved Qt audio buffer."""
        try:
            if not buffer.isValid():
                return 0.0
            import numpy as np
            audio_format = buffer.format()
            sample_format = audio_format.sampleFormat()
            sample_formats = QAudioFormat.SampleFormat
            if sample_format == sample_formats.UInt8:
                dtype, divisor, offset = np.uint8, 128.0, 128.0
            elif sample_format == sample_formats.Int16:
                dtype, divisor, offset = np.int16, 32768.0, 0.0
            elif sample_format == sample_formats.Int32:
                dtype, divisor, offset = np.int32, 2147483648.0, 0.0
            elif sample_format == sample_formats.Float:
                dtype, divisor, offset = np.float32, 1.0, 0.0
            else:
                return 0.0
            data = bytes(buffer.constData())
            width = np.dtype(dtype).itemsize
            count = min(int(buffer.sampleCount()), len(data) // width)
            if count <= 0:
                return 0.0
            channels = max(1, audio_format.channelCount())
            count -= count % channels
            if not count:
                return 0.0
            values = (np.frombuffer(data, dtype=dtype, count=count).astype(np.float32) - offset) / divisor
            values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0).reshape(-1, channels)
            values = np.clip(values, -1.0, 1.0)
            values -= np.mean(values, axis=0, keepdims=True)
            return float(np.sqrt(np.mean(values * values)))
        except (AttributeError, BufferError, OverflowError, TypeError, ValueError):
            return 0.0

    def _on_audio_buffer(self, buffer) -> None:
        if self._playing_mode is None or not self.is_playing or not buffer.isValid():
            return
        rms = self._buffer_rms(buffer)
        # Square-root compression makes quiet musical passages visible; volume
        # includes the configured level and any active fade toward mute.
        output_volume = self._output.volume() if self._output else 0.0
        if self._output and getattr(self._output, "isMuted", lambda: False)():
            output_volume = 0.0
        target = max(0.0, min(1.0, math.sqrt(rms) * output_volume))
        try:
            duration = max(0.001, buffer.duration() / 1_000_000)
        except (AttributeError, TypeError, ValueError):
            duration = 0.02
        tau = 0.075 if target > self._music_level else 0.24
        alpha = 1.0 - math.exp(-duration / tau)
        self._music_level += alpha * (target - self._music_level)
        self._last_audio_buffer = time.monotonic()
        with self._reference_lock:
            self._reference_power = rms * rms * output_volume * output_volume
            self._reference_at = self._last_audio_buffer

    def _on_playback_state(self, state) -> None:
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._clear_music_signal()

    def _expire_music_level(self) -> None:
        if self._last_audio_buffer and time.monotonic() - self._last_audio_buffer > 0.6:
            self._music_level = 0.0
            self._last_audio_buffer = 0.0

    def _clear_music_signal(self) -> None:
        self._music_level = 0.0
        self._last_audio_buffer = 0.0
        self._playing_mode = None
        with self._reference_lock:
            self._reference_mode = None
            self._reference_power = 0.0
            self._reference_at = 0.0

    def _status(self, message: str) -> None:
        if self.on_status:
            self.on_status(message)
