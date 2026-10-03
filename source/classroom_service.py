"""Qt service boundary for the browser classroom interface."""

from __future__ import annotations

import base64
import ipaddress
import json
import sys
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QMicrophonePermission, QObject, QThread, QTimer, Qt, Signal, Slot

from classroom_audio import AudioRuntime, CommandParser
from classroom_core import (
    AudioHealth, BaseMode, ClassroomController, NoiseState, Overlay, PatternMapper,
    RestStage, Settings, SettingsRepository,
)
from classroom_hardware import (
    LatestFrameWorker, WledDevice, WledSession, discover_subnet, inspect_device, local_networks,
)
from classroom_logging import SessionLogger
from classroom_i18n import translate
from classroom_playback import AudioPlayback
from classroom_resources import VERSION, default_rest_source
from pattern_renderer import FrameBlender, music_wave


TIMER_KEYS = ("question_seconds", "feedback_seconds", "rest_seconds",
              "rest_reminder_seconds", "voice_idle_seconds")
NOISE_KEYS = ("noise_rising_db", "noise_loud_db", "noise_rising_enter_seconds",
              "noise_loud_enter_seconds", "noise_rising_exit_db", "noise_loud_exit_db",
              "noise_exit_seconds", "noise_smoothing_ms", "discussion_noise")
MODES = {name.lower(): name for name in ("STANDBY", "NOTICE", "DISCUSSION", "REST",
                                          "QUESTION", "CORRECT", "WRONG")}


class _Job(QThread):
    result = Signal(object, object)

    def __init__(self, target, parent=None):
        super().__init__(parent)
        self.target = target
        self._stop_requested = threading.Event()

    def run(self):
        try:
            value = self.target(self._stop_requested)
            self.result.emit(value, None)
        except Exception as exc:
            self.result.emit(None, str(exc))

    def cancel(self):
        self._stop_requested.set()


class _AudioEvents(QObject):
    status = Signal(int, str, bool, str)
    noise = Signal(int, object)
    voice = Signal(int, bool)
    command = Signal(int, object, int)
    calibrated = Signal(int, float)
    playback_status = Signal(str)
    hardware = Signal(str, str)


class ClassroomService(QObject):
    stateChanged = Signal(str)
    frameChanged = Signal(str)
    fileRequested = Signal(str)
    error = Signal(str)
    shutdownFinished = Signal()

    def __init__(self, repository, resource_root, start_io=True, parent=None):
        super().__init__(parent)
        self.repository = repository if isinstance(repository, SettingsRepository) else SettingsRepository(Path(repository))
        self.resource_root = Path(resource_root)
        self.start_io = bool(start_io)
        self.settings = self.repository.load()
        self.controller = ClassroomController(self.settings, self._schedule)
        self.controller.set_microphone_status(False, "尚未啟動")
        self.controller.set_on_change(self._state_changed)
        self.mapper = PatternMapper()
        self.light_settings = replace(Settings(), brightness_percent=30, gamma_enabled=False,
                                      rest_reminder_seconds=self.settings.rest_reminder_seconds)
        self.blender = FrameBlender()
        self.logger = SessionLogger(self.repository.path.parent / "sessions",
                                    enabled=self.start_io and self.settings.session_logging_enabled)
        self.audio = None
        self._microphone_permission_pending = False
        self._listening_enabled = None
        self.worker = None
        self.playback = AudioPlayback(self.repository.path.parent, self.settings,
                                      on_status=self._playback_status, parent=self,
                                      enabled=self.start_io,
                                      bundled_rest_source=default_rest_source(self.resource_root))
        self._events = _AudioEvents(self)
        self._events.status.connect(self._audio_status)
        self._events.noise.connect(self._noise_received)
        self._events.voice.connect(self._voice_received)
        self._events.command.connect(self._command_received)
        self._events.calibrated.connect(self._calibrated)
        self._events.playback_status.connect(self._set_audio_message)
        self._events.hardware.connect(self._on_hardware)
        self._audio_generation = 0
        self._noise_context = (BaseMode.STANDBY, 0)
        self._last_reading = None
        self._transcript = {"raw": "", "corrected": "", "status": "", "active": False}
        self._speech_ready = False
        self._voice_suppressed = False
        self._microphone = {"ok": False, "message": "尚未啟動"}
        self._speech = {"ok": False, "message": "語音已關閉"}
        self._hardware_messages = {}
        self._busy = {"scan": False, "connect": False, "calibration": False, "audio_stopping": False}
        self._output_enabled = bool(self.settings.wled_devices)
        self._output_stopping = False
        self._audio_stopping = False
        self._closing = False
        self._shutdown_complete = False
        self._cleanup_job = None
        self._jobs = set()
        self._last_frame = bytes(192)
        self._reading = None
        self._settings_revision = 0
        self._messages = {"board": "", "noise": "", "voice": "", "audio": "", "timers": ""}
        self._microphones = []
        self._networks = []
        self._found_boards = []
        self._calibration_started = 0.0
        self._last_periodic_publish = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(max(10, round(1000 / self.light_settings.renderer_fps)))
        if self.start_io:
            QTimer.singleShot(0, self._start_audio)
            QTimer.singleShot(0, self._start_output)
            self.refresh_microphones()
            self.refresh_networks()
        self._publish()

    def _schedule(self, seconds, callback):
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        timer.timeout.connect(timer.deleteLater)
        timer.start(round(seconds * 1000))
        return lambda: (timer.stop(), timer.deleteLater())

    @staticmethod
    def _enum_name(value):
        return value.name if value is not None else None

    def snapshot(self):
        state = self.controller.state
        overlay_remaining = self.controller.remaining_seconds("overlay")
        rest_remaining = self.controller.remaining_seconds("base") if state.base_mode is BaseMode.REST else None
        noise = self._reading
        return {
            "version": VERSION,
            "settings": asdict(self.settings),
            "settings_revision": self._settings_revision,
            "current": {
                "base_mode": state.base_mode.name, "overlay": self._enum_name(state.overlay),
                "rest_stage": state.rest_stage.name, "mode": state.overlay.name if state.overlay else state.base_mode.name,
                "noise_state": state.noise_state.name, "noise_enabled": state.noise_enabled,
                "pattern": self.mapper.map(state).name,
                "remaining_seconds": overlay_remaining if overlay_remaining is not None else rest_remaining,
                "voice_idle_seconds": self.controller.remaining_seconds("voice"),
            },
            "statuses": {"microphone": dict(self._microphone), "speech": dict(self._speech),
                         "output": {"enabled": self._output_enabled, "stopping": self._output_stopping},
                         "audio": {"playing": self.playback.is_playing,
                                   "detection_paused": self.playback.suppress_detection}},
            "noise": {"dbfs": getattr(noise, "dbfs", None), "smoothed_dbfs": getattr(noise, "smoothed_dbfs", None),
                      "relative_db": getattr(noise, "relative_db", None),
                      "health": self._enum_name(getattr(noise, "health", None)),
                      "speech_excluded": bool(noise.speech_excluded) if noise is not None else False,
                      "speech_guard_ready": bool(noise.speech_guard_ready) if noise is not None else True,
                      "calibration_progress": getattr(noise, "calibration_progress", 0.0)},
            "transcript": dict(self._transcript),
            "boards": [dict(item, status=self._board_status(item), health=self._hardware_messages.get(item.get("ip"), ""))
                       for item in self.settings.wled_devices],
            "microphones": list(self._microphones), "networks": list(self._networks),
            "found_boards": [device.to_dict() if hasattr(device, "to_dict") else dict(device)
                             for device in self._found_boards],
            "busy": dict(self._busy), "messages": dict(self._messages),
        }

    def _localized_snapshot(self, data):
        language = self.settings.language
        data["statuses"]["microphone"]["available"] = bool(self.audio and self.audio.is_running)
        for key in ("microphone", "speech"):
            data["statuses"][key]["message"] = translate(data["statuses"][key]["message"], language)
        data["transcript"]["status"] = translate(data["transcript"]["status"], language)
        data["boards"] = [dict(board, health=translate(board["health"], language))
                           for board in data["boards"]]
        data["messages"] = {key: translate(value, language) for key, value in data["messages"].items()}
        return data

    @Slot(result=str)
    def snapshot_json(self):
        return json.dumps(self._localized_snapshot(self.snapshot()), ensure_ascii=False)

    def _publish(self):
        self._last_periodic_publish = time.monotonic()
        data = self._localized_snapshot(self.snapshot())
        self.stateChanged.emit(json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    def _state_changed(self, state):
        if self._noise_context[0] is not state.base_mode:
            self._noise_context = (state.base_mode, self._noise_context[1] + 1)
            self._reading = None
        self.playback.handle_state(state)
        self._sync_voice_capture()
        self._publish()

    def _sync_voice_capture(self):
        suppressed = bool(self.playback.suppress_detection)
        if suppressed and not self._voice_suppressed:
            self.controller.cancel_pending_voice()
        self._voice_suppressed = suppressed
        desired = self.settings.voice_enabled and not suppressed
        if self.audio and desired != self._listening_enabled:
            self.audio.set_listening(desired)
            self._listening_enabled = desired

    def _save(self, settings, restart_audio=False):
        try:
            self.repository.save(settings)
            old_devices = self.settings.wled_devices
            self.settings = settings
            self.light_settings = replace(self.light_settings,
                                          rest_reminder_seconds=settings.rest_reminder_seconds)
            self._settings_revision += 1
            self._noise_context = (self._noise_context[0], self._noise_context[1] + 1)
            self._reading = None
            self.controller.update_settings(settings)
            self.controller.cancel_pending_voice()
            self.playback.apply_settings(settings)
            if self.audio:
                self.audio.update_settings(settings)
                if restart_audio:
                    self._restart_audio()
            if old_devices != settings.wled_devices:
                if self.worker:
                    self._stop_output_then_restart()
                elif self._output_enabled:
                    self._start_output()
            self._publish()
            return True
        except Exception as exc:
            self._messages["board"] = f"設定儲存失敗：{exc}"
            self.error.emit(str(exc))
            self._publish()
            raise

    @Slot(str, str, result=str)
    def command(self, action, payload_json):
        try:
            payload = json.loads(payload_json or "{}")
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            handler = getattr(self, f"_do_{action}", None)
            if handler is None:
                raise ValueError(f"unknown action: {action}")
            handler(payload)
            return json.dumps({"ok": True}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    def _do_mode(self, p):
        mode = MODES.get(str(p.get("mode", "")).lower())
        if not mode:
            raise ValueError("unknown classroom mode")
        if not self.controller.submit_intent(mode):
            raise ValueError("classroom mode was rejected")

    def _do_voice(self, p):
        enabled = self._bool(p, "enabled")
        if enabled == self.settings.voice_enabled:
            return
        if enabled and self.controller.state.base_mode is BaseMode.REST:
            self.playback.stop()
        if self._save(replace(self.settings, voice_enabled=enabled)):
            self._sync_voice_capture()
        self._speech["message"] = "等待語音指令" if enabled else "語音已關閉"
        self._speech["ok"] = self._speech_ready if enabled else False
        self._publish()

    def _do_detection(self, p):
        self.controller.set_detection_enabled(self._bool(p, "enabled"))

    def _do_output(self, p):
        self._output_enabled = self._bool(p, "enabled")
        if not self._output_enabled:
            self._stop_output_then_restart()
        else:
            self._start_output()
        self._publish()

    def _do_language(self, p):
        language = p.get("language")
        if language not in ("zh_TW", "en_US"):
            raise ValueError("language must be zh_TW or en_US")
        self._save(replace(self.settings, language=language))

    def _do_save_timers(self, p):
        self._save(replace(self.settings, **{key: p[key] for key in TIMER_KEYS}))
        self._messages["timers"] = "已儲存；新計時會使用更新後的時間。"
        self._publish()

    def _noise_settings(self, p):
        fields = {key: p[key] for key in NOISE_KEYS}
        if "speech_noise_guard" in p:
            fields["speech_noise_guard"] = self._bool(p, "speech_noise_guard")
        if isinstance(fields["discussion_noise"], dict):
            fields["discussion_noise"] = {key: fields["discussion_noise"][key]
                                           for key in ("rising_db", "loud_db", "rising_exit_db", "loud_exit_db")}
        updated = replace(self.settings, **fields)
        for profile in (updated.noise_profile(BaseMode.NOTICE), updated.noise_profile(BaseMode.DISCUSSION)):
            if (profile.loud_db - profile.rising_db < 4 or profile.rising_exit_db >= profile.rising_db
                    or profile.loud_exit_db >= profile.loud_db):
                raise ValueError("noise thresholds or recovery thresholds are invalid")
        if (updated.discussion_noise["rising_db"] < updated.noise_rising_db or
                updated.discussion_noise["loud_db"] < updated.noise_loud_db):
            raise ValueError("discussion thresholds must not be below notice")
        return updated

    def _do_save_noise(self, p):
        updated = self._noise_settings(p)
        if self._save(updated):
            self._messages["noise"] = "音量設定已儲存。"
        self._publish()

    def _voice_settings(self, p, settings):
        settings = replace(settings, command_aliases=p["aliases"],
                           command_corrections=p["corrections"])
        CommandParser(settings)
        return settings

    def _do_save_input_settings(self, p):
        # Validate both forms before the single repository write. A conflicting
        # alias or invalid threshold must not partially save the other form.
        settings = self._voice_settings(p, self._noise_settings(p))
        if self._save(settings):
            self._messages["noise"] = self._messages["voice"] = "已儲存"
        self._publish()

    def _do_save_voice(self, p):
        settings = self._voice_settings(p, self.settings)
        if self._save(settings):
            self._messages["voice"] = "語音設定已儲存。"
        self._publish()

    def _do_microphone(self, p):
        device_id = p.get("device_id", "")
        if not isinstance(device_id, str):
            raise ValueError("device_id must be a string")
        if device_id == self.settings.microphone_device_id:
            return
        calibration = {**self.settings.calibration, "quality": "UNKNOWN", "baseline_dbfs": None,
                       "spread_db": None, "speech_ratio": None, "device_id": device_id,
                       "sample_rate": None, "timestamp": ""}
        self._save(replace(self.settings, microphone_device_id=device_id,
                           noise_baseline_dbfs=None, calibration=calibration), restart_audio=True)
        self.controller.set_microphone_status(False, "尚未校準")

    def _do_refresh_microphones(self, _p):
        self.refresh_microphones()
        if self.audio is None:
            self._start_audio()

    def _do_refresh_networks(self, _p):
        self.refresh_networks()

    def _do_calibrate(self, _p):
        if not self.audio or not self.audio.is_running:
            raise RuntimeError("麥克風尚未啟動")
        self.audio.start_calibration(self.settings.calibration["duration_sec"])
        self._busy["calibration"] = True
        self._calibration_started = time.monotonic()
        self._publish()

    def _do_connect(self, p):
        if not self.start_io:
            raise RuntimeError("hardware I/O is disabled")
        ip = str(p.get("ip", "")).strip()
        ipaddress.IPv4Address(ip)
        if self._busy["connect"]:
            raise RuntimeError("connection check already running")
        self._busy["connect"] = True
        self._messages["board"] = ""
        self._publish()
        job = _Job(lambda _stop: inspect_device(ip), self)
        self._keep_job(job, lambda device, error: self._connected(device, error))
        job.start()

    def _do_scan(self, p):
        if not self.start_io:
            raise RuntimeError("hardware I/O is disabled")
        cidr = str(p.get("cidr", ""))
        network = ipaddress.ip_network(cidr, strict=False)
        if network.version != 4 or network.num_addresses > 1024:
            raise ValueError("choose an IPv4 subnet with at most 1024 addresses")
        if self._busy["scan"]:
            raise RuntimeError("scan already running")
        self._busy["scan"] = True
        self._messages["board"] = ""
        self._publish()
        job = _Job(lambda stop: discover_subnet(cidr, stop=stop), self)
        self._keep_job(job, lambda devices, error: self._scan_done(devices, error))
        job.start()

    def _do_add_found(self, p):
        ips = p.get("ips")
        if not isinstance(ips, list) or any(not isinstance(ip, str) for ip in ips):
            raise ValueError("ips must be a list of IPv4 addresses")
        found = {device.ip: device for device in self._found_boards}
        devices = list(self.settings.wled_devices)
        by_mac = {str(item.get("mac", "")).lower(): i for i, item in enumerate(devices)}
        for ip in ips:
            device = found.get(ip)
            if device is None:
                raise ValueError(f"board was not found in latest scan: {ip}")
            data = device.to_dict()
            index = by_mac.get(device.mac.lower())
            if index is None:
                if len(devices) >= 8:
                    raise ValueError("at most eight boards can be saved")
                data["enabled"] = True
                by_mac[device.mac.lower()] = len(devices)
                devices.append(data)
            else:
                data.update({key: devices[index].get(key, data.get(key))
                             for key in ("rotation", "mirror_x", "mirror_y", "serpentine")})
                data["enabled"] = True
                devices[index] = data
        self._output_enabled = True
        self._save(replace(self.settings, wled_devices=devices))

    def _do_enable_board(self, p):
        mac = str(p.get("mac", "")).lower()
        enabled = self._bool(p, "enabled")
        if not any(str(item.get("mac", "")).lower() == mac for item in self.settings.wled_devices):
            raise ValueError("unknown board MAC")
        if all(str(item.get("mac", "")).lower() != mac or item.get("enabled", True) == enabled
               for item in self.settings.wled_devices):
            return
        devices = [dict(item, enabled=enabled) if str(item.get("mac", "")).lower() == mac else item
                   for item in self.settings.wled_devices]
        self._save(replace(self.settings, wled_devices=devices))

    def _do_remove_board(self, p):
        mac = str(p.get("mac", "")).lower()
        devices = [item for item in self.settings.wled_devices if str(item.get("mac", "")).lower() != mac]
        if len(devices) == len(self.settings.wled_devices):
            raise ValueError("unknown board MAC")
        if not devices:
            self._output_enabled = False
        self._save(replace(self.settings, wled_devices=devices))

    def _do_orientation(self, p):
        mac = str(p.get("mac", "")).lower()
        rotation = p.get("rotation")
        if rotation not in (0, 90, 180, 270):
            raise ValueError("rotation must be 0, 90, 180, or 270")
        devices = []
        found = False
        for old in self.settings.wled_devices:
            if str(old.get("mac", "")).lower() == mac:
                old = dict(old, rotation=rotation,
                           serpentine=self._payload_bool(p, "serpentine"),
                           mirror_x=self._payload_bool(p, "mirror_x"),
                           mirror_y=self._payload_bool(p, "mirror_y"))
                found = True
            devices.append(old)
        if not found:
            raise ValueError("unknown board MAC")
        self._save(replace(self.settings, wled_devices=devices))

    def _do_choose_audio(self, p):
        mode = self._audio_mode(p)
        self.fileRequested.emit(mode)

    def _do_audio_settings(self, p):
        self._save(replace(self.settings, audio_volume_percent=p["volume_percent"],
                           audio_fade_ms=p["fade_ms"]))

    def _do_audio_reactive(self, p):
        mode = self._audio_mode(p)
        enabled = self._bool(p, "enabled")
        self._save(replace(self.settings, audio_reactive_modes={
            **self.settings.audio_reactive_modes, mode: enabled,
        }))

    def _do_preview_audio(self, p):
        self.playback.preview(self._audio_mode(p))

    def _do_stop_audio(self, _p):
        self.playback.stop()

    def _do_clear_audio(self, p):
        mode = self._audio_mode(p)
        files = dict(self.settings.audio_files)
        files[mode] = ""
        self._save(replace(self.settings, audio_files=files))

    @staticmethod
    def _audio_mode(payload):
        mode = str(payload.get("mode", "")).lower()
        if mode not in ("standby", "question", "correct", "wrong", "notice", "rest", "discussion"):
            raise ValueError("unknown audio cue")
        return mode

    @staticmethod
    def _bool(payload, key):
        value = payload.get(key)
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be boolean")
        return value

    @staticmethod
    def _payload_bool(payload, key):
        value = payload.get(key, False)
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be boolean")
        return value

    def import_audio(self, mode, path):
        mode = self._audio_mode({"mode": mode})
        job = _Job(lambda stop: self.playback.import_file(mode, str(path), cancellation=stop), self)
        self._keep_job(job, lambda relative, error: self._audio_imported(mode, relative, error))
        job.start()

    def _audio_imported(self, mode, relative, error):
        if error:
            self._messages["audio"] = error
            self.error.emit(error)
        else:
            files = dict(self.settings.audio_files)
            files[mode] = relative
            try:
                self._save(replace(self.settings, audio_files=files))
            except Exception as exc:
                self._messages["audio"] = f"音檔已匯入，但設定無法儲存：{exc}"
        self._publish()

    def refresh_microphones(self):
        if not self.start_io or any(getattr(job, "_microphone_refresh", False) for job in self._jobs):
            return
        job = _Job(lambda _stop: AudioRuntime.microphones(), self)
        job._microphone_refresh = True
        self._keep_job(job, self._microphones_refreshed)
        job.start()

    def _microphones_refreshed(self, devices, error):
        if error:
            self._microphones = [{"id": "", "name": "系統預設麥克風"}]
            self._microphone = {"ok": False, "message": error}
        else:
            self._microphones = [{"id": "", "name": "系統預設麥克風"}] + [
                {"id": device_id, "name": name} for device_id, name in devices]
        self._publish()

    def refresh_networks(self):
        if (not self.start_io or any(getattr(job, "_network_refresh", False) for job in self._jobs)):
            return
        job = _Job(lambda _stop: local_networks(), self)
        job._network_refresh = True
        self._keep_job(job, self._networks_refreshed)
        job.start()

    def _networks_refreshed(self, networks, error):
        if error:
            self._messages["board"] = error
        else:
            self._networks = [{"name": name, "address": address, "cidr": cidr}
                              for name, address, cidr in networks]
        self._publish()

    def _keep_job(self, job, callback):
        self._jobs.add(job)
        job.result.connect(lambda value, error: callback(value, error) if not self._closing else None,
                           Qt.ConnectionType.QueuedConnection)
        job.finished.connect(lambda: (self._jobs.discard(job), job.deleteLater()))

    def _connected(self, device, error):
        self._busy["connect"] = False
        if error:
            self._messages["board"] = error
            self._publish()
            return
        devices = list(self.settings.wled_devices)
        index = next((i for i, item in enumerate(devices)
                      if str(item.get("mac", "")).lower() == device.mac.lower()), None)
        value = device.to_dict()
        if index is None:
            if len(devices) >= 8:
                self._messages["board"] = "最多可加入 8 塊燈板。"
                self._publish()
                return
            value["enabled"] = True
            devices.append(value)
        else:
            value.update({key: devices[index].get(key, value.get(key))
                          for key in ("rotation", "mirror_x", "mirror_y", "serpentine")})
            value["enabled"] = True
            devices[index] = value
        self._output_enabled = True
        self._save(replace(self.settings, wled_devices=devices))

    def _scan_done(self, devices, error):
        self._busy["scan"] = False
        self._found_boards = [] if error else list(devices or [])
        self._messages["board"] = error or f"搜尋完成，找到 {len(self._found_boards)} 塊燈板。"
        self._publish()

    def _board_status(self, item):
        if not item.get("enabled", True): return "disabled"
        if not self._output_enabled: return "paused"
        if self._output_stopping: return "checking"
        if self.start_io and self.worker is None: return "connecting"
        session = getattr(self.worker, "session", None)
        health = getattr(session, "health", {}).get(item.get("ip"))
        if health is not None:
            return {"receiving": "connected", "sending": "sending",
                    "checking": "checking", "offline": "error",
                    "conflict": "conflict"}.get(health, "connecting")
        message = self._hardware_messages.get(item.get("ip"), "")
        if "另一" in message: return "conflict"
        if any(word in message for word in ("未成功", "中斷", "失敗", "不足", "拒絕")): return "error"
        if "燈板已回報收到即時燈光" in message: return "connected"
        if "正在送出" in message: return "sending"
        return "connecting" if self.start_io else "idle"

    def _start_output(self):
        if (self._closing or not self.start_io or not self._output_enabled or self.worker or
                self._output_stopping):
            return
        devices = [WledDevice(**{key: value for key, value in item.items() if key != "enabled"})
                   for item in self.settings.wled_devices if item.get("enabled", True)]
        if not devices:
            return
        session = WledSession(devices, self._hardware_status)
        self.worker = LatestFrameWorker(session, interval=1 / self.light_settings.renderer_fps)
        self.worker.start()
        self.worker.submit(self._last_frame)

    def _hardware_status(self, ip, message):
        self._events.hardware.emit(ip, message)

    def _on_hardware(self, ip, message):
        self._hardware_messages[ip] = message
        prefix = "已重新找到燈板："
        if message.startswith(prefix):
            new_ip = message[len(prefix):].strip()
            devices = [dict(item, ip=new_ip) if item.get("ip") == ip else item
                       for item in self.settings.wled_devices]
            if devices != self.settings.wled_devices:
                try:
                    self._save(replace(self.settings, wled_devices=devices))
                except Exception:  # nosec B110
                    # _save reports the failure; rediscovered IP persistence is best-effort.
                    pass
        self._publish()

    def _stop_output_then_restart(self):
        if self._output_stopping:
            return
        worker, self.worker = self.worker, None
        if not worker:
            if self._output_enabled:
                self._start_output()
            return
        self._output_stopping = True
        job = _Job(lambda _stop: (worker.stop(8), not worker.is_running)[1], self)
        self._keep_job(job, self._output_stopped)
        job.start()

    def _output_stopped(self, stopped, error):
        self._output_stopping = False
        if error or not stopped:
            self._messages["board"] = f"輸出尚未完全停止：{error or ''}"
        elif self._output_enabled:
            self._start_output()
        self._publish()

    def _microphone_access(self):
        # Qt's permission API requires a real application bundle on macOS.
        # Source runs rely on the launching Python/Terminal application's TCC
        # permission; never request it from PortAudio's background worker.
        if sys.platform != "darwin" or not getattr(sys, "frozen", False):
            return True
        app = QCoreApplication.instance()
        permission = QMicrophonePermission()
        status = app.checkPermission(permission)
        if status == Qt.PermissionStatus.Granted:
            return True
        if status == Qt.PermissionStatus.Undetermined:
            message = "請允許麥克風權限，才能偵測音量與語音指令"
            if not self._microphone_permission_pending:
                self._microphone_permission_pending = True
                app.requestPermission(permission, self, self._microphone_permission_finished)
        else:
            message = "麥克風權限已拒絕；請在系統設定 → 隱私權與安全性 → 麥克風允許本程式，然後重新啟動"
        self._microphone = {"ok": False, "message": message}
        self.controller.set_microphone_status(False, message)
        self._publish()
        return False

    def _microphone_permission_finished(self, _permission):
        self._microphone_permission_pending = False
        if not self._closing:
            self._start_audio()

    def _start_audio(self):
        if (self._closing or not self.start_io or self._audio_stopping or
                self.audio is not None or self._microphone_permission_pending):
            return
        if not self._microphone_access():
            return
        self._audio_generation += 1
        generation = self._audio_generation
        calibration = self.settings.calibration
        baseline = self.settings.noise_baseline_dbfs if (calibration.get("quality") == "PASS" and
                   calibration.get("device_id") == self.settings.microphone_device_id) else None
        self.audio = AudioRuntime(
            self.settings.microphone_device_id, self.resource_root / "models/sensevoice", self.settings,
            baseline, lambda: (self.controller.state.noise_enabled and
                               not self.playback.suppress_detection),
            lambda: self.controller.manual_revision,
            lambda component, ok, message: self._events.status.emit(generation, component, ok, message),
            lambda reading: self._events.noise.emit(generation, reading),
            lambda active: self._events.voice.emit(generation, active),
            lambda result, revision: self._events.command.emit(generation, result, revision),
            lambda baseline: self._events.calibrated.emit(generation, baseline),
            noise_context=lambda: self._noise_context)
        self._listening_enabled = None
        self._sync_voice_capture()
        self.audio.start()

    def _restart_audio(self):
        if not self.start_io or self._audio_stopping:
            return
        old, self.audio = self.audio, None
        self._listening_enabled = None
        self._audio_generation += 1
        if old is None:
            self._start_audio()
            return
        self._audio_stopping = self._busy["audio_stopping"] = True
        job = _Job(lambda _stop: (old.stop(2), not old.is_running)[1], self)
        self._keep_job(job, self._audio_stopped)
        job.start()

    def _audio_stopped(self, stopped, error):
        self._audio_stopping = self._busy["audio_stopping"] = False
        if error or not stopped:
            self._microphone = {"ok": False, "message": str(error or "舊工作尚未結束")}
        elif not self._closing:
            self._start_audio()
        self._publish()

    @Slot(int, str, bool, str)
    def _audio_status(self, generation, component, ok, message):
        if generation != self._audio_generation:
            return
        if component == "calibration":
            self._messages["noise"] = message
            if not ok:
                self._busy["calibration"] = False
            if "格式已變更" in message:
                try:
                    self._save(replace(self.settings, noise_baseline_dbfs=None,
                                       calibration={**self.settings.calibration, "quality": "UNKNOWN",
                                                    "baseline_dbfs": None, "sample_rate": None}))
                except Exception:  # nosec B110
                    # _save reports the failure; clearing a stale calibration baseline is best-effort.
                    pass
        elif component == "microphone":
            self._microphone = {"ok": bool(ok), "message": message}
            self.controller.set_microphone_status(ok, message)
        else:
            self._speech_ready = bool(ok)
            self._speech = {"ok": bool(ok and self.settings.voice_enabled), "message": message}
        self._publish()

    @Slot(int, object)
    def _noise_received(self, generation, reading):
        if (generation != self._audio_generation or reading is None or
                reading.context_revision != self._noise_context[1] or self.playback.suppress_detection):
            return
        self._reading = self._last_reading = reading
        health_ok = reading.health in (AudioHealth.OK, AudioHealth.CLIPPING)
        self._microphone = {"ok": health_ok, "message": reading.health.name}
        self.controller.set_microphone_status(health_ok, reading.health.name)
        if self.controller.state.base_mode in (BaseMode.NOTICE, BaseMode.DISCUSSION):
            self.controller.set_noise_state(reading.state)
        # Meter callbacks can arrive ten times per second. Publish one bounded
        # snapshot stream; pattern frames have their own lightweight channel.
        if time.monotonic() - self._last_periodic_publish >= .2:
            self._publish()

    @Slot(int, bool)
    def _voice_received(self, generation, active):
        if generation != self._audio_generation:
            return
        self._transcript["active"] = bool(active)
        self._publish()

    @Slot(int, object, int)
    def _command_received(self, generation, result, revision):
        if generation != self._audio_generation or self.playback.suppress_detection:
            return
        self._apply_command(result, revision)

    def _apply_command(self, result, revision):
        raw = getattr(result, "raw_text", getattr(result, "text", ""))
        corrected = getattr(result, "corrected_text", raw)
        applied = bool(result.intent and self.controller.submit_intent(result.intent, "voice", revision))
        status = "已套用" if applied else "已忽略（已手動切換）" if revision != self.controller.manual_revision else getattr(result, "reason", "指令未套用")
        self._transcript = {"raw": raw, "corrected": corrected, "status": status, "active": self._transcript["active"]}
        self._publish()

    @Slot(int, float)
    def _calibrated(self, generation, baseline):
        if generation != self._audio_generation:
            return
        report = getattr(getattr(self.audio, "noise", None), "calibration_result", None) or {}
        calibration = {**self.settings.calibration,
                       **{key: value for key, value in report.items() if key in self.settings.calibration},
                       "baseline_dbfs": baseline, "device_id": self.settings.microphone_device_id, "quality": "PASS"}
        try:
            self._save(replace(self.settings, noise_baseline_dbfs=baseline, calibration=calibration))
            self._messages["noise"] = f"基線 {baseline:.1f} dBFS"
        except Exception:
            self._messages["noise"] = "校準完成，但設定無法儲存。"
        self._busy["calibration"] = False
        self._publish()

    def _playback_status(self, status):
        self._events.playback_status.emit(str(status))

    @Slot(str)
    def _set_audio_message(self, status):
        self._messages["audio"] = status
        self._publish()

    def _tick(self):
        now = time.monotonic()
        self._sync_voice_capture()
        state = self.controller.state
        pattern = self.mapper.map(state)
        rest_remaining = self.controller.remaining_seconds("base") if state.base_mode is BaseMode.REST else None
        frame = self.blender.render(pattern, now - self.controller.pattern_started_at,
                                    self.light_settings, rest_remaining, now=now)
        playing_mode = self.playback.playing_mode
        if (playing_mode and self.playback.has_music_signal and
                self.settings.audio_reactive_modes.get(playing_mode, False)):
            frame = music_wave(frame, self.playback.music_level, now - self.controller.pattern_started_at)
        if frame:
            self._last_frame = bytes(frame)
            self.frameChanged.emit(base64.b64encode(frame).decode("ascii"))
            if self.worker:
                self.worker.submit(frame)
        if self._busy["calibration"]:
            duration = max(1, self.settings.calibration["duration_sec"])
            if now - self._calibration_started >= duration + 3:
                self._busy["calibration"] = False
        if now - self._last_periodic_publish >= 0.2:
            self._publish()

    def close(self):
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        self.playback.close()
        jobs = tuple(self._jobs)
        for job in jobs:
            job.cancel()
        audio, worker = self.audio, self.worker
        self.audio = self.worker = None
        if not self.start_io and not jobs:
            self.logger.close()
            self._shutdown_complete = True
            self.shutdownFinished.emit()
            return
        cleanup = _Job(lambda _stop: self._cleanup_io(audio, worker, jobs), self)
        self._cleanup_job = cleanup
        cleanup.finished.connect(self._cleanup_finished)
        cleanup.start()

    @Slot()
    def _cleanup_finished(self):
        self._shutdown_complete = True
        self.shutdownFinished.emit()
        self._cleanup_job = None

    def _cleanup_io(self, audio, worker, jobs):
        for job in jobs:
            # Wait off the UI thread until every child job has exited before
            # allowing its parent service to be destroyed.
            job.wait()
            if job.isRunning():
                raise RuntimeError("Background job did not stop during shutdown")
        if audio:
            audio.stop(2)
        if worker:
            worker.stop(8)
        self.logger.close()
        return True
