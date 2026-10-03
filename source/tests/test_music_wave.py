import unittest

from classroom_core import Pattern, Settings
from pattern_renderer import music_wave, render


class MusicWaveTests(unittest.TestCase):
    def test_traveling_ribbon_wraps_without_a_jump_or_blackout(self):
        settings=Settings(brightness_percent=30,gamma_enabled=False)
        before=render(Pattern.ORANGE_ROTATE,18-.01,settings)
        after=render(Pattern.ORANGE_ROTATE,18+.01,settings)
        self.assertTrue(any(before) and any(after))
        self.assertLessEqual(max(abs(a-b) for a,b in zip(before,after)),2)

    def test_actual_level_changes_pattern_without_losing_mask_or_brightness_cap(self):
        original = render(Pattern.CORRECT_SQUARE, 2, Settings(brightness_percent=30, gamma_enabled=False))
        silent = music_wave(original, 0, 1)
        # Measured p10/p90 envelope levels from the bundled MP3 at 50% volume.
        # The former renderer produced only ~1.4x brightness contrast here.
        quiet = music_wave(original, .048, 1)
        loud = music_wave(original, .144, 1)
        peak = music_wave(original, .45, 1)
        self.assertLess(sum(silent), sum(quiet))
        self.assertLess(sum(quiet), sum(loud))
        self.assertEqual(peak, original)
        self.assertEqual(quiet, music_wave(original, .048, 2))
        for i in range(64):
            self.assertEqual(any(original[i * 3:i * 3 + 3]),
                             any(quiet[i * 3:i * 3 + 3]))
            self.assertEqual(any(original[i * 3:i * 3 + 3]),
                             any(loud[i * 3:i * 3 + 3]))
        self.assertGreater(sum(loud), sum(quiet) * 4)
        self.assertTrue(all(0 <= a <= b for a, b in zip(quiet, original)))
        self.assertEqual(music_wave(original, -0.1, 1), original)
        self.assertEqual(music_wave(original, float("nan"), 1), original)
        self.assertEqual(music_wave(original, float("inf"), 1), original)
        for level in (0, .05, .2, .45, .9):
            scaled = music_wave(original, level, 1)
            self.assertEqual(tuple(i for i in range(64)
                                   if any(scaled[i * 3:i * 3 + 3])),
                             tuple(i for i in range(64)
                                   if any(original[i * 3:i * 3 + 3])))
            self.assertTrue(all(0 <= a <= b for a, b in zip(scaled, original)))
        self.assertEqual(music_wave(original, .2, 1), music_wave(original, .2, 50))
