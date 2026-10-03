import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from classroom_core import SCHEMA_VERSION, Settings, SettingsRepository


class SettingsRecoveryTests(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            original = json.dumps(data, ensure_ascii=False)
            path.write_text(original, encoding='utf-8')
            settings = SettingsRepository(path).load()
            self.assertEqual(path.read_text(encoding='utf-8'), original)
            return settings

    def test_non_object_json_and_invalid_schema_use_defaults_without_overwrite(self):
        for value in (None, 'bad', 42, [], True):
            with self.subTest(value=value):
                self.assertEqual(self.load(value), Settings())
        for value in (True, 15.0, '15', None, [], {}):
            with self.subTest(schema=value):
                self.assertEqual(self.load({'schema_version': value}), Settings())

    def test_invalid_nested_mapping_keeps_unrelated_current_preferences(self):
        for field, value in (
                ('rest_end_periods', ['stage_a', 'stage_b', 'stage_c']),
                ('rest_end_stage_seconds', ['stage_a', 'stage_b']),
                ('calibration', None), ('language', []), ('rotation_direction', {})):
            with self.subTest(field=field):
                data = {'schema_version': SCHEMA_VERSION, **asdict(Settings(
                    rest_seconds=777, question_seconds=23, language='en_US',
                    noise_rising_db=30, noise_loud_db=40,
                    audio_files={**Settings().audio_files, 'rest': ''}))}
                data[field] = value
                result = self.load(data)
                self.assertEqual(result.rest_seconds, 777)
                self.assertEqual(result.question_seconds, 23)
                self.assertEqual(result.noise_rising_db, 30)
                self.assertEqual(result.noise_loud_db, 40)
                self.assertEqual(result.audio_files['rest'], '')
                self.assertEqual(getattr(result, field), getattr(Settings(), field))

    def test_invalid_optional_numbers_and_boolean_integers_are_rejected(self):
        for name in ('noise_baseline_dbfs', 'spl_calibration_offset_db'):
            for value in (True, '0', [], float('nan'), float('inf'), float('-inf')):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    Settings(**{name: value})
        for name in ('brightness_percent', 'minimum_transition_ms', 'default_fade_ms',
                     'audio_volume_percent', 'audio_fade_ms'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                Settings(**{name: True})

    def test_all_scalar_numeric_settings_reject_non_finite_values(self):
        for name, default in asdict(Settings()).items():
            if type(default) not in (int, float):
                continue
            for value in (float('nan'), float('inf'), float('-inf')):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    Settings(**{name: value})

    def test_nested_numeric_settings_reject_non_finite_values(self):
        defaults = asdict(Settings())
        for name in ('calibration', 'discussion_noise', 'periods', 'rest_end_periods',
                     'rest_end_stage_seconds', 'transition_ms'):
            for key, default in defaults[name].items():
                if type(default) not in (int, float) and default is not None:
                    continue
                for value in (float('nan'), float('inf'), float('-inf')):
                    with self.subTest(name=name, key=key, value=value), self.assertRaises(ValueError):
                        Settings(**{name: {**defaults[name], key: value}})

    def test_invalid_device_fields_are_rejected_without_losing_timers(self):
        base = {'ip': '192.0.2.50', 'mac': 'test', 'name': 'panel',
                'version': '1', 'led_count': 64}
        for field, value in (('ip', '::1'), ('rotation', 45), ('rotation', True),
                             ('led_count', '64'), ('led_count', 0),
                             ('mirror_x', 'false'), ('mac', None)):
            with self.subTest(field=field):
                board = {**base, field: value}
                with self.assertRaises(ValueError):
                    Settings(wled_devices=[board])
                result = self.load({'schema_version': SCHEMA_VERSION,
                                    'rest_seconds': 777, 'wled_devices': [board]})
                self.assertEqual(result.rest_seconds, 777)
                self.assertEqual(result.wled_devices, [])

    def test_valid_cross_field_tuning_is_not_reset_during_recovery(self):
        result = self.load({'schema_version': SCHEMA_VERSION, 'question_seconds': -1,
                            'max_flash_hz': .5, 'red_flash_hz': .4,
                            'noise_rising_db': 50, 'noise_loud_db': 60})
        self.assertEqual((result.max_flash_hz, result.red_flash_hz), (.5, .4))
        self.assertEqual((result.noise_rising_db, result.noise_loud_db), (50, 60))


if __name__ == '__main__':
    unittest.main()
