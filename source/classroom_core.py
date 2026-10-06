"""State, settings, and intent routing for Responsive Classroom."""

from __future__ import annotations

import json
import ipaddress
import logging
import os
import re
import tempfile
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)
SCHEMA_VERSION = 17
DEFAULT_REST_AUDIO = "audio/rest/default-rest.mp3"


def normalize_phrase(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    text = re.sub(r"[\s,，。.!！?？:：;；]+", " ", text)
    return re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])", "", text).strip()


class BaseMode(Enum):
    STANDBY = auto()
    NOTICE = auto()
    REST = auto()
    DISCUSSION = auto()


class Overlay(Enum):
    QUESTION = auto()
    CORRECT = auto()
    WRONG = auto()


class NoiseState(Enum):
    UNKNOWN = auto()
    QUIET = auto()
    RISING = auto()
    LOUD = auto()


class AudioHealth(Enum):
    OK = auto()
    SILENT = auto()
    CLIPPING = auto()
    DISCONNECTED = auto()
    UNCALIBRATED = auto()


class RestStage(Enum):
    NONE = auto()
    RESTING = auto()
    REMINDER = auto()


class Pattern(Enum):
    WARM_BREATH = auto()
    GREEN_BREATH = auto()
    ORANGE_ROTATE = auto()
    RED_EXCLAMATION = auto()
    QUESTION_GREEN_ROTATE = auto()
    CORRECT_SQUARE = auto()
    WRONG_X = auto()
    REST_GREEN_BREATH = auto()
    REST_END_WARM_BREATH = auto()
    OFF = auto()
    DISCUSSION_GREEN_BREATH = auto()
    DISCUSSION_ORANGE_FLOW = auto()
    DISCUSSION_RED_BORDER = auto()


def _default_colors() -> dict[str, str]:
    return {
        "warm": "#FFB000", "green": "#7AC84A", "orange": "#FF981F",
        "red": "#F0644A", "question": "#AD5EFF", "correct": "#00FF8A",
        "wrong": "#F500E5", "rest": "#4466FF", "discussion_quiet": "#F0CB50",
        "discussion_rising": "#45BCAA", "discussion_loud": "#CE7098",
        "rest_end": "#FFEFD7",
    }


def _default_aliases() -> dict[str, list[str]]:
    return {
        "STANDBY": ["standby", "stand by", "待機", "待机"],
        "NOTICE": ["notice", "attention", "注意", "請注意", "请注意", "請注意老師", "请注意老师", "class class class"],
        "DISCUSSION": ["discussion", "討論", "讨论"],
        "QUESTION": ["question", "提問", "問題", "提问", "问题"],
        "REST": ["rest", "break", "休息"],
        "CORRECT": ["correct", "right", "答對", "答对", "正確", "正确"],
        "WRONG": ["wrong", "incorrect", "答錯", "答错", "錯誤", "错误"],
    }


def _default_audio_files() -> dict[str, str]:
    return {"standby": "", "question": "", "correct": "", "wrong": "", "notice": "", "rest": DEFAULT_REST_AUDIO, "discussion": ""}


@dataclass(frozen=True)
class Settings:
    question_seconds: int = 10
    feedback_seconds: int = 5
    rest_seconds: int = 600
    rest_reminder_seconds: int = 20
    voice_idle_seconds: int = 0
    # Retained for older settings files; mode changes no longer pause classification.
    mode_change_grace_ms: int = 0
    # Speech-related keys stay inert so calibration from older settings files remains readable.
    calibration: dict[str, object] = field(default_factory=lambda: {
        "duration_sec": 12, "discard_initial_sec": 2, "max_spread_db": 8.0,
        "max_speech_ratio": 0.1, "device_id": "", "timestamp": "",
        "baseline_dbfs": None, "spread_db": None, "speech_ratio": None,
        "quality": "UNKNOWN", "sample_rate": None,
    })
    rest_debug_seconds: int = 10
    debug_rest: bool = False
    brightness_percent: int = 60
    gamma_enabled: bool = True
    gamma: float = 2.2
    renderer_fps: int = 30
    minimum_transition_ms: int = 150
    default_fade_ms: int = 250
    max_flash_hz: float = 2.0
    red_flash_hz: float = 1.5
    rotation_direction: str = "clockwise"
    noise_rising_db: float = 8.0
    noise_loud_db: float = 15.0
    noise_rising_enter_seconds: float = 0.45
    noise_loud_enter_seconds: float = 0.75
    noise_rising_exit_db: float = 5.0
    noise_loud_exit_db: float = 14.0
    noise_exit_seconds: float = 1.2
    noise_smoothing_ms: int = 300
    discussion_noise: dict[str, float] = field(default_factory=lambda: {
        "rising_db": 12.0, "loud_db": 19.0,
        "rising_exit_db": 9.0, "loud_exit_db": 17.0,
    })
    session_logging_enabled: bool = True
    voice_enabled: bool = False
    # Retained only for settings-file compatibility; noise classification is always level-only.
    speech_noise_guard: bool = True
    command_prefix: str = "課堂"
    command_corrections: dict[str, str] = field(default_factory=lambda: {
        "glass": "class", "克拉斯": "class", "課唐": "課堂", "特堂": "課堂", "題問": "提問",
        "not is": "notice", "not it is": "notice", "questions": "question",
    })
    stt_test_phrase: str = "question"
    language: str = "zh_TW"
    microphone_device_id: str = ""
    noise_baseline_dbfs: float | None = None
    spl_calibration_offset_db: float | None = None
    setup_complete: bool = False
    audio_volume_percent: int = 70
    audio_fade_ms: int = 1500
    audio_files: dict[str, str] = field(default_factory=_default_audio_files)
    audio_reactive_modes: dict[str, bool] = field(default_factory=lambda: {
        mode: False for mode in _default_audio_files()
    })
    colors: dict[str, str] = field(default_factory=_default_colors)
    command_aliases: dict[str, list[str]] = field(default_factory=_default_aliases)
    wled_devices: list[dict[str, object]] = field(default_factory=list)
    periods: dict[str, float] = field(default_factory=lambda: {
        "warm": 4.0, "green": 3.0, "orange": 2.0, "question": 2.5,
        "rest_green": 5.0, "correct": 1.2, "wrong": 1.2,
    })
    pattern_levels: dict[str, list[int]] = field(default_factory=lambda: {
        "warm": [25, 65], "green": [35, 75], "orange": [3, 100, 65, 35, 15],
        "red": [35, 100], "question": [25, 100, 70, 40],
        "rest": [20, 50], "rest_end": [25, 70], "correct": [45, 90],
        "wrong": [30, 100],
    })
    rest_end_periods: dict[str, float] = field(default_factory=lambda: {
        "stage_a": 3.0, "stage_b": 2.0, "stage_c": 1.5,
    })
    rest_end_stage_seconds: dict[str, float] = field(default_factory=lambda: {
        "stage_a": 10.0, "stage_b": 5.0,
    })
    transition_ms: dict[str, int] = field(default_factory=lambda: {
        "breath_to_breath": 300, "breath_to_rotate": 250,
        "rotate_to_warning": 150, "warning_to_rotate": 200,
        "base_to_overlay": 150, "overlay_to_base": 250,
        "rest_to_rest_end": 500, "to_off": 150,
    })

    def __post_init__(self) -> None:
        for name in ("question_seconds", "feedback_seconds", "rest_seconds",
                     "rest_reminder_seconds", "rest_debug_seconds"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 86400:
                raise ValueError(f"{name} must be an integer from 1 to 86400")
        if not isinstance(self.voice_idle_seconds, int) or isinstance(self.voice_idle_seconds, bool) or not 0 <= self.voice_idle_seconds <= 86400:
            raise ValueError("voice_idle_seconds must be an integer from 0 to 86400")
        if not isinstance(self.mode_change_grace_ms, int) or isinstance(self.mode_change_grace_ms, bool) or not 0 <= self.mode_change_grace_ms <= 3000:
            raise ValueError("mode_change_grace_ms must be from 0 to 3000")
        calibration_keys = {"duration_sec", "discard_initial_sec", "max_spread_db", "max_speech_ratio", "device_id", "timestamp", "baseline_dbfs", "spread_db", "speech_ratio", "quality", "sample_rate"}
        c = self.calibration
        if not isinstance(c, dict) or set(c) != calibration_keys:
            raise ValueError("calibration fields are invalid")
        if (not isinstance(c["duration_sec"], int) or isinstance(c["duration_sec"], bool) or not 1 <= c["duration_sec"] <= 120 or
                not isinstance(c["discard_initial_sec"], int) or isinstance(c["discard_initial_sec"], bool) or not 0 <= c["discard_initial_sec"] < c["duration_sec"] or
                not isinstance(c["max_spread_db"], (int, float)) or isinstance(c["max_spread_db"], bool) or not 0 < c["max_spread_db"] <= 40 or
                not isinstance(c["max_speech_ratio"], (int, float)) or isinstance(c["max_speech_ratio"], bool) or not 0 <= c["max_speech_ratio"] <= 1 or
                not isinstance(c["device_id"], str) or not isinstance(c["timestamp"], str) or not isinstance(c["quality"], str)):
            raise ValueError("calibration settings are invalid")
        for key, low, high in (("baseline_dbfs", -120, 0), ("spread_db", 0, 120), ("speech_ratio", 0, 1)):
            value = c[key]
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high):
                raise ValueError(f"calibration {key} is invalid")
        if c["sample_rate"] is not None and (not isinstance(c["sample_rate"], int) or isinstance(c["sample_rate"], bool) or c["sample_rate"] <= 0):
            raise ValueError("calibration sample_rate is invalid")
        if not isinstance(self.brightness_percent, int) or isinstance(self.brightness_percent, bool) or not 1 <= self.brightness_percent <= 80:
            raise ValueError("brightness_percent must be from 1 to 80")
        if not isinstance(self.gamma_enabled, bool):
            raise ValueError("gamma_enabled must be a boolean")
        if not isinstance(self.gamma, (int, float)) or isinstance(self.gamma, bool) or not 0.5 <= self.gamma <= 5:
            raise ValueError("gamma must be from 0.5 to 5")
        if not isinstance(self.renderer_fps, int) or isinstance(self.renderer_fps, bool) or not 5 <= self.renderer_fps <= 60:
            raise ValueError("renderer_fps must be from 5 to 60")
        for name in ("minimum_transition_ms", "default_fade_ms"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1000:
                raise ValueError(f"{name} must be from 0 to 1000 milliseconds")
        if (not isinstance(self.max_flash_hz, (int, float)) or isinstance(self.max_flash_hz, bool)
                or not 0.1 <= self.max_flash_hz <= 2.0):
            raise ValueError("max_flash_hz must be from 0.1 to the 2 Hz safety limit")
        if (not isinstance(self.red_flash_hz, (int, float)) or isinstance(self.red_flash_hz, bool)
                or not 0.1 <= self.red_flash_hz <= self.max_flash_hz):
            raise ValueError("red_flash_hz must be within the configured flash limit")
        if not isinstance(self.rotation_direction, str) or self.rotation_direction not in {"clockwise", "counterclockwise"}:
            raise ValueError("rotation_direction must be clockwise or counterclockwise")
        for name in ("noise_rising_db", "noise_loud_db", "noise_rising_enter_seconds",
                     "noise_loud_enter_seconds", "noise_rising_exit_db",
                     "noise_loud_exit_db", "noise_exit_seconds"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 120:
                raise ValueError(f"{name} must be a number from 0 to 120")
        if not isinstance(self.noise_smoothing_ms, int) or isinstance(self.noise_smoothing_ms, bool) or not 100 <= self.noise_smoothing_ms <= 5000:
            raise ValueError("noise_smoothing_ms must be from 100 to 5000")
        if self.noise_loud_db <= self.noise_rising_db:
            raise ValueError("Loud threshold must exceed Rising threshold")
        if (not isinstance(self.discussion_noise, dict) or
                set(self.discussion_noise) != {"rising_db", "loud_db", "rising_exit_db", "loud_exit_db"} or
                any(not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v <= 120
                    for v in self.discussion_noise.values()) or
                self.discussion_noise["loud_db"] <= self.discussion_noise["rising_db"] or
                self.discussion_noise["rising_exit_db"] > self.discussion_noise["rising_db"] or
                self.discussion_noise["loud_exit_db"] > self.discussion_noise["loud_db"]):
            raise ValueError("discussion_noise thresholds are invalid")
        if not isinstance(self.language, str) or self.language not in {"zh_TW", "en_US"}:
            raise ValueError("language must be zh_TW or en_US")
        if (not isinstance(self.debug_rest, bool) or not isinstance(self.voice_enabled, bool) or
                not isinstance(self.speech_noise_guard, bool)):
            raise ValueError("debug_rest, voice_enabled, and speech_noise_guard must be booleans")
        if not isinstance(self.setup_complete, bool):
            raise ValueError("setup_complete must be a boolean")
        if not isinstance(self.session_logging_enabled, bool):
            raise ValueError("session_logging_enabled must be a boolean")
        if not isinstance(self.audio_volume_percent, int) or isinstance(self.audio_volume_percent, bool) or not 0 <= self.audio_volume_percent <= 100:
            raise ValueError("audio_volume_percent must be from 0 to 100")
        if not isinstance(self.audio_fade_ms, int) or isinstance(self.audio_fade_ms, bool) or not 0 <= self.audio_fade_ms <= 10000:
            raise ValueError("audio_fade_ms must be from 0 to 10000")
        if (not isinstance(self.audio_reactive_modes, dict) or
                set(self.audio_reactive_modes) != set(_default_audio_files()) or
                any(not isinstance(v, bool) for v in self.audio_reactive_modes.values())):
            raise ValueError("audio_reactive_modes must define seven boolean mode switches")
        if not isinstance(self.command_prefix, str) or len(self.command_prefix) > 32:
            raise ValueError("command_prefix must contain at most 32 characters")
        if not isinstance(self.stt_test_phrase, str) or not self.stt_test_phrase.strip() or len(self.stt_test_phrase) > 120:
            raise ValueError("stt_test_phrase must contain 1 to 120 characters")
        if not isinstance(self.microphone_device_id, str):
            raise ValueError("microphone_device_id must be a string")
        if self.noise_baseline_dbfs is not None and (
                not isinstance(self.noise_baseline_dbfs, (int, float)) or
                isinstance(self.noise_baseline_dbfs, bool) or not -120 <= self.noise_baseline_dbfs <= 0):
            raise ValueError("noise_baseline_dbfs must be between -120 and 0")
        if self.spl_calibration_offset_db is not None and (
                not isinstance(self.spl_calibration_offset_db, (int, float)) or
                isinstance(self.spl_calibration_offset_db, bool) or not -80 <= self.spl_calibration_offset_db <= 80):
            raise ValueError("spl_calibration_offset_db must be between -80 and 80")
        color_names = set(_default_colors())
        if not isinstance(self.colors, dict) or set(self.colors) != color_names:
            raise ValueError("colors must define all twelve pattern colors")
        if any(not isinstance(value, str) or len(value) != 7 or value[0] != "#" or
               any(c not in "0123456789abcdefABCDEF" for c in value[1:])
               for value in self.colors.values()):
            raise ValueError("colors must use #RRGGBB values")
        period_names = {"warm", "green", "orange", "question", "rest_green", "correct", "wrong"}
        if not isinstance(self.periods, dict) or set(self.periods) != period_names:
            raise ValueError("periods must define all seven pattern cycles")
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.25 <= v <= 60
               for v in self.periods.values()):
            raise ValueError("animation periods must be from 0.25 to 60 seconds")
        level_lengths = {"warm": 2, "green": 2, "orange": 5, "red": 2,
                         "question": 4, "rest": 2, "rest_end": 2,
                         "correct": 2, "wrong": 2}
        if not isinstance(self.pattern_levels, dict) or set(self.pattern_levels) != set(level_lengths):
            raise ValueError("pattern_levels must define all nine pattern brightness settings")
        if any(not isinstance(values, list) or len(values) != level_lengths[name] or
               any(not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100
                   for value in values)
               for name, values in self.pattern_levels.items()):
            raise ValueError("pattern brightness levels must be integer percentages from 0 to 100")
        if any(self.pattern_levels[name][0] > self.pattern_levels[name][1]
               for name in ("warm", "green", "red", "rest", "rest_end", "correct", "wrong")):
            raise ValueError("pattern minimum brightness must not exceed its maximum")
        for name in ("orange", "question"):
            values = self.pattern_levels[name]
            if values[0] > values[-1] or any(a < b for a, b in zip(values[1:], values[2:])):
                raise ValueError(f"{name} ring and comet brightness must decrease from head to tail")
        if self.pattern_levels["orange"][0] > 5:
            raise ValueError("orange ring background must not exceed 5 percent")
        if not isinstance(self.rest_end_periods, dict) or set(self.rest_end_periods) != {"stage_a", "stage_b", "stage_c"} or any(
            not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.25 <= v <= 60
            for v in self.rest_end_periods.values()
        ):
            raise ValueError("rest_end_periods must define three periods from 0.25 to 60 seconds")
        if not isinstance(self.rest_end_stage_seconds, dict) or set(self.rest_end_stage_seconds) != {"stage_a", "stage_b"} or any(
            not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.1 <= v <= 86400
            for v in self.rest_end_stage_seconds.values()
        ) or self.rest_end_stage_seconds["stage_a"] <= self.rest_end_stage_seconds["stage_b"]:
            raise ValueError("rest-end stage thresholds must be positive and descending")
        transition_names = {"breath_to_breath", "breath_to_rotate", "rotate_to_warning",
                            "warning_to_rotate", "base_to_overlay", "overlay_to_base",
                            "rest_to_rest_end", "to_off"}
        if not isinstance(self.transition_ms, dict) or set(self.transition_ms) != transition_names or any(
            not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 1000
            for v in self.transition_ms.values()
        ):
            raise ValueError("transition_ms must define all transitions from 0 to 1000 milliseconds")
        allowed = {"STANDBY", "NOTICE", "DISCUSSION", "QUESTION", "REST", "CORRECT", "WRONG"}
        if not isinstance(self.command_aliases, dict):
            raise ValueError("command_aliases must be an object")
        if set(self.command_aliases) != allowed or any(
            not isinstance(values, list) or not values or
            any(not isinstance(alias, str) or not alias.strip() for alias in values)
            for values in self.command_aliases.values()
        ):
            raise ValueError("command_aliases must define non-empty aliases for all seven commands")
        aliases = [normalize_phrase(alias) for values in self.command_aliases.values() for alias in values]
        if len(aliases) != len(set(aliases)):
            raise ValueError("Command aliases must be unique")
        if not isinstance(self.command_corrections, dict) or len(self.command_corrections) > 100 or any(
            not isinstance(a, str) or not isinstance(b, str) or not a.strip() or not b.strip()
            or len(a) > 120 or len(b) > 120 for a, b in self.command_corrections.items()
        ):
            raise ValueError("誤辨字修正需填入不超過 100 組『誤辨文字 → 正確文字』")
        if not isinstance(self.wled_devices, list) or len(self.wled_devices) > 8:
            raise ValueError("wled_devices must contain at most eight boards")
        macs = set()
        for board in self.wled_devices:
            if not isinstance(board, dict):
                raise ValueError("Each WLED board setting must be an object")
            ipaddress.IPv4Address(str(board.get("ip", "")))
            for name in ("name", "version", "mac"):
                if name in board and not isinstance(board[name], str):
                    raise ValueError(f"WLED {name} must be a string")
            if "rotation" in board and (type(board["rotation"]) is not int or
                                        board["rotation"] not in (0, 90, 180, 270)):
                raise ValueError("WLED rotation must be 0, 90, 180, or 270")
            if "led_count" in board and (type(board["led_count"]) is not int or
                                         not 1 <= board["led_count"] <= 4096):
                raise ValueError("WLED LED count must be between 1 and 4096")
            for name in ("mirror_x", "mirror_y", "serpentine"):
                if name in board and not isinstance(board[name], bool):
                    raise ValueError(f"WLED {name} must be a boolean")
            mac = str(board.get("mac", "")).lower()
            if not mac or mac in macs:
                raise ValueError("Each WLED board must have a unique MAC address")
            macs.add(mac)
            if "enabled" in board and not isinstance(board["enabled"], bool):
                raise ValueError("WLED enabled must be a boolean")
        if not isinstance(self.audio_files, dict) or set(self.audio_files) != {"standby", "question", "correct", "wrong", "notice", "rest", "discussion"} or any(
            not isinstance(path, str) for path in self.audio_files.values()
        ):
            raise ValueError("audio_files must contain paths for all seven mode cues")

    def noise_profile(self, mode: BaseMode) -> NoiseProfile:
        if mode is BaseMode.DISCUSSION:
            return NoiseProfile(**self.discussion_noise)
        return NoiseProfile(self.noise_rising_db, self.noise_loud_db,
                            self.noise_rising_exit_db, self.noise_loud_exit_db)


@dataclass(frozen=True)
class NoiseProfile:
    rising_db: float
    loud_db: float
    rising_exit_db: float
    loud_exit_db: float


@dataclass(frozen=True)
class ClassroomState:
    base_mode: BaseMode = BaseMode.STANDBY
    overlay: Overlay | None = None
    noise_state: NoiseState = NoiseState.QUIET
    rest_stage: RestStage = RestStage.NONE
    noise_enabled: bool = False
    microphone_error: str = ""


class SettingsRepository:
    """Versioned UTF-8 JSON with atomic replacement and a pre-migration backup."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _backup(self, version: int) -> None:
        backup = self.path.with_suffix(self.path.suffix + f".v{version}.bak")
        if backup.exists():
            return
        handle, temporary = tempfile.mkstemp(prefix=backup.name + ".", suffix=".tmp", dir=backup.parent)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(self.path.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, backup)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Settings must be a JSON object")
            version = data.pop("schema_version", 0)
            if type(version) is not int:
                raise ValueError("Settings schema_version must be an integer")
            if version == 0:
                old_rest = data.pop("rest_debug_seconds", 10)
                migrated = Settings(
                    question_seconds=data.pop("question_seconds", 10),
                    feedback_seconds=data.pop("feedback_seconds", 5),
                    rest_debug_seconds=old_rest, debug_rest=True,
                )
                self._backup(version)
                self.save(migrated)
                return migrated
            if version in (2, 3, 4, 5, 6, 7, 8, 9, 10):
                self._backup(version)
                defaults = Settings()
                colors = data.get("colors")
                data["colors"] = {**defaults.colors,
                                   **({key: value for key, value in colors.items()
                                       if key in defaults.colors} if isinstance(colors, dict) else {})}
                # Upgrade only the untouched former defaults; retain user tuning.
                converted_noise_thresholds = {}
                for key, old, new in (
                    ("noise_rising_db", 8.0, 20.0), ("noise_loud_db", 18.0, 36.0),
                    ("noise_rising_exit_db", 5.0, 12.0), ("noise_loud_exit_db", 15.0, 24.0),
                    ("noise_rising_enter_seconds", 1.5, 3.0),
                    ("noise_loud_enter_seconds", 2.0, 5.0),
                    ("noise_exit_seconds", 3.0, 4.0), ("noise_smoothing_ms", 750, 1200),
                ):
                    if data.get(key) == old:
                        converted_noise_thresholds[key] = data[key]
                        data[key] = new
                rising = data.get("noise_rising_db", defaults.noise_rising_db)
                loud = data.get("noise_loud_db", defaults.noise_loud_db)
                rising_exit = data.get("noise_rising_exit_db", defaults.noise_rising_exit_db)
                loud_exit = data.get("noise_loud_exit_db", defaults.noise_loud_exit_db)
                if (loud <= rising or loud - rising < 4 or rising_exit >= rising or
                        loud_exit >= loud):
                    data.update(converted_noise_thresholds)
                if data.get("discussion_noise") == {
                    "rising_db": 16.0, "loud_db": 26.0,
                    "rising_exit_db": 13.0, "loud_exit_db": 23.0,
                }:
                    data["discussion_noise"] = defaults.discussion_noise
                audio_files = data.get("audio_files")
                data["audio_files"] = {**defaults.audio_files,
                                       **(audio_files if isinstance(audio_files, dict) else {})}
                data.setdefault("mode_change_grace_ms", defaults.mode_change_grace_ms)
                data.setdefault("calibration", defaults.calibration)
                for board in data.get("wled_devices", []):
                    if isinstance(board, dict):
                        board.setdefault("enabled", True)
                data.setdefault("discussion_noise", defaults.discussion_noise)
                aliases = data.get("command_aliases")
                if isinstance(aliases, dict):
                    if "DISCUSSION" not in aliases:
                        occupied = {
                            normalize_phrase(alias)
                            for name, values in aliases.items() if name != "DISCUSSION" and isinstance(values, list)
                            for alias in values if isinstance(alias, str)
                        }
                        discussion = [alias for alias in defaults.command_aliases["DISCUSSION"]
                                      if normalize_phrase(alias) not in occupied]
                        if not discussion:
                            discussion = ["discuss"]
                            suffix = 2
                            while normalize_phrase(discussion[0]) in occupied:
                                discussion[0] = f"discuss{suffix}"
                                suffix += 1
                        aliases["DISCUSSION"] = discussion
                corrections = data.get("command_corrections")
                if isinstance(corrections, dict):
                    occupied = {normalize_phrase(phrase) for phrase in corrections}
                    for phrase in ("not is", "not it is", "questions"):
                        if phrase not in occupied and len(corrections) < 100:
                            corrections[phrase] = defaults.command_corrections[phrase]
                old_notice = ["notice", "attention", "請注意老師", "请注意老师", "class class class"]
                if version < 7 and isinstance(aliases, dict) and aliases.get("NOTICE") == old_notice:
                    aliases["NOTICE"] = defaults.command_aliases["NOTICE"]
                if version == 2:
                    legacy_colors = data.get("colors", {})
                    if not isinstance(legacy_colors, dict):
                        legacy_colors = {}
                    data["colors"] = {**defaults.colors, **{
                        key: value for key, value in legacy_colors.items()
                        if key in defaults.colors
                    }}
                    legacy_periods = data.get("periods", {})
                    if not isinstance(legacy_periods, dict):
                        legacy_periods = {}
                    data["periods"] = {**defaults.periods, **{
                        key: value for key, value in legacy_periods.items()
                        if key in defaults.periods
                    }}
                    data["rest_end_periods"] = {
                        **defaults.rest_end_periods,
                        "stage_a": legacy_periods.get("rest_warm", defaults.rest_end_periods["stage_a"]),
                    }
                    data["rest_end_stage_seconds"] = defaults.rest_end_stage_seconds
                    data["transition_ms"] = defaults.transition_ms
                data["pattern_levels"] = {
                    **defaults.pattern_levels,
                    **(data.get("pattern_levels") if isinstance(data.get("pattern_levels"), dict) else {}),
                }
            elif version in (11, 12, 13, 14, 15, 16):
                self._backup(version)
            elif version != SCHEMA_VERSION:
                raise ValueError(f"Unsupported settings schema: {version}")
            if version in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11):
                audio_files = data.get("audio_files")
                if isinstance(audio_files, dict) and audio_files.get("rest") == "":
                    data["audio_files"] = {**audio_files, "rest": DEFAULT_REST_AUDIO}
            if version in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
                # Retune only complete former default profiles; custom values,
                # timers, audio choices and an explicitly cleared Rest stay intact.
                defaults = Settings()
                previous_notice = {"noise_rising_db": 20.0, "noise_loud_db": 36.0,
                                   "noise_rising_exit_db": 12.0, "noise_loud_exit_db": 24.0}
                if all(data.get(k) == v for k, v in previous_notice.items()):
                    data.update({k: getattr(defaults, k) for k in previous_notice})
                if data.get("discussion_noise") == {"rising_db": 32.0, "loud_db": 48.0,
                                                    "rising_exit_db": 22.0, "loud_exit_db": 34.0}:
                    data["discussion_noise"] = defaults.discussion_noise
                previous_timing = {"noise_rising_enter_seconds": 3.0,
                                   "noise_loud_enter_seconds": 5.0,
                                   "noise_exit_seconds": 4.0, "noise_smoothing_ms": 1200}
                if all(data.get(k) == v for k, v in previous_timing.items()):
                    data.update({k: getattr(defaults, k) for k in previous_timing})
            if version == 13:
                defaults = Settings()
                notice_profiles = (
                    {"noise_rising_db": 20.0, "noise_loud_db": 36.0,
                     "noise_rising_exit_db": 12.0, "noise_loud_exit_db": 24.0},
                    {"noise_rising_db": 18.0, "noise_loud_db": 30.0,
                     "noise_rising_exit_db": 12.0, "noise_loud_exit_db": 22.0},
                )
                for profile in notice_profiles:
                    if all(data.get(key) == value for key, value in profile.items()):
                        data.update({key: getattr(defaults, key) for key in profile})
                        break
                discussion_profiles = (
                    {"rising_db": 32.0, "loud_db": 48.0,
                     "rising_exit_db": 22.0, "loud_exit_db": 34.0},
                    {"rising_db": 26.0, "loud_db": 38.0,
                     "rising_exit_db": 18.0, "loud_exit_db": 28.0},
                    {"rising_db": 16.0, "loud_db": 26.0,
                     "rising_exit_db": 12.0, "loud_exit_db": 22.0},
                )
                discussion = data.get("discussion_noise")
                notice_rising = data.get("noise_rising_db", defaults.noise_rising_db)
                notice_loud = data.get("noise_loud_db", defaults.noise_loud_db)
                if isinstance(discussion, dict) and any(
                        discussion == profile for profile in discussion_profiles) and (
                        defaults.discussion_noise["rising_db"] >= notice_rising and
                        defaults.discussion_noise["loud_db"] >= notice_loud):
                    data["discussion_noise"] = defaults.discussion_noise
            if version == 14:
                # Schema 14's full Discussion default is the only profile
                # retuned here; preserve any partially customized profile.
                defaults = Settings()
                if data.get("discussion_noise") == {
                    "rising_db": 16.0, "loud_db": 26.0,
                    "rising_exit_db": 12.0, "loud_exit_db": 22.0,
                } and (
                        defaults.discussion_noise["rising_db"] >= data.get(
                            "noise_rising_db", defaults.noise_rising_db) and
                        defaults.discussion_noise["loud_db"] >= data.get(
                            "noise_loud_db", defaults.noise_loud_db)):
                    data["discussion_noise"] = defaults.discussion_noise
            if version in (14, 15):
                # Retune only untouched loud-level defaults; preserve user tuning.
                if data.get("noise_loud_db") == 18.0:
                    data["noise_loud_db"] = 15.0
                discussion = data.get("discussion_noise")
                if isinstance(discussion, dict) and discussion.get("loud_db") == 22.0:
                    data["discussion_noise"] = {**discussion, "loud_db": 19.0}
            if version == 16:
                # Replace only the prior untouched response defaults; keep custom timing.
                previous_timing = {
                    "noise_rising_enter_seconds": 1.5,
                    "noise_loud_enter_seconds": 2.0,
                    "noise_exit_seconds": 3.0,
                    "noise_smoothing_ms": 750,
                }
                if all(data.get(key) == value for key, value in previous_timing.items()):
                    data.update({key: getattr(Settings(), key) for key in previous_timing})
            data.pop("schema_version", None)
            allowed = Settings.__dataclass_fields__.keys()
            values = {k: v for k, v in data.items() if k in allowed}
            try:
                settings = Settings(**values)
            except (ValueError, TypeError):
                if version != SCHEMA_VERSION:
                    raise
                # Recover unrelated preferences without overwriting the damaged
                # file. Older schemas still use their established migration path.
                return self._recover_current(values)
            if version in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16):
                self.save(settings)
            return settings
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            log.warning("Could not load settings from %s; using defaults: %s", self.path, exc)
            return Settings()

    def _recover_current(self, values: dict) -> Settings:
        # Validate coupled values together so valid custom pairs (for example
        # rising/loud thresholds) are not tested against incompatible defaults.
        groups = [
            ("noise_rising_db", "noise_loud_db", "noise_rising_exit_db", "noise_loud_exit_db"),
            ("max_flash_hz", "red_flash_hz"),
        ]
        grouped = {key for group in groups for key in group}
        groups.extend((key,) for key in values if key not in grouped)
        recovered = {}
        for group in groups:
            candidate = {key: values[key] for key in group if key in values}
            try:
                Settings(**{**recovered, **candidate})
            except (ValueError, TypeError) as exc:
                log.warning("Ignoring invalid settings field(s) %s in %s: %s",
                            ", ".join(group), self.path, exc)
            else:
                recovered.update(candidate)
        return Settings(**recovered)

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": SCHEMA_VERSION, **asdict(settings)}
        handle, temporary = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class PatternMapper:
    """Map classroom state to a pattern enum; it performs no rendering or output."""

    def map(self, state: ClassroomState) -> Pattern:
        if state.overlay is not None:
            return {
                Overlay.QUESTION: Pattern.QUESTION_GREEN_ROTATE,
                Overlay.CORRECT: Pattern.CORRECT_SQUARE,
                Overlay.WRONG: Pattern.WRONG_X,
            }[state.overlay]
        if state.base_mode is BaseMode.STANDBY:
            return Pattern.WARM_BREATH
        if state.base_mode in (BaseMode.NOTICE, BaseMode.DISCUSSION):
            if state.base_mode is BaseMode.DISCUSSION:
                return {
                    NoiseState.UNKNOWN: Pattern.WARM_BREATH,
                    NoiseState.QUIET: Pattern.DISCUSSION_GREEN_BREATH,
                    NoiseState.RISING: Pattern.DISCUSSION_ORANGE_FLOW,
                    NoiseState.LOUD: Pattern.DISCUSSION_RED_BORDER,
                }[state.noise_state]
            return {
                NoiseState.UNKNOWN: Pattern.WARM_BREATH,
                NoiseState.QUIET: Pattern.GREEN_BREATH,
                NoiseState.RISING: Pattern.ORANGE_ROTATE,
                NoiseState.LOUD: Pattern.RED_EXCLAMATION,
            }[state.noise_state]
        return (Pattern.REST_END_WARM_BREATH if state.rest_stage is RestStage.REMINDER
                else Pattern.REST_GREEN_BREATH)


class ClassroomController:
    """The sole owner of classroom state transitions and temporary timers."""

    def __init__(self, settings: Settings,
                 schedule: Callable[[float, Callable[[], None]], Callable[[], None]],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self._schedule = schedule
        self._clock = clock
        self.state = ClassroomState()
        self._timer_cancellers: dict[str, Callable[[], None] | None] = {
            "base": None, "overlay": None, "voice": None,
        }
        self._timer_generations = {"base": 0, "overlay": 0, "voice": 0}
        self._deadlines: dict[str, float | None] = {"base": None, "overlay": None, "voice": None}
        self._on_change: Callable[[ClassroomState], None] | None = None
        self._mapper = PatternMapper()
        self.pattern_started_at = clock()
        self._last_pattern = self._mapper.map(self.state)
        self._manual_revision = 0
        self.last_source = "system"
        self._microphone_ok = True
        self._detection_requested = False

    @property
    def manual_revision(self) -> int:
        return self._manual_revision

    def remaining_seconds(self, timer: str = "base") -> int | None:
        deadline = self._deadlines[timer]
        return None if deadline is None else max(0, int(deadline - self._clock() + 0.999))

    def set_on_change(self, callback: Callable[[ClassroomState], None]) -> None:
        self._on_change = callback

    def update_settings(self, settings: Settings) -> None:
        if self.settings.voice_enabled and not settings.voice_enabled:
            self.cancel_pending_voice()
        self.settings = settings

    def cancel_pending_voice(self) -> None:
        self._manual_revision += 1
        self._cancel_timer("voice")

    def set_base_mode(self, mode: BaseMode, source: str = "manual") -> None:
        self._mark_source(source)
        self._cancel_timer("base")
        self._cancel_timer("overlay")
        self._detection_requested = mode in (BaseMode.NOTICE, BaseMode.DISCUSSION)
        enabled = self._detection_requested and self._microphone_ok
        noise = self.state.noise_state if enabled and self._microphone_ok else NoiseState.UNKNOWN
        if enabled and mode is not self.state.base_mode and self.state.base_mode in (BaseMode.NOTICE, BaseMode.DISCUSSION):
            noise = NoiseState.UNKNOWN
        self.state = ClassroomState(mode, noise_state=noise, rest_stage=(
            RestStage.RESTING if mode is BaseMode.REST else RestStage.NONE),
            noise_enabled=enabled, microphone_error=self.state.microphone_error)
        self._notify()
        if mode is BaseMode.REST:
            duration = self.settings.rest_debug_seconds if self.settings.debug_rest else self.settings.rest_seconds
            self._start_timer("base", duration, self._begin_rest_reminder)

    def show_overlay(self, overlay: Overlay, source: str = "manual") -> None:
        self._mark_source(source)
        self._cancel_timer("overlay")
        self.state = ClassroomState(self.state.base_mode, overlay, self.state.noise_state,
                                    self.state.rest_stage, self.state.noise_enabled,
                                    self.state.microphone_error)
        self._notify()
        duration = self.settings.question_seconds if overlay is Overlay.QUESTION else self.settings.feedback_seconds
        self._start_timer("overlay", duration, self._clear_overlay)

    def set_noise_state(self, noise: NoiseState) -> None:
        if self.state.base_mode not in (BaseMode.NOTICE, BaseMode.DISCUSSION) or not self.state.noise_enabled:
            return
        if not self._microphone_ok:
            noise = NoiseState.UNKNOWN
        if noise is not self.state.noise_state:
            self.state = ClassroomState(self.state.base_mode, self.state.overlay, noise,
                                        self.state.rest_stage, self.state.noise_enabled,
                                        self.state.microphone_error)
            self._notify()

    def set_detection_enabled(self, enabled: bool, source: str = "manual") -> None:
        self._mark_source(source)
        active_mode = self.state.base_mode in (BaseMode.NOTICE, BaseMode.DISCUSSION)
        self._detection_requested = bool(enabled) and active_mode
        enabled = bool(enabled) and active_mode and self._microphone_ok
        noise = self.state.noise_state if enabled else NoiseState.UNKNOWN
        self.state = ClassroomState(self.state.base_mode, self.state.overlay, noise,
                                    self.state.rest_stage, enabled, self.state.microphone_error)
        self._notify()

    def set_microphone_status(self, ok: bool, message: str = "") -> None:
        self._microphone_ok = ok
        state = ClassroomState(
            self.state.base_mode, self.state.overlay,
            self.state.noise_state if ok else NoiseState.UNKNOWN,
            self.state.rest_stage, self._detection_requested and ok, "" if ok else message,
        )
        if state == self.state:
            return
        self.state = state
        self._notify()

    def submit_intent(self, intent: str, source: str = "manual", revision: int | None = None) -> bool:
        """Apply a named command; discard voice results made stale by later manual input."""
        if source == "voice" and revision != self._manual_revision:
            return False
        try:
            mode = BaseMode[intent.upper()]
        except KeyError:
            try:
                overlay = Overlay[intent.upper()]
            except KeyError:
                return False
            self.show_overlay(overlay, source)
        else:
            self.set_base_mode(mode, source)
        if source == "voice":
            self._cancel_timer("voice")
            continuous = self.state.base_mode in (BaseMode.NOTICE, BaseMode.DISCUSSION)
            if self.settings.voice_idle_seconds and not continuous and (
                    self.state.base_mode is not BaseMode.STANDBY or self.state.overlay is not None):
                self._start_timer("voice", self.settings.voice_idle_seconds, self._voice_idle_expired)
        return True

    def reject_voice_command(self, reason: str, revision: int | None) -> bool:
        """Rejected, stale, or rate-limited speech never changes classroom state."""
        return False

    def _voice_idle_expired(self) -> None:
        if self.state.base_mode in (BaseMode.NOTICE, BaseMode.DISCUSSION):
            return
        self.cancel_pending_voice()
        self.set_base_mode(BaseMode.STANDBY, "timer")

    def _mark_source(self, source: str) -> None:
        self.last_source = source
        if source == "manual":
            self.cancel_pending_voice()

    def _begin_rest_reminder(self) -> None:
        if self.state.base_mode is not BaseMode.REST:
            return
        self.state = ClassroomState(BaseMode.REST, self.state.overlay, self.state.noise_state,
                                    RestStage.REMINDER, False, self.state.microphone_error)
        self._notify()
        self._start_timer("base", self.settings.rest_reminder_seconds, self._finish_rest)

    def _finish_rest(self) -> None:
        self._mark_source("timer")
        self._cancel_timer("overlay")
        self._cancel_timer("base")
        self._cancel_timer("voice")
        self.state = ClassroomState(BaseMode.STANDBY, microphone_error=self.state.microphone_error)
        self._notify()

    def _clear_overlay(self) -> None:
        self._cancel_timer("overlay")
        self.state = ClassroomState(self.state.base_mode, noise_state=self.state.noise_state,
                                    rest_stage=self.state.rest_stage, noise_enabled=self.state.noise_enabled,
                                    microphone_error=self.state.microphone_error)
        self._notify()

    def _start_timer(self, name: str, seconds: int, callback: Callable[[], None]) -> None:
        generation = self._timer_generations[name]
        self._deadlines[name] = self._clock() + seconds

        def fire() -> None:
            if generation == self._timer_generations[name]:
                self._timer_cancellers[name] = None
                self._deadlines[name] = None
                callback()

        self._timer_cancellers[name] = self._schedule(seconds, fire)

    def _cancel_timer(self, name: str) -> None:
        self._timer_generations[name] += 1
        self._deadlines[name] = None
        cancel = self._timer_cancellers[name]
        if cancel is not None:
            cancel()
            self._timer_cancellers[name] = None

    def _notify(self) -> None:
        pattern = self._mapper.map(self.state)
        if pattern is not self._last_pattern:
            self.pattern_started_at = self._clock()
            self._last_pattern = pattern
        if self._on_change is not None:
            self._on_change(self.state)
