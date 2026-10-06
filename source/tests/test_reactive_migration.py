import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from classroom_core import Settings, SettingsRepository, SCHEMA_VERSION


class ReactiveMigrationTests(unittest.TestCase):
    def test_old_defaults_retune_but_user_timers_aliases_audio_and_calibration_survive(self):
        data = {'schema_version':12, **asdict(Settings(rest_seconds=147, question_seconds=8))}
        data.pop('audio_reactive_modes')
        data.update(noise_rising_db=20, noise_loud_db=36, noise_rising_exit_db=12,
                    noise_loud_exit_db=24, noise_rising_enter_seconds=3,
                    noise_loud_enter_seconds=5, noise_exit_seconds=4, noise_smoothing_ms=1200)
        data['discussion_noise']={'rising_db':32, 'loud_db':48, 'rising_exit_db':22, 'loud_exit_db':34}
        data['audio_files']['rest']=''
        data['command_aliases']['REST']=['my break']
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            original=json.dumps(data,ensure_ascii=False)
            path.write_text(original,encoding='utf-8')
            settings=SettingsRepository(path).load()
            self.assertEqual(path.with_suffix('.json.v12.bak').read_text(encoding='utf-8'),original)
            self.assertEqual(settings.noise_rising_db,8)
            self.assertEqual(settings.noise_loud_db,15)
            self.assertEqual(settings.noise_rising_exit_db,5)
            self.assertEqual(settings.noise_loud_exit_db,14)
            self.assertEqual(settings.discussion_noise, {
                'rising_db':12, 'loud_db':19, 'rising_exit_db':9, 'loud_exit_db':17,
            })
            self.assertEqual(settings.rest_seconds,147)
            self.assertEqual(settings.command_aliases['REST'],['my break'])
            self.assertEqual(settings.audio_files['rest'],'')
            self.assertFalse(any(settings.audio_reactive_modes.values()))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['schema_version'],SCHEMA_VERSION)

    def test_custom_thresholds_and_music_switches_are_not_replaced(self):
        custom=Settings(noise_rising_db=21,noise_loud_db=36,
                        audio_reactive_modes={**Settings().audio_reactive_modes,'question':True})
        data={'schema_version':12,**asdict(custom)}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            path.write_text(json.dumps(data),encoding='utf-8')
            result=SettingsRepository(path).load()
        self.assertEqual(result.noise_rising_db,21)
        self.assertEqual(result.noise_loud_db,36)
        self.assertTrue(result.audio_reactive_modes['question'])

    def test_schema_13_keeps_custom_profiles_and_unrelated_settings(self):
        settings = Settings(
            noise_rising_db=19, noise_loud_db=31,
            noise_rising_exit_db=11, noise_loud_exit_db=21,
            discussion_noise={'rising_db':27, 'loud_db':39,
                              'rising_exit_db':19, 'loud_exit_db':29},
            noise_rising_enter_seconds=2.4, noise_loud_enter_seconds=3.2,
            noise_exit_seconds=4.1, noise_smoothing_ms=1400,
            command_prefix='老師', command_aliases={**Settings().command_aliases,
                                                   'NOTICE':['老師請注意']},
            noise_baseline_dbfs=-54, speech_noise_guard=False,
            audio_files={**Settings().audio_files, 'notice':'audio/notice/custom.wav'},
            audio_reactive_modes={**Settings().audio_reactive_modes, 'notice':True},
        )
        data = {'schema_version':13, **asdict(settings)}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            original=json.dumps(data,ensure_ascii=False)
            path.write_text(original,encoding='utf-8')
            result=SettingsRepository(path).load()
            self.assertEqual(path.with_suffix('.json.v13.bak').read_text(encoding='utf-8'), original)

        self.assertEqual((result.noise_rising_db, result.noise_loud_db,
                          result.noise_rising_exit_db, result.noise_loud_exit_db),
                         (19,31,11,21))
        self.assertEqual(result.discussion_noise, settings.discussion_noise)
        self.assertEqual((result.noise_rising_enter_seconds, result.noise_loud_enter_seconds,
                          result.noise_exit_seconds, result.noise_smoothing_ms),
                         (2.4,3.2,4.1,1400))
        self.assertEqual(result.command_prefix, '老師')
        self.assertEqual(result.command_aliases['NOTICE'], ['老師請注意'])
        self.assertEqual(result.noise_baseline_dbfs, -54)
        self.assertFalse(result.speech_noise_guard)
        self.assertEqual(result.audio_files['notice'], 'audio/notice/custom.wav')
        self.assertTrue(result.audio_reactive_modes['notice'])

    def test_schema_13_migrates_only_whole_legacy_or_previous_default_profiles(self):
        previous_notice = {'noise_rising_db':18, 'noise_loud_db':30,
                           'noise_rising_exit_db':12, 'noise_loud_exit_db':22}
        legacy_notice = {'noise_rising_db':20, 'noise_loud_db':36,
                         'noise_rising_exit_db':12, 'noise_loud_exit_db':24}
        previous_discussion = {'rising_db':26, 'loud_db':38,
                               'rising_exit_db':18, 'loud_exit_db':28}
        schema_14_discussion = {'rising_db':16, 'loud_db':26,
                                'rising_exit_db':12, 'loud_exit_db':22}
        legacy_discussion = {'rising_db':32, 'loud_db':48,
                             'rising_exit_db':22, 'loud_exit_db':34}
        with tempfile.TemporaryDirectory() as directory:
            for index, (notice, discussion) in enumerate((
                    (legacy_notice, previous_discussion),
                    (previous_notice, legacy_discussion),
                    (legacy_notice, legacy_discussion),
                    (legacy_notice, schema_14_discussion),
                    (previous_notice, previous_discussion))):
                with self.subTest(notice=notice, discussion=discussion):
                    path=Path(directory)/f'settings{index}.json'
                    data={'schema_version':13, **asdict(Settings())}
                    data.update(notice)
                    data['discussion_noise']=discussion
                    path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
                    result=SettingsRepository(path).load()
                    self.assertEqual((result.noise_rising_db, result.noise_loud_db,
                                      result.noise_rising_exit_db, result.noise_loud_exit_db),
                                     (8,15,5,14))
                    self.assertEqual(result.discussion_noise, {
                        'rising_db':12, 'loud_db':19, 'rising_exit_db':9, 'loud_exit_db':17,
                    })

    def test_schema_14_migrates_exact_default_and_preserves_backup_and_other_settings(self):
        settings = Settings(
            rest_seconds=147, question_seconds=8,
            noise_rising_db=11, noise_loud_db=19,
            noise_rising_exit_db=7, noise_loud_exit_db=15,
            noise_rising_enter_seconds=2.4, noise_loud_enter_seconds=3.2,
            noise_exit_seconds=4.1, noise_smoothing_ms=1400,
            discussion_noise={'rising_db':16, 'loud_db':26,
                              'rising_exit_db':12, 'loud_exit_db':22},
            command_prefix='老師', command_aliases={**Settings().command_aliases,
                                                   'DISCUSSION':['班級討論']},
            noise_baseline_dbfs=-54, speech_noise_guard=False,
            audio_files={**Settings().audio_files, 'discussion':'audio/discussion/custom.wav'},
        )
        data = {'schema_version':14, **asdict(settings)}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            original=json.dumps(data,ensure_ascii=False)
            path.write_text(original,encoding='utf-8')
            repository=SettingsRepository(path)
            result=repository.load()
            backup=path.with_suffix('.json.v14.bak')
            self.assertEqual(backup.read_text(encoding='utf-8'), original)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['schema_version'], SCHEMA_VERSION)
            self.assertEqual(repository.load(), result)

        self.assertEqual(result.discussion_noise,
                         {'rising_db':12, 'loud_db':19, 'rising_exit_db':9, 'loud_exit_db':17})
        self.assertEqual((result.rest_seconds, result.question_seconds), (147, 8))
        self.assertEqual((result.noise_rising_db, result.noise_loud_db,
                          result.noise_rising_exit_db, result.noise_loud_exit_db), (11, 19, 7, 15))
        self.assertEqual((result.noise_rising_enter_seconds, result.noise_loud_enter_seconds,
                          result.noise_exit_seconds, result.noise_smoothing_ms), (2.4, 3.2, 4.1, 1400))
        self.assertEqual(result.command_prefix, '老師')
        self.assertEqual(result.command_aliases['DISCUSSION'], ['班級討論'])
        self.assertEqual(result.noise_baseline_dbfs, -54)
        self.assertFalse(result.speech_noise_guard)
        self.assertEqual(result.audio_files['discussion'], 'audio/discussion/custom.wav')

    def test_schema_14_keeps_custom_discussion_profile(self):
        custom = {'rising_db':12, 'loud_db':23,
                  'rising_exit_db':9, 'loud_exit_db':17}
        data = {'schema_version':14, **asdict(Settings(discussion_noise=custom))}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            path.write_text(json.dumps(data),encoding='utf-8')
            result=SettingsRepository(path).load()
        self.assertEqual(result.discussion_noise, custom)

    def test_schema_15_lowers_only_untouched_loud_thresholds(self):
        data = {'schema_version':15, **asdict(Settings())}
        data['noise_loud_db'] = 18
        data['discussion_noise']['loud_db'] = 22
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            path.write_text(json.dumps(data),encoding='utf-8')
            result=SettingsRepository(path).load()
            self.assertTrue(path.with_suffix('.json.v15.bak').is_file())
        self.assertEqual((result.noise_loud_db, result.discussion_noise['loud_db']), (15, 19))
        custom = Settings(noise_loud_db=21,
                          discussion_noise={'rising_db':12, 'loud_db':24,
                                            'rising_exit_db':9, 'loud_exit_db':17})
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            path.write_text(json.dumps({'schema_version':15, **asdict(custom)}),encoding='utf-8')
            result=SettingsRepository(path).load()
        self.assertEqual((result.noise_loud_db, result.discussion_noise['loud_db']), (21, 24))

    def test_schema_13_and_14_keep_old_discussion_default_when_notice_is_higher(self):
        old_discussion = {'rising_db':16, 'loud_db':26,
                          'rising_exit_db':12, 'loud_exit_db':22}
        notice = {'noise_rising_db':14, 'noise_loud_db':24,
                  'noise_rising_exit_db':11, 'noise_loud_exit_db':20}
        with tempfile.TemporaryDirectory() as directory:
            for version in (13, 14):
                with self.subTest(schema_version=version):
                    path=Path(directory)/f'settings{version}.json'
                    data={'schema_version':version, **asdict(Settings())}
                    data.update(notice)
                    data['discussion_noise']=old_discussion
                    path.write_text(json.dumps(data),encoding='utf-8')
                    result=SettingsRepository(path).load()

                    self.assertEqual((result.noise_rising_db, result.noise_loud_db), (14, 24))
                    self.assertEqual(result.discussion_noise, old_discussion)
