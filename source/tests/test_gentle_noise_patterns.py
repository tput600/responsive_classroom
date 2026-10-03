import unittest

from classroom_core import Pattern, Settings
from pattern_renderer import render


NOISE_PATTERNS = (
    Pattern.GREEN_BREATH,
    Pattern.ORANGE_ROTATE,
    Pattern.RED_EXCLAMATION,
    Pattern.DISCUSSION_GREEN_BREATH,
    Pattern.DISCUSSION_ORANGE_FLOW,
    Pattern.DISCUSSION_RED_BORDER,
)


class GentleNoisePatternTests(unittest.TestCase):
    def setUp(self):
        colors = {
            "green": "#B1CE86",
            "orange": "#FFC176",
            "red": "#ECAA88",
            "discussion_quiet": "#FFD8B0",
            "discussion_rising": "#F2D26E",
            "discussion_loud": "#DFA5AF",
        }
        self.settings = Settings(brightness_percent=30, colors={**Settings().colors, **colors})

    @staticmethod
    def active_positions(frame):
        return frozenset(index for index in range(64)
                         if max(frame[index * 3:index * 3 + 3]) > 1)

    def test_noise_patterns_are_soft_distinct_and_evolve_continuously(self):
        signatures_by_time = {time: {} for time in (0.0, 1.5, 4.0)}
        for pattern in NOISE_PATTERNS:
            frames = [render(pattern, step / 30, self.settings)
                      for step in range(12 * 30 + 1)]
            self.assertTrue(all(len(frame) == 192 for frame in frames), pattern)
            occupancies = [self.active_positions(frame) for frame in frames]
            self.assertTrue(all(occupancies), pattern)
            self.assertTrue(all(len(active) < 64 for active in occupancies), pattern)
            self.assertTrue(any(frame != frames[0] for frame in frames[1:]), pattern)
            max_step = max(abs(current - previous)
                           for previous, current in zip(frames, frames[1:])
                           for previous, current in zip(previous, current))
            self.assertLessEqual(max_step, 6, (pattern, max_step))
            for time in signatures_by_time:
                frame = render(pattern, time, self.settings)
                signatures_by_time[time][pattern] = self.active_positions(frame)

        for time, signatures in signatures_by_time.items():
            for index, first in enumerate(NOISE_PATTERNS):
                for second in NOISE_PATTERNS[index + 1:]:
                    self.assertNotEqual(signatures[first], signatures[second],
                                        (time, first, second))


if __name__ == "__main__":
    unittest.main()
