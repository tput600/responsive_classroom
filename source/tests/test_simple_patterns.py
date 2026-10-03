import unittest

from classroom_core import Pattern, Settings
from pattern_renderer import render


class SimplePatternTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(brightness_percent=60)

    @staticmethod
    def active_positions(frame):
        return frozenset(index for index in range(64)
                         if max(frame[index * 3:index * 3 + 3]) > 1)

    @staticmethod
    def positions(bitmap):
        return frozenset(y * 8 + x for y, row in enumerate(bitmap)
                         for x, pixel in enumerate(row) if pixel == "#")

    def test_question_circle_comet_rotates_continuously_and_loops(self):
        settings = Settings(brightness_percent=30, gamma_enabled=False)
        frames = [render(Pattern.QUESTION_GREEN_ROTATE, step / 30, settings)
                  for step in range(4 * 30 + 1)]
        self.assertTrue(all(any(frame) for frame in frames))
        self.assertNotEqual(frames[0], frames[15])
        self.assertEqual(frames[0], frames[-1])
        self.assertLessEqual(max(abs(a - b) for old, new in zip(frames, frames[1:])
                                 for a, b in zip(old, new)), 6)
        ccw = Settings(brightness_percent=30, gamma_enabled=False,
                       rotation_direction="counterclockwise")
        self.assertNotEqual(frames[15], render(Pattern.QUESTION_GREEN_ROTATE, .5, ccw))

    def test_correct_circle_and_wrong_x_remain_complete_and_animate(self):
        settings = Settings(brightness_percent=30, gamma_enabled=False)
        circle = frozenset(y * 8 + x for x, y in
                           ((2, 0), (3, 0), (4, 0), (5, 0), (6, 1), (7, 2),
                            (7, 3), (7, 4), (7, 5), (6, 6), (5, 7), (4, 7),
                            (3, 7), (2, 7), (1, 6), (0, 5), (0, 4), (0, 3),
                            (0, 2), (1, 1)))
        wrong_x = frozenset(y * 8 + x for i in range(8)
                            for x, y in ((i, i), (7 - i, i)))
        for pattern, geometry in ((Pattern.CORRECT_SQUARE, circle),
                                  (Pattern.WRONG_X, wrong_x)):
            with self.subTest(pattern=pattern):
                frames = [render(pattern, elapsed, settings)
                          for elapsed in (0.0, .8, 1.6, 2.4, 3.2)]
                self.assertTrue(all(self.active_positions(frame) == geometry
                                    for frame in frames))
                self.assertGreater(len(set(frames)), 1)
                self.assertLessEqual(max(abs(a - b) for old, new in zip(frames, frames[1:])
                                         for a, b in zip(old, new)), 45)

    def test_discussion_quiet_lights_follow_two_continuous_tracks(self):
        settings = Settings(brightness_percent=30, gamma_enabled=False)
        start = render(Pattern.DISCUSSION_GREEN_BREATH, 0, settings)
        later = render(Pattern.DISCUSSION_GREEN_BREATH, 2.5, settings)
        left = [i for i in range(64) if i % 8 <= 2]
        right = [i for i in range(64) if i % 8 >= 5]
        self.assertTrue(any(any(start[i * 3:i * 3 + 3]) for i in left))
        self.assertTrue(any(any(start[i * 3:i * 3 + 3]) for i in right))
        self.assertNotEqual(self.active_positions(start), self.active_positions(later))
        frames = [render(Pattern.DISCUSSION_GREEN_BREATH, i / 30, settings)
                  for i in range(10 * 30 + 1)]
        self.assertTrue(all(any(f) for f in frames))
        self.assertLessEqual(max(abs(a - b) for old, new in zip(frames, frames[1:])
                                 for a, b in zip(old, new)), 6)
        self.assertEqual(frames[0], frames[-1])

    def test_standby_glow_moves_smoothly_without_blackout_or_static_icon(self):
        settings=Settings(brightness_percent=30,gamma_enabled=False)
        def center(frame):
            weights=[sum(frame[i*3:i*3+3]) for i in range(64)]
            total=sum(weights)
            return (sum((i%8)*w for i,w in enumerate(weights))/total,
                    sum((i//8)*w for i,w in enumerate(weights))/total)
        start=render(Pattern.WARM_BREATH,0,settings)
        later=render(Pattern.WARM_BREATH,4,settings)
        self.assertGreater(center(later)[0]-center(start)[0],1)
        self.assertLess(center(later)[1],center(start)[1]-1)
        frames=[render(Pattern.WARM_BREATH,i/20,settings) for i in range(961)]
        self.assertTrue(all(max(f)>0 for f in frames))
        self.assertLessEqual(max(abs(a-b) for old,new in zip(frames,frames[1:])
                                 for a,b in zip(old,new)),2)

    def test_rest_wave_moves_and_rest_end_keeps_hourglass_frame(self):
        wave_start = self.active_positions(
            render(Pattern.REST_GREEN_BREATH, 0.0, self.settings))
        wave_later = self.active_positions(
            render(Pattern.REST_GREEN_BREATH, 1.0, self.settings))
        self.assertNotEqual(wave_start, wave_later)
        self.assertGreaterEqual(len(wave_start), 8)

        def hourglass(elapsed, remaining):
            return render(Pattern.REST_END_WARM_BREATH, elapsed, self.settings,
                          rest_remaining_seconds=remaining)

        def value(frame, x, y):
            return max(frame[(y * 8 + x) * 3:(y * 8 + x) * 3 + 3])

        outline = frozenset(y * 8 + x for y, row in enumerate((
            "########", ".#....#.", "..#..#..", "...##...",
            "...##...", "..#..#..", ".#....#.", "########"))
                            for x, pixel in enumerate(row) if pixel == "#")
        start = hourglass(0.0, 20.0)
        quarter = hourglass(5.0, 15.0)
        middle = hourglass(10.0, 10.0)
        end = hourglass(20.0, 0.0)
        for frame in (start, quarter, middle, end):
            self.assertTrue(outline.issubset(self.active_positions(frame)))

        self.assertGreater(value(start, 2, 1), value(start, 3, 6))
        self.assertGreater(value(quarter, 3, 6), value(start, 3, 6))
        self.assertGreaterEqual(value(middle, 3, 6), value(quarter, 3, 6))
        self.assertGreater(value(middle, 2, 6), value(quarter, 2, 6))
        self.assertEqual(value(end, 3, 6), value(middle, 3, 6))
        self.assertEqual(value(end, 2, 1), 0)
        self.assertGreater(value(end, 3, 5), value(start, 3, 5))
        self.assertNotEqual(hourglass(10.0, 10.0), hourglass(10.5, 10.0))


if __name__ == "__main__":
    unittest.main()
