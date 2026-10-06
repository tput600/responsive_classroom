import unittest

from classroom_audio import CommandParser, NoiseAnalyzer
from classroom_core import BaseMode, NoiseState, Settings


class ContextNoiseTests(unittest.TestCase):
    def test_short_authorized_bare_command_and_exact_correction(self):
        settings = Settings(command_prefix="class",
                            command_corrections={"not is": "notice", "not it is": "notice"})
        parser = CommandParser(settings)
        self.assertEqual(parser.parse("notice", now=1).intent, "NOTICE")
        self.assertIsNone(parser.parse("today is not noticeable", now=4).intent)
        self.assertEqual(parser.parse("not it is", now=7).intent, "NOTICE")

    def test_profile_change_restarts_candidate_and_tags_reading_context(self):
        analyzer = NoiseAnalyzer(Settings(), -50)
        analyzer.feed([.02, -.02] * 100, now=1, mode=BaseMode.NOTICE,
                                context_revision=4)
        reading = analyzer.feed([.02, -.02] * 100, now=1.1, mode=BaseMode.DISCUSSION,
                                context_revision=5)
        self.assertEqual(reading.context_revision, 5)
        self.assertEqual(reading.state, NoiseState.QUIET)
        self.assertEqual(analyzer._candidate, NoiseState.RISING)
        self.assertEqual(analyzer._candidate_since, 1.1)


if __name__ == "__main__":
    unittest.main()
