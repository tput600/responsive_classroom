import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import classroom_app
from PySide6.QtWidgets import QApplication

from classroom_audio import CommandResult
from classroom_core import SettingsRepository
from classroom_service import ClassroomService


APP = QApplication.instance() or QApplication([])


def service(directory):
    path = __import__("pathlib").Path(directory)
    return ClassroomService(SettingsRepository(path / "settings.json"), path,
                            start_io=False)


def call(service, action, payload):
    return json.loads(service.command(action, json.dumps(payload)))


def finish(service):
    service.close()
    deadline = time.monotonic() + 5
    while not service._shutdown_complete and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(0.005)
    APP.processEvents()
    if not service._shutdown_complete:
        raise TimeoutError("service shutdown did not complete")


class WebServiceTests(unittest.TestCase):
    def test_combined_input_save_is_atomic_and_writes_both_forms_once(self):
        from unittest.mock import patch
        from classroom_service import NOISE_KEYS
        subject = service(self.directory)
        original = subject.settings
        aliases = {key: list(values) for key, values in original.command_aliases.items()}
        aliases['STANDBY'].append('ready now')
        payload = {key: getattr(original, key) for key in NOISE_KEYS}
        payload.update(noise_rising_db=9,
                       aliases=aliases, corrections=original.command_corrections)
        conflicting = {key: list(values) for key, values in aliases.items()}
        conflicting['WRONG'].append('question')
        with patch.object(subject.repository, 'save', wraps=subject.repository.save) as save:
            self.assertFalse(call(subject, 'save_input_settings',
                                  {**payload, 'aliases': conflicting})['ok'])
            self.assertEqual(subject.settings, original)
            save.assert_not_called()
            self.assertTrue(call(subject, 'save_input_settings', payload)['ok'])
            save.assert_called_once()
        loaded = subject.repository.load()
        self.assertEqual(loaded.noise_rising_db, 9)
        self.assertIn('ready now', loaded.command_aliases['STANDBY'])
        self.assertEqual(loaded.audio_files, original.audio_files)
        finish(subject)

    def test_removed_speech_noise_guard_setting_is_ignored(self):
        subject = service(self.directory)
        names = ("noise_rising_db", "noise_loud_db", "noise_rising_enter_seconds",
                 "noise_loud_enter_seconds", "noise_rising_exit_db", "noise_loud_exit_db",
                 "noise_exit_seconds", "noise_smoothing_ms", "discussion_noise")
        payload = {name: getattr(subject.settings, name) for name in names}
        self.assertTrue(call(subject,"save_noise",{**payload,"speech_noise_guard":False})["ok"])
        self.assertTrue(subject.repository.load().speech_noise_guard)
        self.assertTrue(call(subject,"save_noise",{**payload,"speech_noise_guard":"false"})["ok"])
        finish(subject)

    def test_late_context_is_rejected_but_measurable_clipping_is_not_masked(self):
        from classroom_audio import NoiseReading
        from classroom_core import AudioHealth, NoiseState
        subject = service(self.directory)
        call(subject,"mode",{"mode":"NOTICE"})
        revision=subject._noise_context[1]
        call(subject,"mode",{"mode":"DISCUSSION"})
        subject._noise_received(subject._audio_generation,NoiseReading(-30,-30,30,NoiseState.LOUD,False,0,None,context_revision=revision))
        self.assertEqual(subject.controller.state.noise_state,NoiseState.UNKNOWN)
        subject._noise_received(subject._audio_generation,NoiseReading(-2,-2,58,NoiseState.LOUD,True,0,None,context_revision=subject._noise_context[1],health=AudioHealth.CLIPPING))
        self.assertEqual(subject.controller.state.noise_state,NoiseState.LOUD)
        self.assertEqual(subject.snapshot()['noise']['health'],'CLIPPING')
        self.assertEqual(subject.controller.state.base_mode.name,"DISCUSSION")
        finish(subject)

    def test_language_preserves_current_mode_timers_and_aliases(self):
        subject=service(self.directory)
        call(subject,"mode",{"mode":"NOTICE"})
        before=subject.settings
        self.assertTrue(call(subject,"language",{"language":"en_US"})["ok"])
        self.assertEqual(subject.controller.state.base_mode.name,"NOTICE")
        self.assertEqual(subject.settings.question_seconds,before.question_seconds)
        self.assertEqual(subject.settings.command_aliases,before.command_aliases)
        self.assertIn("available",json.loads(subject.snapshot_json())["statuses"]["microphone"])
        finish(subject)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = self.temp.name

    def test_board_health_is_typed_and_never_depends_on_translated_sentence(self):
        from types import SimpleNamespace
        subject = service(self.directory)
        board = {'ip':'192.0.2.10','enabled':True}
        subject._output_enabled = True
        subject.worker = SimpleNamespace(session=SimpleNamespace(health={board['ip']:'receiving'}))
        self.assertEqual(subject._board_status(board),'connected')
        for health, status in [('sending','sending'),('checking','checking'),('offline','error')]:
            subject.worker.session.health[board['ip']] = health
            self.assertEqual(subject._board_status(board),status)
        self.assertEqual(subject._board_status({**board,'enabled':False}),'disabled')
        subject._output_enabled = False
        self.assertEqual(subject._board_status(board),'paused')
        subject.worker = None
        finish(subject)

    def test_background_search_gets_cancellable_event_instead_of_callable(self):
        from classroom_service import _Job
        captured = []
        job = _Job(lambda stop: captured.append(stop.is_set()))
        job.run()
        job.cancel()
        job.run()
        self.assertEqual(captured,[False,True])

    def test_close_cancels_import_and_waits_until_job_finishes(self):
        import threading

        subject = service(self.directory)
        started = threading.Event()
        release = threading.Event()
        original_audio_files = dict(subject.settings.audio_files)

        def blocked_import(_mode, _path, cancellation):
            started.set()
            release.wait(2)
            if cancellation.is_set():
                raise InterruptedError("Audio import cancelled")
            return "audio/discussion/cue.wav"

        subject.playback.import_file = blocked_import
        subject.import_audio("discussion", self.directory + "/cue.wav")
        deadline = time.monotonic() + 2
        while not started.is_set() and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.005)
        self.assertTrue(started.is_set())

        before_close = time.monotonic()
        subject.close()
        self.assertLess(time.monotonic() - before_close, 0.5)
        self.assertFalse(subject._shutdown_complete)
        self.assertTrue(next(iter(subject._jobs))._stop_requested.is_set())

        release.set()
        deadline = time.monotonic() + 3
        while not subject._shutdown_complete and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.005)
        APP.processEvents()
        self.assertTrue(subject._shutdown_complete)
        self.assertEqual(subject.settings.audio_files, original_audio_files)

    def test_music_response_switches_persist_independently_without_changing_class_mode(self):
        subject = service(self.directory)
        call(subject,'mode',{'mode':'DISCUSSION'})
        self.assertTrue(call(subject,'audio_reactive',{'mode':'rest','enabled':True})['ok'])
        self.assertTrue(call(subject,'audio_reactive',{'mode':'notice','enabled':True})['ok'])
        settings=subject.repository.load()
        self.assertTrue(settings.audio_reactive_modes['rest'])
        self.assertTrue(settings.audio_reactive_modes['notice'])
        self.assertFalse(settings.audio_reactive_modes['discussion'])
        self.assertEqual(subject.controller.state.base_mode.name,'DISCUSSION')
        self.assertFalse(call(subject,'audio_reactive',{'mode':'rest','enabled':'false'})['ok'])
        finish(subject)

    def tearDown(self):
        self.temp.cleanup()

    def test_modes_save_and_restore_without_io(self):
        first = service(self.directory)
        self.assertEqual(call(first, "mode", {"mode": "notice"}), {"ok": True})
        self.assertEqual(first.snapshot()["current"]["base_mode"], "NOTICE")
        self.assertEqual(call(first, "mode", {"mode": "discussion"}), {"ok": True})
        self.assertEqual(first.snapshot()["current"]["base_mode"], "DISCUSSION")
        settings = first.snapshot()["settings"]
        discussion = {"rising_db": settings["discussion_noise"]["rising_db"] + 1,
                      "loud_db": settings["discussion_noise"]["loud_db"] + 1,
                      "rising_exit_db": settings["discussion_noise"]["rising_exit_db"],
                      "loud_exit_db": settings["discussion_noise"]["loud_exit_db"]}
        payload = {key: settings[key] for key in (
            "noise_rising_db", "noise_loud_db", "noise_rising_enter_seconds",
            "noise_loud_enter_seconds", "noise_rising_exit_db", "noise_loud_exit_db",
            "noise_exit_seconds", "noise_smoothing_ms")}
        payload["discussion_noise"] = discussion
        self.assertEqual(call(first, "save_noise", payload), {"ok": True})
        finish(first)

        second = service(self.directory)
        self.assertEqual(second.snapshot()["settings"]["language"], "zh_TW")
        self.assertEqual(second.snapshot()["current"]["base_mode"], "STANDBY")
        self.assertEqual(second.settings.discussion_noise, discussion)
        finish(second)

    def test_invalid_settings_retain_last_good_revision_and_voice_is_validated(self):
        subject = service(self.directory)
        self.assertEqual(call(subject, "save_timers", {"question_seconds": 12, "feedback_seconds": 7,
                                         "rest_seconds": 500, "rest_reminder_seconds": 20,
                                         "voice_idle_seconds": 30}), {"ok": True})
        saved = subject.snapshot()
        invalid = call(subject, "save_timers", {**{key: saved["settings"][key] for key in (
        "question_seconds", "feedback_seconds", "rest_seconds", "rest_reminder_seconds",
        "voice_idle_seconds")}, "question_seconds": 0})
        self.assertFalse(invalid["ok"])
        self.assertEqual(subject.snapshot()["settings_revision"], saved["settings_revision"])
        self.assertEqual(subject.snapshot()["settings"]["question_seconds"], 12)

        aliases = dict(saved["settings"]["command_aliases"])
        aliases["STANDBY"] = ["standby", "quiet room"]
        self.assertEqual(call(subject, "save_voice", {"aliases": aliases,
                         "corrections": saved["settings"]["command_corrections"]}), {"ok": True})
        self.assertEqual(subject.settings.command_aliases["STANDBY"], ["standby", "quiet room"])
        from classroom_resources import VERSION
        self.assertEqual(json.loads(subject.snapshot_json())["version"], VERSION)
        finish(subject)

    def test_failed_repository_write_returns_command_error(self):
        subject = service(self.directory)
        subject.repository.save = lambda _settings: (_ for _ in ()).throw(OSError("read only"))
        result = call(subject, "language", {"language": "en_US"})
        self.assertFalse(result["ok"])
        self.assertEqual(subject.settings.language, "zh_TW")
        finish(subject)

    def test_stale_voice_command_cannot_override_manual_mode(self):
        subject = service(self.directory)
        revision = subject.controller.manual_revision
        self.assertEqual(call(subject, "mode", {"mode": "discussion"}), {"ok": True})
        subject._apply_command(CommandResult("class notice", "NOTICE", "accepted", "class notice"), revision)
        self.assertEqual(subject.snapshot()["current"]["base_mode"], "DISCUSSION")
        self.assertEqual(subject.snapshot()["transcript"]["status"], "已忽略（已手動切換）")
        finish(subject)

    def test_scenario_switches_and_voice_wake_only_mode(self):
        from classroom_audio import CommandParser
        subject = service(self.directory)
        parser = CommandParser(subject.settings, cooldown_seconds=0)
        self.assertEqual(call(subject, "voice", {"enabled": True}), {"ok": True})
        subject._apply_command(parser.parse("no sensor"), subject.controller.manual_revision)
        self.assertTrue(subject.snapshot()["statuses"]["speech"]["command_paused"])
        subject._apply_command(parser.parse("notice"), subject.controller.manual_revision)
        self.assertEqual(subject.snapshot()["current"]["base_mode"], "STANDBY")
        subject._apply_command(parser.parse("sensor on"), subject.controller.manual_revision)
        self.assertFalse(subject.snapshot()["statuses"]["speech"]["command_paused"])
        self.assertEqual(call(subject, "scenario_voice", {"mode": "notice", "enabled": False}), {"ok": True})
        subject._apply_command(parser.parse("notice"), subject.controller.manual_revision)
        self.assertEqual(subject.snapshot()["current"]["base_mode"], "STANDBY")
        subject._apply_command(parser.parse("discussion"), subject.controller.manual_revision)
        self.assertEqual(subject.snapshot()["current"]["base_mode"], "DISCUSSION")
        self.assertEqual(call(subject, "scenario_audio", {"mode": "discussion", "enabled": False}), {"ok": True})
        self.assertFalse(subject.settings.audio_enabled_modes["discussion"])
        self.assertFalse(subject.repository.load().voice_mode_enabled["notice"])
        self.assertFalse(subject.repository.load().audio_enabled_modes["discussion"])
        finish(subject)

    def test_optional_phone_buttons_take_over_only_base_voice_commands(self):
        subject = service(self.directory)
        subject.resource_root = Path(__file__).parents[1] / "resources"
        with patch("classroom_service.local_networks", return_value=[("LAN", "127.0.0.1", "127.0.0.0/8")]):
            self.assertEqual(call(subject, "remote", {"enabled": True}), {"ok": True})
        try:
            url = subject.snapshot()["remote"]["urls"][0].split("#")[0]
            token = subject.remote.token
            with urllib.request.urlopen(url, timeout=2) as response:
                self.assertIn("responsive classroom", response.read().decode("utf-8"))
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(url + "state", timeout=2)
            request = urllib.request.Request(url + "state", headers={"X-Remote-Token": token})
            with urllib.request.urlopen(request, timeout=2) as response:
                state = json.load(response)
                self.assertEqual(state["mode"], "STANDBY")
                self.assertFalse(state["active"])
            self.assertTrue(subject.remote.connected)
            subject._tick()
            subject._apply_command(CommandResult("notice", "NOTICE", "accepted", "notice"),
                                   subject.controller.manual_revision)
            self.assertEqual(subject.snapshot()["current"]["base_mode"], "NOTICE")
            request = urllib.request.Request(url + "mode", data=b'{"mode":"STANDBY"}',
                                             headers={"X-Remote-Token": token, "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as paused:
                urllib.request.urlopen(request, timeout=2)
            self.assertEqual(paused.exception.code, 409)
            self.assertEqual(call(subject, "remote_active", {"enabled": True}), {"ok": True})
            self.assertTrue(subject.snapshot()["remote"]["active"])
            subject._apply_command(CommandResult("standby", "STANDBY", "accepted", "standby"),
                                   subject.controller.manual_revision)
            self.assertEqual(subject.snapshot()["current"]["base_mode"], "NOTICE")
            subject._apply_command(CommandResult("question", "QUESTION", "accepted", "question"),
                                   subject.controller.manual_revision)
            self.assertEqual(subject.snapshot()["current"]["overlay"], "QUESTION")
            self.assertIsNone(subject.controller.remaining_seconds("voice"))
            request = urllib.request.Request(url + "mode", data=b'{"mode":"NOTICE"}',
                                             headers={"X-Remote-Token": token, "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertTrue(json.load(response)["ok"])
            deadline = time.monotonic() + 2
            while subject.snapshot()["current"]["base_mode"] != "NOTICE" and time.monotonic() < deadline:
                APP.processEvents()
                time.sleep(0.005)
            self.assertEqual(subject.snapshot()["current"]["base_mode"], "NOTICE")
            self.assertEqual(call(subject, "remote_active", {"enabled": False}), {"ok": True})
            self.assertTrue(subject.snapshot()["remote"]["connected"])
            self.assertFalse(subject.snapshot()["remote"]["active"])
            with self.assertRaises(urllib.error.HTTPError) as paused:
                urllib.request.urlopen(request, timeout=2)
            self.assertEqual(paused.exception.code, 409)
            subject._apply_command(CommandResult("standby", "STANDBY", "accepted", "standby"),
                                   subject.controller.manual_revision)
            self.assertEqual(subject.snapshot()["current"]["base_mode"], "STANDBY")
            self.assertEqual(call(subject, "remote_active", {"enabled": True}), {"ok": True})
            with subject.remote._lock:
                subject.remote._last_seen = time.monotonic() - 7
            subject._tick()
            self.assertFalse(subject.snapshot()["remote"]["connected"])
            subject._apply_command(CommandResult("discussion", "DISCUSSION", "accepted", "discussion"),
                                   subject.controller.manual_revision)
            self.assertEqual(subject.snapshot()["current"]["base_mode"], "DISCUSSION")
        finally:
            call(subject, "remote", {"enabled": False})
            finish(subject)

    def test_phone_voice_permission_switches_and_noise_continuity(self):
        subject = service(self.directory)
        subject.resource_root = Path(__file__).parents[1] / "resources"
        with patch("classroom_service.local_networks", return_value=[("LAN", "127.0.0.1", "127.0.0.0/8")]):
            self.assertEqual(call(subject, "remote", {"enabled": True}), {"ok": True})
        try:
            remote = subject.snapshot()["remote"]
            self.assertTrue(remote["qrs"][0].startswith("data:image/png;base64,"))
            self.assertFalse(remote["allow_voice"])
            self.assertFalse(any(remote["voice_modes"].values()))
            url = remote["urls"][0].split("#")[0]
            headers = {"X-Remote-Token": subject.remote.token, "Content-Type": "application/json"}
            with urllib.request.urlopen(urllib.request.Request(url + "state", headers=headers), timeout=2):
                pass
            self.assertEqual(call(subject, "remote_active", {"enabled": True}), {"ok": True})
            marker, context = object(), subject._noise_context
            subject._reading = marker
            voice = urllib.request.Request(url + "switch", data=b'{"kind":"voice","mode":"NOTICE","enabled":true}', headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as blocked:
                urllib.request.urlopen(voice, timeout=2)
            self.assertEqual(blocked.exception.code, 409)
            self.assertEqual(call(subject, "remote_voice_permission", {"enabled": True}), {"ok": True})
            with urllib.request.urlopen(voice, timeout=2) as response:
                self.assertTrue(json.load(response)["ok"])
            deadline = time.monotonic() + 2
            while not subject.snapshot()["remote"]["voice_modes"]["notice"] and time.monotonic() < deadline:
                APP.processEvents()
                time.sleep(.005)
            self.assertTrue(subject.snapshot()["remote"]["voice_modes"]["notice"])
            self.assertIs(subject._reading, marker)
            self.assertEqual(subject._noise_context, context)
            subject._apply_command(CommandResult("notice", "NOTICE", "accepted", "notice"),
                                   subject.controller.manual_revision)
            self.assertEqual(subject.snapshot()["current"]["base_mode"], "NOTICE")
            self.assertEqual(call(subject, "remote_voice_permission", {"enabled": False}), {"ok": True})
            self.assertFalse(any(subject.snapshot()["remote"]["voice_modes"].values()))
            marker, context = object(), subject._noise_context
            subject._reading = marker
            audio = urllib.request.Request(url + "switch", data=b'{"kind":"audio","mode":"NOTICE","enabled":false}', headers=headers)
            with urllib.request.urlopen(audio, timeout=2) as response:
                self.assertTrue(json.load(response)["ok"])
            deadline = time.monotonic() + 2
            while subject.settings.audio_enabled_modes["notice"] and time.monotonic() < deadline:
                APP.processEvents()
                time.sleep(.005)
            self.assertFalse(subject.settings.audio_enabled_modes["notice"])
            self.assertIs(subject._reading, marker)
            self.assertEqual(subject._noise_context, context)
        finally:
            call(subject, "remote", {"enabled": False})
            finish(subject)

    def test_board_enable_is_saved_independently_and_stop_runs_off_gui_thread(self):
        subject = service(self.directory)
        board = {"ip": "192.168.1.20", "name": "board", "mac": "aa:bb:cc:dd:ee:ff",
             "version": "1", "led_count": 64, "rotation": 0, "mirror_x": False,
             "mirror_y": False, "serpentine": False, "enabled": True}
        subject._save(__import__("dataclasses").replace(subject.settings, wled_devices=[board]))
        gui_thread = threading.get_ident()
        entered = threading.Event()
        release = threading.Event()

        class Worker:
            is_running = True
            stop_thread = None

            def stop(self, timeout):
                self.stop_thread = threading.get_ident()
                entered.set()
                if not release.wait(timeout):
                    raise TimeoutError("test did not release the output worker")
                self.is_running = False

        worker = subject.worker = Worker()
        try:
            result = call(subject, "enable_board", {"mac": board["mac"], "enabled": False})
            self.assertEqual(result, {"ok": True})
            self.assertTrue(entered.wait(2), "background stop did not start")
            self.assertNotEqual(worker.stop_thread, gui_thread)
            # The action returns while stop() is still waiting. This checks
            # nonblocking cleanup without a disk/runner-dependent time limit.
            self.assertTrue(worker.is_running)
            self.assertTrue(subject._output_stopping)
            self.assertEqual(subject.controller.state.base_mode.name, "STANDBY")
            self.assertFalse(subject.settings.wled_devices[0]["enabled"])
        finally:
            release.set()
        deadline = time.monotonic() + 2
        while subject._output_stopping and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.005)
        self.assertFalse(subject._output_stopping)
        self.assertFalse(subject.worker)
        self.assertFalse(subject.settings.wled_devices[0]["enabled"])
        finish(subject)


if __name__ == "__main__":
    unittest.main()
