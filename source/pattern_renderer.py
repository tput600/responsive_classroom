"""Deterministic 8x8 RGB patterns shared by the preview and WLED output."""

from __future__ import annotations

import math
import time

from classroom_core import Pattern, Settings

SIZE = 8
PIXELS = SIZE * SIZE


def _splat_max(levels: list[float], x: float, y: float, level: float) -> None:
    """Bilinearly add a soft point while retaining the strongest overlap."""
    left, top = math.floor(x), math.floor(y)
    fx, fy = x - left, y - top
    for px, wx in ((left, 1 - fx), (left + 1, fx)):
        for py, wy in ((top, 1 - fy), (top + 1, fy)):
            if 0 <= px < SIZE and 0 <= py < SIZE:
                index = py * SIZE + px
                levels[index] = max(levels[index], level * wx * wy)


def _path_sample(path: tuple[tuple[int, int], ...], progress: float) -> tuple[float, float]:
    """Sample a closed pixel path continuously at a fractional path index."""
    position = progress % len(path)
    index = math.floor(position)
    fraction = position - index
    x0, y0 = path[index]
    x1, y1 = path[(index + 1) % len(path)]
    return x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction


def _path_comet(levels: list[float], path: tuple[tuple[int, int], ...],
                progress: float, tail: tuple[float, ...], head: float) -> None:
    for offset, level in enumerate((head, *tail)):
        x, y = _path_sample(path, progress - offset)
        _splat_max(levels, x, y, level)


def _pixel(color: str, level: float, settings: Settings) -> tuple[int, int, int]:
    level = max(0.0, min(1.0, level)) * settings.brightness_percent / 100
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    if settings.gamma_enabled:
        channels = [channel ** (1 / settings.gamma) for channel in channels]
    return tuple(round(channel * level * 255) for channel in channels)


def render(pattern: Pattern, elapsed: float, settings: Settings,
           rest_remaining_seconds: float | None = None) -> bytes:
    """Return one row-major frame with global brightness and gamma applied."""
    frame = [(0, 0, 0)] * PIXELS

    def put(x: int, y: int, color: str, level: float) -> None:
        if 0 <= x < SIZE and 0 <= y < SIZE:
            frame[y * SIZE + x] = _pixel(color, level, settings)

    if pattern is Pattern.WARM_BREATH:
        # One soft halo follows a continuous slow orbit. Both its position and
        # brightness ease smoothly; there is no icon, strobe, or hard reset.
        orbit = 2 * math.pi * elapsed / 16.0
        cx, cy = 3.5 + 1.7 * math.sin(orbit), 3.5 + 1.3 * math.cos(orbit)
        phase = 0.5 - 0.5 * math.cos(2 * math.pi * elapsed / 6.0)
        low, high = settings.pattern_levels["warm"]
        for y in range(SIZE):
            for x in range(SIZE):
                distance = math.hypot(x - cx, y - cy)
                strength = math.exp(-distance * distance / (2 * 1.25 * 1.25))
                put(x, y, settings.colors["warm"],
                    strength * (low + (high - low) * phase) / 100)
    elif pattern is Pattern.GREEN_BREATH:
        # Notice: a quiet central field, with the corners left dark for contrast.
        phase = 0.5 - 0.5 * math.cos(2 * math.pi * elapsed / 12.0)
        for y in range(SIZE):
            for x in range(SIZE):
                radius = max(abs(x - 3.5), abs(y - 3.5))
                if radius <= 3:
                    put(x, y, settings.colors["green"], .35 + .25 * phase)
    elif pattern is Pattern.ORANGE_ROTATE:
        # Rising: a broad horizontal ribbon drifts slowly from top to bottom.
        travel = (elapsed / 18.0 * 8.0) % 8.0
        center = travel if settings.rotation_direction == "clockwise" else 8.0 - travel
        for y in range(SIZE):
            distance = abs((y - center + 4.0) % 8.0 - 4.0)
            strength = max(0.0, 1.0 - distance / 1.55)
            if strength > 0:
                for x in range(SIZE):
                    edge = 0.78 + 0.22 * math.sin(math.pi * (x + 0.5) / SIZE)
                    put(x, y, settings.colors["orange"], strength * edge * 0.72)
    elif pattern is Pattern.RED_EXCLAMATION:
        # Loud: a wide vertical wave slowly meanders across the board.
        phase = 2 * math.pi * elapsed / 24.0
        for y in range(SIZE):
            for x in range(SIZE):
                center = 3.5 + 1.7 * math.sin(y * math.pi / 3.5 - phase)
                distance = abs(x - center)
                if distance < 2.0:
                    strength = 1.0 - distance / 2.0
                    put(x, y, settings.colors["red"], strength * 0.68)
    elif pattern is Pattern.DISCUSSION_GREEN_BREATH:
        left_path = ((0, 2), (1, 2), (2, 2), (2, 3), (2, 4),
                     (2, 5), (1, 5), (0, 5), (0, 4), (0, 3))
        right_path = tuple((7 - x, y) for x, y in reversed(left_path))
        progress = elapsed / 10.0 * len(left_path)
        levels = [0.0] * PIXELS
        color = settings.colors["discussion_quiet"]
        _path_comet(levels, left_path, progress, (.48, .25, .10), .80)
        _path_comet(levels, right_path, progress + len(right_path) / 2,
                    (.48, .25, .10), .80)
        for index, level in enumerate(levels):
            if level:
                put(index % SIZE, index // SIZE, color, level)
    elif pattern is Pattern.DISCUSSION_ORANGE_FLOW:
        phase = 2 * math.pi * elapsed / 12.0
        color = settings.colors["discussion_rising"]
        levels = [0.0] * PIXELS
        for arm in range(4):
            for radius in (1.1, 1.8, 2.5, 3.1):
                theta = phase + arm * math.pi / 2 + radius * .35
                x = 3.5 + radius * math.cos(theta)
                y = 3.5 + radius * math.sin(theta)
                _splat_max(levels, x, y, .35 + .30 * radius / 3.1)
        for index, level in enumerate(levels):
            if level:
                put(index % SIZE, index // SIZE, color, level)
    elif pattern is Pattern.DISCUSSION_RED_BORDER:
        border = (tuple((x, 0) for x in range(8)) +
                  tuple((7, y) for y in range(1, 7)) +
                  tuple((x, 7) for x in range(7, -1, -1)) +
                  tuple((0, y) for y in range(6, 0, -1)))
        direction = 1 if settings.rotation_direction == "clockwise" else -1
        progress = direction * elapsed / 12.0 * len(border)
        color = settings.colors["discussion_loud"]
        levels = [0.0] * PIXELS
        _path_comet(levels, border, progress, (.56, .36, .20, .09), .82)
        _path_comet(levels, border, progress + len(border) / 2,
                    (.56, .36, .20, .09), .82)
        for index, level in enumerate(levels):
            if level:
                put(index % SIZE, index // SIZE, color, level)
    elif pattern is Pattern.QUESTION_GREEN_ROTATE:
        circle = ((2, 0), (3, 0), (4, 0), (5, 0), (6, 1), (7, 2),
                  (7, 3), (7, 4), (7, 5), (6, 6), (5, 7), (4, 7),
                  (3, 7), (2, 7), (1, 6), (0, 5), (0, 4), (0, 3),
                  (0, 2), (1, 1))
        direction = 1 if settings.rotation_direction == "clockwise" else -1
        head = direction * elapsed / 4.0 * len(circle)
        for index, (x, y) in enumerate(circle):
            distance = ((index - head + len(circle) / 2) % len(circle)
                        - len(circle) / 2)
            if abs(distance) < 5:
                level = .90 * (.5 + .5 * math.cos(math.pi * distance / 5))
                put(x, y, settings.colors["question"], level)
    elif pattern is Pattern.CORRECT_SQUARE:
        circle = ((2, 0), (3, 0), (4, 0), (5, 0), (6, 1), (7, 2), (7, 3),
                  (7, 4), (7, 5), (6, 6), (5, 7), (4, 7), (3, 7), (2, 7),
                  (1, 6), (0, 5), (0, 4), (0, 3), (0, 2), (1, 1))
        breath = .5 - .5 * math.cos(2 * math.pi * elapsed / 3.0)
        level = .40 + .40 * breath
        for x, y in circle:
            put(x, y, settings.colors["correct"], level)
    elif pattern is Pattern.WRONG_X:
        center = 3.5 + 3.5 * math.sin(2 * math.pi * elapsed / 3.2)
        for i in range(SIZE):
            level = .30 + .55 * max(0.0, 1.0 - abs(i - center) / 1.4)
            put(i, i, settings.colors["wrong"], level)
            put(7 - i, i, settings.colors["wrong"], level)
    elif pattern is Pattern.REST_GREEN_BREATH:
        phase = elapsed * 2 * math.pi / settings.periods["rest_green"]
        for x in range(SIZE):
            y = 3.5 + 2.5 * math.sin(phase + x * math.pi / 3.5)
            row = math.floor(y)
            fraction = y - row
            put(x, row, settings.colors["rest"], 1 - fraction)
            put(x, row + 1, settings.colors["rest"], fraction)
    elif pattern is Pattern.REST_END_WARM_BREATH:
        total = float(settings.rest_reminder_seconds)
        remaining = (max(0.0, total - elapsed) if rest_remaining_seconds is None
                     else max(0.0, rest_remaining_seconds))
        drain = min(1.0, max(0.0, 1.0 - remaining / total)) if total > 0 else 1.0
        color = settings.colors["rest_end"]
        hourglass = (
            "########",
            ".#....#.",
            "..#..#..",
            "...##...",
            "...##...",
            "..#..#..",
            ".#....#.",
            "########",
        )
        levels = [0.0] * PIXELS
        for y, row in enumerate(hourglass):
            for x, pixel in enumerate(row):
                if pixel == "#":
                    levels[y * SIZE + x] = .22

        upper_groups = (((1, 2), (1, 5)), ((1, 3), (1, 4)), ((2, 3), (2, 4)))
        lower_groups = (((6, 3), (6, 4)), ((6, 2), (6, 5)), ((5, 3), (5, 4)))
        for index, group in enumerate(upper_groups):
            level = min(1.0, max(0.0, 1.0 - drain * 3 + index)) * .9
            for y, x in group:
                pixel = y * SIZE + x
                levels[pixel] = max(levels[pixel], level)
        for index, group in enumerate(lower_groups):
            level = min(1.0, max(0.0, drain * 3 - index)) * .9
            for y, x in group:
                pixel = y * SIZE + x
                levels[pixel] = max(levels[pixel], level)

        stage_a = max(0.0, total - settings.rest_end_stage_seconds["stage_a"])
        stage_b = max(0.0, min(total, settings.rest_end_stage_seconds["stage_a"])
                      - settings.rest_end_stage_seconds["stage_b"])
        elapsed_a = min(max(0.0, elapsed), stage_a)
        elapsed_b = min(max(0.0, elapsed - stage_a), stage_b)
        elapsed_c = max(0.0, elapsed - stage_a - stage_b)
        phase = (elapsed_a / settings.rest_end_periods["stage_a"] +
                 elapsed_b / settings.rest_end_periods["stage_b"] +
                 elapsed_c / settings.rest_end_periods["stage_c"])
        fraction = phase % 1.0
        grain_y = 2.3 + (6.2 - 2.3) * fraction
        grain_level = .85 * math.sin(math.pi * fraction) ** 2
        _splat_max(levels, 3.5, grain_y, grain_level)

        for index, level in enumerate(levels):
            if level:
                put(index % SIZE, index // SIZE, color, level)
    return bytes(channel for pixel in frame for channel in pixel)


def transition_duration(previous: Pattern, current: Pattern, settings: Settings) -> float:
    if previous is current:
        return 0.0
    overlays = {Pattern.QUESTION_GREEN_ROTATE, Pattern.CORRECT_SQUARE, Pattern.WRONG_X}
    breaths = {Pattern.WARM_BREATH, Pattern.GREEN_BREATH, Pattern.REST_GREEN_BREATH,
               Pattern.REST_END_WARM_BREATH}
    rotations = {Pattern.ORANGE_ROTATE, Pattern.QUESTION_GREEN_ROTATE}
    key = ""
    if current is Pattern.OFF:
        key = "to_off"
    elif previous is Pattern.REST_GREEN_BREATH and current is Pattern.REST_END_WARM_BREATH:
        key = "rest_to_rest_end"
    elif previous in overlays and current not in overlays:
        key = "overlay_to_base"
    elif current in overlays and previous not in overlays:
        key = "base_to_overlay"
    elif previous is Pattern.RED_EXCLAMATION and current in rotations:
        key = "warning_to_rotate"
    elif previous in rotations and current is Pattern.RED_EXCLAMATION:
        key = "rotate_to_warning"
    elif previous in breaths and current in rotations:
        key = "breath_to_rotate"
    elif previous in breaths and current in breaths:
        key = "breath_to_breath"
    milliseconds = settings.default_fade_ms if not key else settings.transition_ms[key]
    return min(1.0, max(settings.minimum_transition_ms, milliseconds) / 1000)


def crossfade(old_frame: bytes, new_frame: bytes, progress: float) -> bytes:
    """Linearly blend two RGB frames; progress is clamped to the closed interval."""
    if len(old_frame) != len(new_frame) or len(new_frame) != 192:
        raise ValueError("Crossfade requires two 192-byte RGB frames")
    amount = max(0.0, min(1.0, progress))
    return bytes(round(old + (new - old) * amount)
                 for old, new in zip(old_frame, new_frame))


def music_wave(frame: bytes, level: float, elapsed: float) -> bytes:
    """Scale the entire frame by measured music volume, preserving geometry and hue."""
    if len(frame) != 192:
        raise ValueError("Music response requires a 192-byte RGB frame")
    if not math.isfinite(level) or level < 0:
        return frame
    factor = .08 + .92 * min(1.0, max(0.0, level) / .16) ** 2
    return bytes(round(channel * factor) for channel in frame)


class FrameBlender:
    """Blend pattern changes from the frame currently visible to a live target."""

    def __init__(self, duration: float = 1.4, noise_duration: float = 3.0) -> None:
        self.duration = max(0.001, duration)
        self.noise_duration = max(self.duration, noise_duration)
        self._duration = self.duration
        self.frame: bytes | None = None
        self._source: bytes | None = None
        self._started = 0.0
        self._pattern: Pattern | None = None

    def render(self, pattern: Pattern, elapsed: float, settings: Settings,
               rest_remaining_seconds: float | None = None,
               now: float | None = None) -> bytes:
        """Return the visible frame, smoothly retargeting from its current value."""
        target = render(pattern, elapsed, settings, rest_remaining_seconds)
        current_time = time.monotonic() if now is None else now
        if self.frame is None:
            self.frame = target
            self._pattern = pattern
            return target
        if pattern is not self._pattern:
            ambient = {Pattern.GREEN_BREATH, Pattern.ORANGE_ROTATE, Pattern.RED_EXCLAMATION,
                       Pattern.DISCUSSION_GREEN_BREATH, Pattern.DISCUSSION_ORANGE_FLOW,
                       Pattern.DISCUSSION_RED_BORDER}
            self._duration = (self.noise_duration if pattern in ambient and self._pattern in ambient
                              else self.duration)
            self._source = self.frame
            self._started = current_time
            self._pattern = pattern
        if self._source is None:
            self.frame = target
            return target
        progress = max(0.0, min(1.0, (current_time - self._started) / self._duration))
        eased = progress * progress * (3 - 2 * progress)
        self.frame = crossfade(self._source, target, eased)
        if progress >= 1:
            self._source = None
        return self.frame
