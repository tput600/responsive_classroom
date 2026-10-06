import tempfile
import unittest
import json
from dataclasses import asdict, replace
from pathlib import Path

from classroom_core import (
    BaseMode,
    ClassroomController,
    ClassroomState,
    Overlay,
    NoiseState,
    Pattern,
    PatternMapper,
    RestStage,
    Settings,
    SettingsRepository,
    SCHEMA_VERSION,
    DEFAULT_REST_AUDIO,
)


class ManualScheduler:
    def __init__(self):
        self.now = 0.0
        self.events = []

    def schedule(self, seconds, callback):
        event = [self.now + seconds, callback, False]
        self.events.append(event)

        def cancel():
            event[2] = True

        return cancel

    def advance(self, seconds):
        target = self.now + seconds
        while True:
            due = [event for event in self.events if not event[2] and event[0] <= target]
            if not due:
                break
            event = min(due, key=lambda item: item[0])
            event[2] = True
            self.now = event[0]
            event[1]()
        self.now = target


class ClassroomControllerTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualScheduler()
        self.controller = ClassroomController(
            Settings(rest_debug_seconds=7, rest_reminder_seconds=2, debug_rest=True,
                     voice_idle_seconds=6),
            self.clock.schedule, lambda: self.clock.now,
        )

    def test_question_expires_to_previous_base_mode_after_ten_seconds(self):
        self.controller.set_base_mode(BaseMode.NOTICE)
        self.controller.show_overlay(Overlay.QUESTION)
        self.clock.advance(9)
        self.assertEqual(self.controller.state, ClassroomState(
            BaseMode.NOTICE, Overlay.QUESTION, noise_enabled=True))
        self.clock.advance(1)
        self.assertEqual(self.controller.state.base_mode, BaseMode.NOTICE)
        self.assertIsNone(self.controller.state.overlay)

    def test_unchanged_microphone_readings_do_not_publish_duplicate_states(self):
        changes=[]
        self.controller.set_on_change(changes.append)
        self.controller.set_base_mode(BaseMode.NOTICE)
        self.controller.set_microphone_status(True)
        initial=len(changes)
        for _ in range(100):
            self.controller.set_microphone_status(True, "OK")
        self.assertEqual(len(changes),initial)
        self.controller.set_microphone_status(False,"disconnected")
        self.assertEqual(len(changes),initial+1)
        self.assertFalse(self.controller.state.noise_enabled)
        self.controller.set_microphone_status(True)
        self.assertEqual(len(changes),initial+2)
        self.assertTrue(self.controller.state.noise_enabled)

    def test_correct_and_wrong_are_five_second_overlays(self):
        for overlay in (Overlay.CORRECT, Overlay.WRONG):
            with self.subTest(overlay=overlay):
                self.controller.set_base_mode(BaseMode.NOTICE)
                self.controller.show_overlay(overlay)
                self.clock.advance(4)
                self.assertEqual(self.controller.state.overlay, overlay)
                self.clock.advance(1)
                self.assertEqual(self.controller.state.base_mode, BaseMode.NOTICE)
                self.assertIsNone(self.controller.state.overlay)

    def test_rest_has_a_reminder_then_returns_to_standby(self):
        self.controller.set_base_mode(BaseMode.REST)
        self.clock.advance(6)
        self.assertEqual(self.controller.state.base_mode, BaseMode.REST)
        self.clock.advance(1)
        self.assertEqual(self.controller.state.rest_stage, RestStage.REMINDER)
        self.clock.advance(1)
        self.assertEqual(self.controller.state.base_mode, BaseMode.REST)
        self.clock.advance(1)
        self.assertEqual(self.controller.state, ClassroomState(BaseMode.STANDBY))

    def test_rest_timer_continues_while_an_overlay_is_active(self):
        self.controller.set_base_mode(BaseMode.REST)
        self.clock.advance(4)
        self.controller.show_overlay(Overlay.QUESTION)
        self.clock.advance(3)
        self.assertEqual(self.controller.state.base_mode, BaseMode.REST)
        self.assertEqual(self.controller.state.rest_stage, RestStage.REMINDER)
        self.clock.advance(3)
        self.assertIsNone(self.controller.state.overlay)
        self.clock.advance(2)
        self.assertEqual(self.controller.state, ClassroomState(BaseMode.STANDBY))

    def test_manual_mode_change_cancels_overlay_and_its_timer(self):
        self.controller.show_overlay(Overlay.QUESTION)
        self.controller.set_base_mode(BaseMode.NOTICE)
        self.clock.advance(10)
        self.assertEqual(self.controller.state.base_mode, BaseMode.NOTICE)
        self.assertIsNone(self.controller.state.overlay)

    def test_mapper_returns_pattern_enum_for_overlay_then_base_mode(self):
        mapper = PatternMapper()
        self.assertEqual(mapper.map(ClassroomState()), Pattern.WARM_BREATH)
        self.assertEqual(mapper.map(ClassroomState(BaseMode.NOTICE)), Pattern.GREEN_BREATH)
        self.assertEqual(mapper.map(ClassroomState(BaseMode.DISCUSSION)), Pattern.DISCUSSION_GREEN_BREATH)
        self.assertEqual(
            mapper.map(ClassroomState(BaseMode.NOTICE, Overlay.CORRECT)), Pattern.CORRECT_SQUARE
        )

    def test_voice_idle_timeout_clears_rest_overlay_and_stale_results(self):
        revision = self.controller.manual_revision
        self.assertTrue(self.controller.submit_intent("REST", "voice", revision))
        self.clock.advance(1)
        self.assertTrue(self.controller.submit_intent("QUESTION", "voice", revision))
        self.clock.advance(5)
        self.assertEqual(self.controller.state.overlay, Overlay.QUESTION)
        self.clock.advance(1)
        self.assertEqual(self.controller.state.base_mode, BaseMode.STANDBY)
        self.assertIsNone(self.controller.state.overlay)
        self.assertFalse(self.controller.state.noise_enabled)
        self.assertIsNone(self.controller.remaining_seconds("voice"))
        self.assertFalse(self.controller.submit_intent("NOTICE", "voice", revision))
        self.clock.advance(30)
        self.assertEqual(self.controller.state.base_mode, BaseMode.STANDBY)

    def test_valid_voice_command_restarts_idle_timer(self):
        self.controller.update_settings(replace(self.controller.settings, debug_rest=False, rest_seconds=600))
        revision = self.controller.manual_revision
        self.controller.submit_intent("REST", "voice", revision)
        self.clock.advance(5)
        self.assertEqual(self.controller.remaining_seconds("voice"), 1)
        self.controller.submit_intent("QUESTION", "voice", revision)
        self.assertEqual(self.controller.remaining_seconds("voice"), 6)
        self.clock.advance(5)
        self.assertEqual(self.controller.state.overlay, Overlay.QUESTION)
        self.clock.advance(1)
        self.assertEqual(self.controller.state.base_mode, BaseMode.STANDBY)
        self.assertIsNone(self.controller.state.overlay)

    def test_notice_and_discussion_are_continuous_beyond_voice_idle(self):
        revision = self.controller.manual_revision
        for mode in ("NOTICE", "DISCUSSION"):
            self.assertTrue(self.controller.submit_intent(mode, "voice", revision))
            self.assertIsNone(self.controller.remaining_seconds("voice"))
            self.clock.advance(12)
            self.assertEqual(self.controller.state.base_mode, BaseMode[mode])
            self.assertIsNone(self.controller.state.overlay)

    def test_continuous_mode_overlay_survives_idle_and_returns_to_its_base(self):
        revision = self.controller.manual_revision
        self.controller.submit_intent("NOTICE", "voice", revision)
        self.controller.submit_intent("QUESTION", "voice", revision)
        self.assertIsNone(self.controller.remaining_seconds("voice"))
        self.clock.advance(6)
        self.assertEqual(self.controller.state.base_mode, BaseMode.NOTICE)
        self.assertEqual(self.controller.state.overlay, Overlay.QUESTION)
        self.clock.advance(4)
        self.assertEqual(self.controller.state.base_mode, BaseMode.NOTICE)
        self.assertIsNone(self.controller.state.overlay)

    def test_all_voice_overlays_expire_without_idle_resetting_continuous_bases(self):
        for mode in (BaseMode.NOTICE, BaseMode.DISCUSSION):
            for overlay in (Overlay.QUESTION, Overlay.CORRECT, Overlay.WRONG):
                with self.subTest(mode=mode, overlay=overlay):
                    clock = ManualScheduler()
                    controller = ClassroomController(
                        Settings(voice_idle_seconds=1, question_seconds=2, feedback_seconds=1),
                        clock.schedule, lambda: clock.now)
                    controller.set_base_mode(mode)
                    revision = controller.manual_revision
                    self.assertTrue(controller.submit_intent(overlay.name, "voice", revision))
                    self.assertIsNone(controller.remaining_seconds("voice"))
                    clock.advance(3)
                    self.assertEqual(controller.state.base_mode, mode)
                    self.assertIsNone(controller.state.overlay)

    def test_invalid_voice_returns_to_standby_and_cancels_all_timers(self):
        revision = self.controller.manual_revision
        self.controller.submit_intent("REST", "voice", revision)
        self.controller.submit_intent("WRONG", "voice", revision)
        before = self.controller.state
        timers = {name: self.controller.remaining_seconds(name) for name in ("base", "overlay", "voice")}
        self.assertFalse(self.controller.reject_voice_command("no exact command match", revision))
        self.assertEqual(self.controller.state, before)
        for name in ("base", "overlay", "voice"):
            self.assertEqual(self.controller.remaining_seconds(name), timers[name])
        self.assertEqual(self.controller.state.base_mode, BaseMode.REST)

    def test_cooldown_and_stale_invalid_voice_do_not_reset_a_valid_mode(self):
        revision = self.controller.manual_revision
        self.controller.submit_intent("QUESTION", "voice", revision)
        self.assertFalse(self.controller.reject_voice_command("command cooldown", revision))
        self.assertEqual(self.controller.state.overlay, Overlay.QUESTION)
        self.assertEqual(self.controller.remaining_seconds("voice"), 6)
        self.controller.set_base_mode(BaseMode.NOTICE)
        self.assertFalse(self.controller.reject_voice_command("no exact command match", revision))
        self.assertEqual(self.controller.state.base_mode, BaseMode.NOTICE)
        self.assertIsNone(self.controller.remaining_seconds("voice"))
        self.clock.advance(30)
        self.assertEqual(self.controller.state.base_mode, BaseMode.NOTICE)

    def test_manual_overlay_cancels_voice_idle_timer(self):
        self.controller.submit_intent("REST", "voice", self.controller.manual_revision)
        self.clock.advance(3)
        self.controller.show_overlay(Overlay.QUESTION)
        self.assertIsNone(self.controller.remaining_seconds("voice"))
        self.clock.advance(4)
        self.assertEqual(self.controller.state.base_mode, BaseMode.REST)
        self.assertEqual(self.controller.state.rest_stage, RestStage.REMINDER)

    def test_question_and_feedback_overlays_keep_noise_classification_live(self):
        self.controller.set_base_mode(BaseMode.NOTICE)
        self.controller.set_noise_state(NoiseState.RISING)
        self.controller.show_overlay(Overlay.QUESTION)
        self.controller.set_noise_state(NoiseState.LOUD)
        self.assertEqual(self.controller.state.noise_state, NoiseState.LOUD)
        self.controller.show_overlay(Overlay.CORRECT)
        self.controller.set_noise_state(NoiseState.QUIET)
        self.assertEqual(self.controller.state.noise_state, NoiseState.QUIET)

    def test_disabling_voice_cancels_idle_but_language_change_preserves_it(self):
        self.controller.update_settings(replace(self.controller.settings, voice_enabled=True))
        self.controller.submit_intent("REST", "voice", self.controller.manual_revision)
        self.clock.advance(2)
        self.controller.update_settings(replace(self.controller.settings, language="en_US"))
        self.assertEqual(self.controller.remaining_seconds("voice"), 4)
        self.controller.update_settings(replace(self.controller.settings, voice_enabled=False))
        self.assertIsNone(self.controller.remaining_seconds("voice"))
        self.clock.advance(30)
        self.assertEqual(self.controller.state.base_mode, BaseMode.STANDBY)


class SettingsTests(unittest.TestCase):
    def test_schema_thirteen_migrates_noise_defaults_with_backup_and_saves_current_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            data = {"schema_version": 13, **asdict(Settings(
                question_seconds=19, rest_seconds=777, noise_baseline_dbfs=-54,
                audio_files={**Settings().audio_files, "notice": "audio/notice/custom.wav"},
                speech_noise_guard=False,
            ))}
            data.update(noise_rising_db=20, noise_loud_db=36,
                        noise_rising_exit_db=12, noise_loud_exit_db=24)
            data["discussion_noise"] = {"rising_db": 32, "loud_db": 48,
                                         "rising_exit_db": 22, "loud_exit_db": 34}
            original = json.dumps(data, ensure_ascii=False)
            path.write_text(original, encoding="utf-8")

            loaded = SettingsRepository(path).load()

            self.assertEqual(path.with_suffix(".json.v13.bak").read_text(encoding="utf-8"), original)
            self.assertEqual((loaded.noise_rising_db, loaded.noise_loud_db,
                              loaded.noise_rising_exit_db, loaded.noise_loud_exit_db), (8, 15, 5, 14))
            self.assertEqual(loaded.discussion_noise,
                             {"rising_db": 12, "loud_db": 19,
                              "rising_exit_db": 9, "loud_exit_db": 17})
            self.assertEqual((loaded.question_seconds, loaded.rest_seconds), (19, 777))
            self.assertEqual(loaded.noise_baseline_dbfs, -54)
            self.assertEqual(loaded.audio_files["notice"], "audio/notice/custom.wav")
            self.assertFalse(loaded.speech_noise_guard)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], SCHEMA_VERSION)

    def test_schema_eleven_rest_default_preserves_custom_audio_and_clear_stays_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            for number, original_rest in enumerate(("", "audio/rest/custom.mp3")):
                path = Path(directory) / f"settings{number}.json"
                data = asdict(Settings(question_seconds=17))
                data["schema_version"] = 11
                data["audio_files"]["rest"] = original_rest
                original = json.dumps(data, ensure_ascii=False)
                path.write_text(original, encoding="utf-8")
                repository = SettingsRepository(path)
                loaded = repository.load()
                self.assertEqual(loaded.question_seconds, 17)
                self.assertEqual(loaded.audio_files["rest"], original_rest or DEFAULT_REST_AUDIO)
                self.assertEqual(path.with_suffix(".json.v11.bak").read_text(encoding="utf-8"), original)
                repository.save(replace(loaded, audio_files={**loaded.audio_files, "rest": ""}))
                self.assertEqual(repository.load().audio_files["rest"], "")

    def test_schema_ten_adds_discussion_audio_and_backs_up_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            old_colors = {key: value for key, value in Settings().colors.items()
                          if key in {"warm", "green", "orange", "red", "question", "correct", "wrong", "rest"}}
            data = {"schema_version": 10, **asdict(Settings(
                question_seconds=19, noise_rising_db=14))}
            data["colors"] = old_colors
            data.update(noise_loud_db=18, noise_rising_exit_db=5, noise_loud_exit_db=15,
                        noise_rising_enter_seconds=1.5, noise_loud_enter_seconds=2,
                        noise_exit_seconds=3, noise_smoothing_ms=750)
            data["discussion_noise"] = {"rising_db": 16, "loud_db": 26,
                                         "rising_exit_db": 13, "loud_exit_db": 23}
            data["audio_files"].pop("discussion")
            data.pop("speech_noise_guard")
            original = json.dumps(data, ensure_ascii=False)
            path.write_text(original, encoding="utf-8")
            settings = SettingsRepository(path).load()
            self.assertEqual(settings.question_seconds, 19)
            self.assertEqual(settings.noise_rising_db, 14)
            self.assertEqual(settings.noise_loud_db, 36)
            self.assertEqual(settings.discussion_noise["rising_db"], Settings().discussion_noise["rising_db"])
            self.assertEqual(settings.audio_files["discussion"], "")
            self.assertTrue(settings.speech_noise_guard)
            self.assertEqual(len(settings.colors), 12)
            self.assertEqual(path.with_suffix(".json.v10.bak").read_text(encoding="utf-8"), original)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], SCHEMA_VERSION)

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SettingsRepository(Path(directory) / "settings.json")
            expected = Settings(question_seconds=10, feedback_seconds=5, rest_debug_seconds=23)
            repository.save(expected)
            self.assertEqual(repository.load(), expected)

    def test_missing_settings_use_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SettingsRepository(Path(directory) / "missing.json")
            self.assertEqual(repository.load(), Settings())

    def test_settings_reject_invalid_timer_values(self):
        with self.assertRaises(ValueError):
            Settings(rest_debug_seconds=0)
        for value in (86401, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings(voice_idle_seconds=value)
        self.assertEqual(Settings(voice_idle_seconds=0).voice_idle_seconds, 0)

    def test_audio_health_and_calibration_defaults_validate(self):
        from classroom_core import AudioHealth
        self.assertEqual({state.name for state in AudioHealth},
                         {"OK", "SILENT", "CLIPPING", "DISCONNECTED", "UNCALIBRATED"})
        self.assertEqual(Settings().calibration["duration_sec"], 12)
        with self.assertRaises(ValueError):
            Settings(mode_change_grace_ms=3001)
        with self.assertRaises(ValueError):
            Settings(calibration={**Settings().calibration, "max_speech_ratio": 1.1})

    def test_schema_five_adds_idle_timer_without_losing_user_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = {"schema_version": 5, **asdict(Settings(
                question_seconds=17, language="en_US", command_prefix="teacher"))}
            original["noise_loud_db"] = 18.0
            original.pop("voice_idle_seconds")
            raw = json.dumps(original, ensure_ascii=False)
            path.write_text(raw, encoding="utf-8")
            settings = SettingsRepository(path).load()
            self.assertEqual(settings.voice_idle_seconds, 0)
            self.assertEqual(settings.question_seconds, 17)
            self.assertEqual(settings.language, "en_US")
            self.assertEqual(settings.command_prefix, "teacher")
            self.assertEqual(path.with_suffix(".json.v5.bak").read_text(encoding="utf-8"), raw)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], SCHEMA_VERSION)

    def test_schema_six_adds_short_notice_to_defaults_and_preserves_custom_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for aliases, expected in (
                (["notice", "attention", "請注意老師", "请注意老师", "class class class"],
                 Settings().command_aliases["NOTICE"]),
                (["大家看這裡"], ["大家看這裡"]),
            ):
                with self.subTest(aliases=aliases):
                    data = {"schema_version": 6, **asdict(Settings(voice_idle_seconds=42))}
                    data["noise_loud_db"] = 18.0
                    data["command_aliases"]["NOTICE"] = aliases
                    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    loaded = SettingsRepository(path).load()
                    self.assertEqual(loaded.command_aliases["NOTICE"], expected)
                    self.assertEqual(loaded.voice_idle_seconds, 42)
                    self.assertTrue(path.with_suffix(".json.v6.bak").is_file())

    def test_schema_eight_migration_adds_new_fields_preserving_user_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            previous = asdict(Settings(question_seconds=19, rest_seconds=777, voice_idle_seconds=41,
                                       command_prefix="老師", noise_baseline_dbfs=-54))
            previous["noise_loud_db"] = 18.0
            previous.pop("mode_change_grace_ms")
            previous.pop("calibration")
            previous["wled_devices"] = [{"ip": "192.0.2.20", "mac": "aa:bb", "name": "Panel"}]
            original = {"schema_version": 8, **previous}
            raw = json.dumps(original, ensure_ascii=False)
            path.write_text(raw, encoding="utf-8")
            loaded = SettingsRepository(path).load()
            self.assertEqual((loaded.question_seconds, loaded.rest_seconds, loaded.voice_idle_seconds), (19, 777, 41))
            self.assertEqual((loaded.command_prefix, loaded.noise_baseline_dbfs), ("老師", -54))
            self.assertEqual(loaded.wled_devices[0]["enabled"], True)
            self.assertEqual(loaded.mode_change_grace_ms, 0)
            self.assertEqual(loaded.calibration["quality"], "UNKNOWN")
            self.assertEqual(path.with_suffix(".json.v8.bak").read_text(encoding="utf-8"), raw)


if __name__ == "__main__":
    unittest.main()
