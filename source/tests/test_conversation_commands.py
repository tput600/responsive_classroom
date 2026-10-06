import unittest
import json
import tempfile
from dataclasses import asdict

from pathlib import Path

from classroom_audio import AudioRuntime, CommandParser
from classroom_core import Settings, SettingsRepository, SCHEMA_VERSION


class ConversationCommandTests(unittest.TestCase):
    def test_complete_english_trigger_in_sentence_and_suffix_boundaries(self):
        parser = CommandParser(Settings(), cooldown_seconds=0)
        self.assertEqual(parser.parse("one two three question", now=1).intent, "QUESTION")
        self.assertEqual(parser.parse("you are wrong", now=2).intent, "WRONG")
        self.assertIsNone(parser.parse("questionnaire", now=3).intent)
        self.assertIsNone(parser.parse("incorrectly", now=4).intent)
        self.assertEqual(parser.parse("incorrect", now=5).intent, "WRONG")

    def test_last_complete_trigger_wins_and_chinese_trigger_can_be_embedded(self):
        parser = CommandParser(Settings(), cooldown_seconds=0)
        self.assertEqual(parser.parse("question, then you are wrong", now=1).intent, "WRONG")
        self.assertEqual(parser.parse("我們現在請注意老師，然後討論", now=2).intent, "DISCUSSION")

    def test_voice_pause_and_resume_phrases_are_recognized(self):
        parser = CommandParser(Settings(), cooldown_seconds=0)
        self.assertEqual(parser.parse("no sensor", now=1).intent, "VOICE_OFF")
        self.assertEqual(parser.parse("關閉語音", now=2).intent, "VOICE_OFF")
        self.assertEqual(parser.parse("sensor on", now=3).intent, "VOICE_ON")
        self.assertEqual(parser.parse("啟動語音", now=4).intent, "VOICE_ON")
        self.assertIsNone(parser.parse("sensoring", now=5).intent)

    def test_word_boundary_correction_preserves_both_forms(self):
        parser = CommandParser(Settings(), cooldown_seconds=0)
        result = parser.parse("the model said NOT IS today", now=1)
        self.assertEqual(result.text, "the model said NOT IS today")
        self.assertEqual(result.corrected_text, "the model said notice today")
        self.assertEqual(result.intent, "NOTICE")
        no_boundary = parser.parse("not issue", now=2)
        self.assertNotIn("notice", no_boundary.corrected_text)

    def test_observed_plural_is_corrected_with_boundaries_and_custom_override(self):
        parser = CommandParser(Settings(), cooldown_seconds=0)
        result = parser.parse('One,2, three questions.')
        self.assertEqual(result.text, 'One,2, three questions.')
        self.assertEqual(result.corrected_text, 'one 2 three question')
        self.assertEqual(result.intent, 'QUESTION')
        self.assertIsNone(parser.parse('questionsomething').intent)
        corrections = {**Settings().command_corrections, 'questions': 'wrong'}
        self.assertEqual(CommandParser(Settings(command_corrections=corrections)).parse('questions').intent, 'WRONG')

    def test_schema_nine_migration_preserves_timers_aliases_and_correction_overrides(self):
        original = {"schema_version": 9, **asdict(Settings(question_seconds=19, rest_seconds=123,
                    voice_idle_seconds=47, command_corrections={'Questions': 'wrong', 'custom': 'rest'}))}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            content = json.dumps(original, ensure_ascii=False)
            path.write_text(content, encoding='utf-8')
            migrated = SettingsRepository(path).load()
            self.assertEqual((migrated.question_seconds, migrated.rest_seconds, migrated.voice_idle_seconds), (19, 123, 47))
            self.assertEqual(migrated.command_aliases, original['command_aliases'])
            self.assertEqual(migrated.command_corrections['Questions'], 'wrong')
            self.assertEqual(migrated.command_corrections['custom'], 'rest')
            self.assertNotIn('questions', migrated.command_corrections)
            self.assertEqual(path.with_suffix('.json.v9.bak').read_text(encoding='utf-8'), content)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['schema_version'], SCHEMA_VERSION)

    def test_longest_alias_at_same_start_wins(self):
        aliases = {key: list(value) for key, value in Settings().command_aliases.items()}
        aliases["QUESTION"].append("notice now")
        parser = CommandParser(Settings(command_aliases=aliases), cooldown_seconds=0)
        self.assertEqual(parser.parse("notice now", now=1).intent, "QUESTION")
        self.assertEqual(parser.parse("notice now, then rest", now=2).intent, "REST")
        aliases["QUESTION"].append("請注意老師")
        aliases["NOTICE"].remove("請注意老師")
        parser = CommandParser(Settings(command_aliases=aliases), cooldown_seconds=0)
        self.assertEqual(parser.parse("請注意老師", now=3).intent, "QUESTION")
        self.assertEqual(parser.parse("我們要題問", now=4).intent, "QUESTION")

    def test_changed_capture_rate_invalidates_saved_baseline(self):
        calibration = {**Settings().calibration, "quality": "PASS", "sample_rate": 48000}
        settings = Settings(calibration=calibration)
        statuses = []
        runtime = AudioRuntime("mic", Path("."), settings, -45, lambda: True, lambda: 0,
                               lambda *args: statuses.append(args), lambda *_: None,
                               lambda *_: None, lambda *_: None)
        self.assertTrue(runtime._invalidate_calibration_for_rate(44100))
        self.assertIsNone(runtime.noise.baseline_dbfs)
        self.assertEqual(runtime.noise.state.name, "UNKNOWN")
        self.assertIn("請重新校準", statuses[-1][-1])


if __name__ == "__main__":
    unittest.main()
