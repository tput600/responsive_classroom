import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import numpy as np

from classroom_audio import CommandParser, NoiseAnalyzer
from classroom_core import (
    BaseMode, ClassroomController, NoiseState, Overlay, Pattern,
    PatternMapper, Settings, SettingsRepository,
)


class ContextProfileTests(unittest.TestCase):
    @staticmethod
    def tone(relative_db=20):
        amplitude = np.sqrt(2) * 10 ** ((relative_db - 60) / 20)
        return amplitude * np.sin(np.arange(1600) * 2 * np.pi * 440 / 16000)

    def test_discussion_context_mapping_and_question_overlay_restore(self):
        scheduled = []
        controller = ClassroomController(
            Settings(), lambda _seconds, callback: (scheduled.append(callback) or (lambda: None)))
        controller.set_base_mode(BaseMode.DISCUSSION)
        self.assertTrue(controller.state.noise_enabled)
        self.assertEqual(PatternMapper().map(controller.state), Pattern.DISCUSSION_GREEN_BREATH)
        controller.show_overlay(Overlay.QUESTION)
        self.assertEqual(PatternMapper().map(controller.state), Pattern.QUESTION_GREEN_ROTATE)
        scheduled[-1]()
        self.assertEqual(controller.state.base_mode, BaseMode.DISCUSSION)

    def test_same_relative_level_is_loud_in_notice_and_rising_in_discussion(self):
        settings = Settings(speech_noise_guard=False)
        self.assertEqual((settings.noise_rising_db, settings.noise_loud_db,
                          settings.noise_rising_exit_db, settings.noise_loud_exit_db),
                         (8, 18, 5, 14))
        self.assertEqual(settings.discussion_noise,
                         {"rising_db": 12, "loud_db": 22,
                          "rising_exit_db": 9, "loud_exit_db": 17})
        self.assertEqual((settings.noise_rising_enter_seconds,
                          settings.noise_loud_enter_seconds,
                          settings.noise_exit_seconds, settings.noise_smoothing_ms),
                         (1.5, 2.0, 3.0, 750))
        analyzer = NoiseAnalyzer(settings, baseline_dbfs=-60)
        tone = self.tone()
        for now in np.arange(0, 2.2, .1):
            reading = analyzer.feed(tone, now=now, mode=BaseMode.NOTICE)
        self.assertEqual(reading.state, NoiseState.LOUD)
        reading = analyzer.feed(tone, now=3, mode=BaseMode.DISCUSSION)
        self.assertEqual(reading.state, NoiseState.UNKNOWN)
        for now in np.arange(3.1, 5.6, .1):
            reading = analyzer.feed(tone, now=float(now), mode=BaseMode.DISCUSSION)
        self.assertEqual(reading.state, NoiseState.RISING)
        self.assertAlmostEqual(reading.relative_db, 20, delta=0.1)

    def test_discussion_loud_threshold_and_exit_hysteresis_use_new_profile(self):
        settings = Settings(speech_noise_guard=False)
        analyzer = NoiseAnalyzer(settings, baseline_dbfs=-60)
        for now in np.arange(0, 3.1, .1):
            reading = analyzer.feed(self.tone(24), now=float(now), mode=BaseMode.DISCUSSION)
        self.assertEqual(reading.state, NoiseState.LOUD)

        for now in np.arange(3.1, 4.1, .1):
            reading = analyzer.feed(self.tone(18), now=float(now), mode=BaseMode.DISCUSSION)
        self.assertEqual(reading.state, NoiseState.LOUD)

        for now in np.arange(4.1, 8.0, .1):
            reading = analyzer.feed(self.tone(16), now=float(now), mode=BaseMode.DISCUSSION)
        self.assertEqual(reading.state, NoiseState.RISING)

    def test_context_switch_discards_pending_candidate_but_keeps_baseline_and_smoothing(self):
        settings = Settings(speech_noise_guard=False, noise_rising_db=8, noise_loud_db=18,
                            noise_rising_exit_db=5, noise_loud_exit_db=15,
                            noise_loud_enter_seconds=3,
                            discussion_noise={"rising_db": 16, "loud_db": 26,
                                              "rising_exit_db": 13, "loud_exit_db": 23})
        analyzer = NoiseAnalyzer(settings, baseline_dbfs=-60)
        tone = self.tone()
        analyzer.feed(tone, now=0, mode=BaseMode.NOTICE)
        analyzer.feed(tone, now=0.1, mode=BaseMode.NOTICE)
        self.assertEqual(analyzer._candidate, NoiseState.LOUD)
        smooth = list(analyzer._smooth)
        reading = analyzer.feed(tone, now=0.2, mode=BaseMode.DISCUSSION)
        self.assertIsNone(analyzer._candidate)
        self.assertEqual(reading.state, NoiseState.UNKNOWN)
        self.assertEqual(analyzer.baseline_dbfs, -60)
        self.assertEqual(list(analyzer._smooth), smooth + [(0.2, reading.dbfs)])
        self.assertAlmostEqual(reading.raw_rms, np.sqrt(np.mean((tone - tone.mean()) ** 2)), places=8)
        self.assertEqual(reading.context_revision, 0)
        for now in np.arange(.3, 1.2, .1):
            analyzer.feed(tone, now=float(now), mode=BaseMode.DISCUSSION)
        self.assertEqual(analyzer._candidate, NoiseState.RISING)
        self.assertGreaterEqual(analyzer._candidate_since, 1.0)

    def test_v7_migration_preserves_custom_settings_and_aliases(self):
        settings = Settings(command_aliases={
            **Settings().command_aliases,
            "NOTICE": ["老師請注意"],
        }, command_prefix="老師", voice_idle_seconds=47, noise_baseline_dbfs=-53)
        payload = {"schema_version": 7, **asdict(settings)}
        payload.pop("discussion_noise")
        payload["command_aliases"].pop("DISCUSSION")
        payload["command_corrections"]["custom phrase"] = "notice"
        payload["command_corrections"]["not is"] = "question"
        payload["command_corrections"].pop("not it is")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = json.dumps(payload, ensure_ascii=False)
            path.write_text(original, encoding="utf-8")
            migrated = SettingsRepository(path).load()
            self.assertEqual(path.with_suffix(".json.v7.bak").read_text(encoding="utf-8"), original)
            self.assertEqual(migrated.command_aliases["NOTICE"], ["老師請注意"])
            self.assertEqual(migrated.command_aliases["DISCUSSION"], ["discussion", "討論", "讨论"])
            self.assertEqual(migrated.command_prefix, "老師")
            self.assertEqual(migrated.voice_idle_seconds, 47)
            self.assertEqual(migrated.noise_baseline_dbfs, -53)
            self.assertEqual(migrated.discussion_noise["rising_db"], Settings().discussion_noise["rising_db"])
            self.assertEqual(migrated.command_corrections["custom phrase"], "notice")
            self.assertEqual(migrated.command_corrections["not is"], "question")
            self.assertEqual(migrated.command_corrections["not it is"], "notice")

    def test_v7_migration_does_not_expand_legacy_notice_aliases(self):
        payload = {"schema_version": 7, **asdict(Settings())}
        payload.pop("discussion_noise")
        payload["command_aliases"].pop("DISCUSSION")
        old_notice = ["notice", "attention", "請注意老師", "请注意老师", "class class class"]
        payload["command_aliases"]["NOTICE"] = old_notice
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            migrated = SettingsRepository(path).load()
        self.assertEqual(migrated.command_aliases["NOTICE"], old_notice)

    def test_v7_alias_collisions_choose_free_discuss_suffix_and_preserve_settings(self):
        settings = Settings(command_prefix="teacher", voice_idle_seconds=47,
                            question_seconds=19, noise_baseline_dbfs=-53)
        payload = {"schema_version": 7, **asdict(settings)}
        payload.pop("discussion_noise")
        payload["command_aliases"].pop("DISCUSSION")
        collisions = ["discussion", "討論", "讨论", "discuss", "discuss2"]
        payload["command_aliases"]["STANDBY"].extend(collisions)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            migrated = SettingsRepository(path).load()
        self.assertEqual(migrated.command_aliases["DISCUSSION"], ["discuss3"])
        self.assertEqual(migrated.command_aliases["STANDBY"], payload["command_aliases"]["STANDBY"])
        self.assertEqual(migrated.command_prefix, "teacher")
        self.assertEqual(migrated.voice_idle_seconds, 47)
        self.assertEqual(migrated.question_seconds, 19)
        self.assertEqual(migrated.noise_baseline_dbfs, -53)

    def test_observed_notice_corrections_use_word_boundaries_and_allow_override(self):
        corrections = dict(Settings().command_corrections)
        parser = CommandParser(Settings(command_corrections=corrections), cooldown_seconds=0)
        self.assertEqual(parser.parse("Not is.").intent, "NOTICE")
        self.assertEqual(parser.parse("Not it is.").intent, "NOTICE")
        self.assertEqual(parser.parse("I heard not it is today.").intent, "NOTICE")
        self.assertIsNone(parser.parse("Not this.").intent)
        corrections["not is"] = "question"
        override = CommandParser(Settings(command_corrections=corrections), cooldown_seconds=0)
        self.assertEqual(override.parse("Not is.").intent, "QUESTION")

    def test_migration_keeps_full_and_case_insensitive_custom_correction_dictionary(self):
        corrections = {f"custom{i}": "question" for i in range(99)}
        corrections["NOT IS"] = "wrong"
        payload = {"schema_version": 7, **asdict(Settings(command_corrections=corrections, question_seconds=19))}
        payload["command_aliases"].pop("DISCUSSION")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            migrated = SettingsRepository(path).load()
        self.assertEqual(migrated.command_corrections, corrections)
        self.assertEqual(migrated.question_seconds, 19)
        self.assertEqual(CommandParser(migrated).parse("not is").intent, "WRONG")

    def test_parser_and_manual_controller_keep_existing_semantics(self):
        parser = CommandParser(Settings())
        self.assertEqual(parser.parse("random", now=1).reason, "no exact command match")
        self.assertEqual(parser.parse("question", now=2).intent, "QUESTION")
        self.assertEqual(parser.parse("question", now=3).reason, "command cooldown")
        controller = ClassroomController(Settings(), lambda _seconds, _callback: lambda: None)
        self.assertTrue(controller.submit_intent("REST", "manual"))
        self.assertEqual(controller.state.base_mode, BaseMode.REST)
        self.assertFalse(controller.state.noise_enabled)
        with self.assertRaises(ValueError):
            Settings(session_logging_enabled=1)


if __name__ == "__main__":
    unittest.main()
