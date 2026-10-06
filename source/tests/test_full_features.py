import json
import math
import socket
import tempfile
import unittest
from pathlib import Path

import numpy as np

from classroom_audio import CommandParser, NoiseAnalyzer, normalize_phrase
from classroom_core import (
    BaseMode,
    ClassroomController,
    ClassroomState,
    NoiseState,
    Overlay,
    Pattern,
    PatternMapper,
    RestStage,
    Settings,
    SettingsRepository,
    SCHEMA_VERSION,
)
from classroom_hardware import WledDevice, make_ddp_packet, orient_frame
from pattern_renderer import crossfade, render, transition_duration


class Scheduler:
    def __init__(self):
        self.events = []

    def schedule(self, seconds, callback):
        event = [seconds, callback, False]
        self.events.append(event)

        def cancel():
            event[2] = True

        return cancel


class FullControllerTests(unittest.TestCase):
    def test_notice_maps_each_noise_state_to_its_own_pattern(self):
        mapper = PatternMapper()
        self.assertEqual(mapper.map(ClassroomState(BaseMode.NOTICE, noise_state=NoiseState.QUIET)), Pattern.GREEN_BREATH)
        self.assertEqual(mapper.map(ClassroomState(BaseMode.NOTICE, noise_state=NoiseState.RISING)), Pattern.ORANGE_ROTATE)
        self.assertEqual(mapper.map(ClassroomState(BaseMode.NOTICE, noise_state=NoiseState.LOUD)), Pattern.RED_EXCLAMATION)
        self.assertEqual(mapper.map(ClassroomState(BaseMode.NOTICE, noise_state=NoiseState.UNKNOWN)), Pattern.WARM_BREATH)
        self.assertEqual(mapper.map(ClassroomState(BaseMode.DISCUSSION, noise_state=NoiseState.QUIET)), Pattern.DISCUSSION_GREEN_BREATH)
        self.assertEqual(mapper.map(ClassroomState(BaseMode.DISCUSSION, noise_state=NoiseState.RISING)), Pattern.DISCUSSION_ORANGE_FLOW)
        self.assertEqual(mapper.map(ClassroomState(BaseMode.DISCUSSION, noise_state=NoiseState.LOUD)), Pattern.DISCUSSION_RED_BORDER)

    def test_voice_result_is_discarded_after_manual_state_change(self):
        controller = ClassroomController(Settings(), Scheduler().schedule)
        captured = controller.manual_revision
        controller.set_base_mode(BaseMode.NOTICE)
        self.assertFalse(controller.submit_intent("QUESTION", "voice", captured))
        self.assertEqual(controller.state.base_mode, BaseMode.NOTICE)
        captured = controller.manual_revision
        self.assertTrue(controller.submit_intent("CORRECT", "voice", captured))
        self.assertEqual(controller.state.overlay, Overlay.CORRECT)
        controller.set_base_mode(BaseMode.STANDBY)
        self.assertFalse(controller.submit_intent("WRONG", "voice", captured))
        self.assertEqual(controller.state.base_mode, BaseMode.STANDBY)
        self.assertIsNone(controller.state.overlay)

    def test_rest_has_two_configurable_stages_and_restoring_standby(self):
        clock = [0.0]
        events = []

        def schedule(seconds, callback):
            due = [clock[0] + seconds, callback, False]
            events.append(due)
            return lambda: due.__setitem__(2, True)

        def advance(seconds):
            target = clock[0] + seconds
            while True:
                due = [event for event in events if not event[2] and event[0] <= target]
                if not due:
                    break
                event = min(due, key=lambda item: item[0])
                clock[0] = event[0]
                event[2] = True
                event[1]()
            clock[0] = target

        settings = Settings(debug_rest=True, rest_debug_seconds=3, rest_reminder_seconds=2)
        controller = ClassroomController(settings, schedule, lambda: clock[0])
        controller.set_base_mode(BaseMode.REST)
        advance(3)
        self.assertEqual(controller.state.rest_stage, RestStage.REMINDER)
        advance(2)
        self.assertEqual(controller.state, ClassroomState(BaseMode.STANDBY))


class NoiseAndVoiceTests(unittest.TestCase):
    @staticmethod
    def tone(dbfs):
        t = np.arange(1600, dtype=np.float32) / 16000
        return np.sin(2 * math.pi * 440 * t) * (10 ** (dbfs / 20) * math.sqrt(2))

    def test_noise_thresholds_use_hold_times_and_hysteresis(self):
        settings = Settings(
            speech_noise_guard=False,
            noise_rising_db=8, noise_loud_db=18,
            noise_rising_exit_db=5, noise_loud_exit_db=15,
            noise_smoothing_ms=100, noise_rising_enter_seconds=0.2,
            noise_loud_enter_seconds=0.2, noise_exit_seconds=0.2,
        )
        analyzer = NoiseAnalyzer(settings, baseline_dbfs=-60)
        for i in range(1, 6):
            reading = analyzer.feed(self.tone(-50), now=i / 10)
        self.assertEqual(reading.state, NoiseState.RISING)
        for i in range(6, 12):
            reading = analyzer.feed(self.tone(-40), now=i / 10)
        self.assertEqual(reading.state, NoiseState.LOUD)
        for i in range(12, 17):
            reading = analyzer.feed(self.tone(-46), now=i / 10)
        self.assertEqual(reading.state, NoiseState.RISING)
        for i in range(17, 22):
            reading = analyzer.feed(self.tone(-56), now=i / 10)
        self.assertEqual(reading.state, NoiseState.QUIET)
        self.assertAlmostEqual(reading.relative_db, 4.0, delta=2.0)

    def test_ten_second_calibration_and_optional_spl_offset(self):
        analyzer = NoiseAnalyzer(Settings(speech_noise_guard=False, noise_smoothing_ms=750,
                                          calibration={**Settings().calibration, 'discard_initial_sec': 0}))
        analyzer.start_calibration(now=0, seconds=1)
        for t in range(11):
            reading = analyzer.feed(self.tone(-55), now=t / 10)
        self.assertAlmostEqual(analyzer.baseline_dbfs, -55, delta=0.2)
        self.assertEqual(reading.state, NoiseState.QUIET)
        analyzer.set_spl_reference(60, -55)
        for now in np.arange(1.1, 2.0, .1):
            reading = analyzer.feed(self.tone(-50), now=float(now))
        self.assertAlmostEqual(reading.estimated_spl, 65, delta=.2)

    def test_command_prefix_exact_alias_and_cooldown(self):
        parser = CommandParser(Settings())
        self.assertEqual(parser.parse("class question", now=1).intent, "QUESTION")
        self.assertEqual(parser.parse("class question", now=2).reason, "command cooldown")
        self.assertEqual(parser.parse("class class class", now=4).intent, "NOTICE")
        self.assertEqual(parser.parse("課堂問題", now=7).intent, "QUESTION")
        self.assertEqual(parser.parse("课堂请注意老师", now=9).intent, "NOTICE")
        self.assertEqual(parser.parse("we have a question", now=10).intent, "QUESTION")
        self.assertEqual(parser.parse("請注意老師", now=13).intent, "NOTICE")
        self.assertEqual(parser.parse("Please pay attention to the teacher", now=16).intent, "NOTICE")

    def test_bare_mode_aliases_route_exact_voice_commands(self):
        aliases = {
            "question": "QUESTION",
            "注意": "NOTICE", "請注意": "NOTICE", "请注意": "NOTICE",
            "Rest": "REST", "Correct": "CORRECT", "Wrong": "WRONG",
            "休息": "REST", "答對": "CORRECT", "答錯": "WRONG",
        }
        for phrase, intent in aliases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(CommandParser(Settings()).parse(phrase, now=100).intent, intent)
        parser = CommandParser(Settings())
        self.assertEqual(parser.parse("I think rest is next", now=1).intent, "REST")
        self.assertEqual(parser.parse("wrong, wrong, wrong", now=4).intent, "WRONG")

    def test_custom_stt_test_text_uses_same_normalization_as_recognition(self):
        self.assertEqual(normalize_phrase(" 課堂  問題！ "), "課堂問題")


class SettingsTests(unittest.TestCase):
    def test_schema_zero_migration_preserves_values_and_writes_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = '{"question_seconds":12,"feedback_seconds":6,"rest_debug_seconds":17}'
            path.write_text(original, encoding="utf-8")
            settings = SettingsRepository(path).load()
            self.assertEqual(settings.question_seconds, 12)
            self.assertEqual(settings.feedback_seconds, 6)
            self.assertEqual(settings.rest_debug_seconds, 17)
            self.assertTrue(settings.debug_rest)
            self.assertEqual(path.with_suffix(".json.v0.bak").read_text(encoding="utf-8"), original)

    def test_cjk_settings_round_trip_stays_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "教室設定.json"
            settings = Settings(command_prefix="課堂", audio_files={
                "standby": "待機.wav", "question": "提問.wav", "correct": "答對.wav",
                "wrong": "答錯.wav", "notice": "注意.wav", "rest": "休息.wav",
                "discussion": "討論.wav",
            }, wled_devices=[{"ip": "192.0.2.50", "mac": "000000000050"}])
            repository = SettingsRepository(path)
            repository.save(settings)
            self.assertIn("課堂", path.read_text(encoding="utf-8"))
            self.assertEqual(repository.load(), settings)

    def test_schema_two_migration_preserves_timers_and_old_light_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = {
                "schema_version": 2,
                "question_seconds": 13,
                "feedback_seconds": 7,
                "rest_seconds": 720,
                "rest_reminder_seconds": 25,
                "colors": {"warm": "#EFA030", "green": "#31CC77",
                           "orange": "#F08020", "red": "#F02030"},
                "periods": {"warm": 5.0, "green": 3.5, "orange": 2.3,
                            "rest_green": 6.0, "rest_warm": 4.0},
            }
            path.write_text(json.dumps(original), encoding="utf-8")
            settings = SettingsRepository(path).load()
            self.assertEqual(settings.question_seconds, 13)
            self.assertEqual(settings.feedback_seconds, 7)
            self.assertEqual(settings.rest_seconds, 720)
            self.assertEqual(settings.rest_reminder_seconds, 25)
            self.assertEqual(settings.colors["warm"], "#EFA030")
            self.assertEqual(settings.colors["question"], "#AD5EFF")
            self.assertEqual(settings.periods["warm"], 5.0)
            self.assertEqual(settings.rest_end_periods["stage_a"], 4.0)
            self.assertTrue(path.with_suffix(".json.v2.bak").is_file())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], SCHEMA_VERSION)

    def test_schema_three_migration_adds_editable_pattern_brightness(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = {
                "schema_version": 3,
                "question_seconds": 14,
                "brightness_percent": 55,
                "colors": Settings().colors,
                "periods": Settings().periods,
                "rest_end_periods": Settings().rest_end_periods,
                "rest_end_stage_seconds": Settings().rest_end_stage_seconds,
                "transition_ms": Settings().transition_ms,
            }
            path.write_text(json.dumps(original), encoding="utf-8")
            settings = SettingsRepository(path).load()
            self.assertEqual(settings.question_seconds, 14)
            self.assertEqual(settings.brightness_percent, 55)
            self.assertEqual(settings.pattern_levels["orange"], [3, 100, 65, 35, 15])
            self.assertTrue(path.with_suffix(".json.v3.bak").is_file())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], SCHEMA_VERSION)


class PatternAndDdpTests(unittest.TestCase):
    def test_all_twelve_patterns_render_capped_8x8_rgb_and_off_is_black(self):
        settings = Settings(brightness_percent=80, gamma_enabled=False)
        for pattern in Pattern:
            with self.subTest(pattern=pattern):
                frame = render(pattern, 0.7, settings, 20)
                self.assertEqual(len(frame), 192)
                self.assertLessEqual(max(frame), 204)
        self.assertEqual(render(Pattern.OFF, 0.7, settings), bytes(192))

    def test_twelve_visuals_have_distinct_color_and_shape_signatures(self):
        settings = Settings(gamma_enabled=False)
        patterns = [pattern for pattern in Pattern if pattern is not Pattern.OFF]
        frames = [render(pattern, 0.42, settings, 7) for pattern in patterns]
        self.assertEqual(len(set(frames)), 12)
        masks = [tuple(i for i in range(64) if any(frame[i * 3:i * 3 + 3]))
                 for frame in frames]
        self.assertEqual(len(set(masks)), 12)
        self.assertEqual(len(set(settings.colors.values())), 12)

    def test_response_glyphs_stay_clear_but_noise_has_no_warning_symbol(self):
        settings = Settings(brightness_percent=80, gamma_enabled=False)
        lit = lambda frame: {index for index in range(64) if any(frame[index * 3:index * 3 + 3])}
        exclamation = {y * 8 + x for y in (0, 1, 2, 3, 4, 6) for x in (3, 4)}
        circle = {y * 8 + x for y, xs in (
            (0, (2, 3, 4, 5)), (1, (1, 6)), (2, (0, 7)), (3, (0, 7)),
            (4, (0, 7)), (5, (0, 7)), (6, (1, 6)), (7, (2, 3, 4, 5))) for x in xs}
        wrong_x = {y * 8 + x for y in range(8) for x in (y, 7 - y)}
        self.assertNotEqual(lit(render(Pattern.RED_EXCLAMATION, 0, settings)), exclamation)
        self.assertEqual(lit(render(Pattern.CORRECT_SQUARE, 1.0, settings)), circle)
        self.assertEqual(lit(render(Pattern.WRONG_X, 0.8, settings)), wrong_x)

    def test_comets_move_and_rest_end_speeds_up_across_stages(self):
        settings = Settings(brightness_percent=80, gamma_enabled=False)
        self.assertNotEqual(render(Pattern.ORANGE_ROTATE, 0, settings),
                            render(Pattern.ORANGE_ROTATE, 0.2, settings))
        slow = render(Pattern.REST_END_WARM_BREATH, 0.75, settings, 15)
        medium = render(Pattern.REST_END_WARM_BREATH, 0.75, settings, 7)
        fast = render(Pattern.REST_END_WARM_BREATH, 0.75, settings, 3)
        self.assertNotEqual(slow, medium)
        self.assertNotEqual(medium, fast)

    def test_fixed_noise_animation_ignores_legacy_flash_controls(self):
        settings = Settings(brightness_percent=60, gamma_enabled=False)
        legacy = Settings(brightness_percent=60, gamma_enabled=False, red_flash_hz=1.8)
        for pattern in (Pattern.ORANGE_ROTATE, Pattern.RED_EXCLAMATION,
                        Pattern.DISCUSSION_RED_BORDER):
            self.assertEqual(render(pattern, 0.4, settings), render(pattern, 0.4, legacy))
        warm_settings = Settings(pattern_levels={**settings.pattern_levels, "warm": [10, 20]},
                                 gamma_enabled=False)
        low=render(Pattern.WARM_BREATH,0,warm_settings)
        high=render(Pattern.WARM_BREATH,3,warm_settings)
        self.assertGreater(max(high),max(low))
        self.assertLessEqual(max(low),16)
        self.assertLessEqual(max(high),31)

    def test_crossfade_and_transition_rules_are_bounded(self):
        settings = Settings()
        self.assertEqual(crossfade(bytes(192), bytes([100]) * 192, 0.5), bytes([50]) * 192)
        self.assertEqual(transition_duration(Pattern.GREEN_BREATH, Pattern.ORANGE_ROTATE, settings), 0.25)
        self.assertEqual(transition_duration(Pattern.ORANGE_ROTATE, Pattern.RED_EXCLAMATION, settings), 0.15)
        self.assertEqual(transition_duration(Pattern.RED_EXCLAMATION, Pattern.OFF, settings), 0.15)
        self.assertEqual(transition_duration(Pattern.QUESTION_GREEN_ROTATE, Pattern.WRONG_X, settings), 0.25)

    def test_ddp_header_and_board_orientation(self):
        frame = bytes(range(192))
        packet = make_ddp_packet(frame, sequence=7)
        self.assertEqual(packet[:10], bytes((0x41, 7, 0x0B, 1, 0, 0, 0, 0, 0, 192)))
        self.assertEqual(packet[10:], frame)
        self.assertEqual(orient_frame(frame), frame)
        snake = orient_frame(frame, serpentine=True)
        expected_row = b"".join(frame[i * 3:i * 3 + 3] for i in range(15, 7, -1))
        self.assertEqual(snake[8 * 3:16 * 3], expected_row)
        self.assertEqual(WledDevice("192.0.2.50", "panel", "000000000050", "16.0.1", 64).rotation, 0)

    def test_eight_udp_receivers_receive_the_same_ddp_frame(self):
        receivers = []
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for _ in range(8):
                receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                receiver.bind(("127.0.0.1", 0))
                receiver.settimeout(1.0)
                receivers.append(receiver)
            frame = bytes((30, 60, 90)) * 64
            packet = make_ddp_packet(frame)
            for receiver in receivers:
                sender.sendto(packet, receiver.getsockname())
            for receiver in receivers:
                data, _address = receiver.recvfrom(2048)
                self.assertEqual(data[10:], frame)
        finally:
            sender.close()
            for receiver in receivers:
                receiver.close()


if __name__ == "__main__":
    unittest.main()
