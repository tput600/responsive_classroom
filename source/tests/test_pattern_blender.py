import unittest

from classroom_core import Pattern, Settings
from pattern_renderer import FrameBlender, render


class FrameBlenderTests(unittest.TestCase):
    def test_retarget_starts_at_current_frame_and_reaches_live_target(self):
        settings = Settings()
        blender = FrameBlender()
        first = blender.render(Pattern.WARM_BREATH, 0.2, settings, now=0.0)
        self.assertEqual(blender.frame, first)
        middle = blender.render(Pattern.ORANGE_ROTATE, 0.3, settings, now=0.7)
        self.assertEqual(len(middle), 192)
        self.assertNotEqual(middle, render(Pattern.ORANGE_ROTATE, 0.3, settings))

        before_retarget = blender.render(Pattern.GREEN_BREATH, 0.4, settings, now=0.8)
        immediate = blender.render(Pattern.RED_EXCLAMATION, 0.1, settings, now=0.8)
        self.assertEqual(immediate, before_retarget)
        self.assertEqual(before_retarget, middle)
        endpoint = blender.render(Pattern.RED_EXCLAMATION, 0.6, settings, now=3.8)
        self.assertEqual(endpoint, render(Pattern.RED_EXCLAMATION, 0.6, settings))
        self.assertEqual(blender.frame, endpoint)

    def test_noise_to_noise_transition_takes_three_seconds_without_jumping(self):
        settings = Settings()
        blender = FrameBlender()
        initial = blender.render(Pattern.GREEN_BREATH, 0.2, settings, now=0)
        start = blender.render(Pattern.RED_EXCLAMATION, 0.2, settings, now=1)
        self.assertEqual(start, initial)
        halfway = blender.render(Pattern.RED_EXCLAMATION, 0.2, settings, now=2.5)
        target = render(Pattern.RED_EXCLAMATION, 0.2, settings)
        self.assertNotEqual(halfway, start)
        self.assertNotEqual(halfway, target)
        self.assertEqual(blender.render(Pattern.RED_EXCLAMATION, 0.2, settings, now=4), target)


if __name__ == "__main__":
    unittest.main()
