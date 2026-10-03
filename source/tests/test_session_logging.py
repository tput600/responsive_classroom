import csv
import tempfile
import time
import unittest
from pathlib import Path

from classroom_logging import SessionLogger


class SessionLoggingTests(unittest.TestCase):
    def test_rows_are_throttled_and_state_or_accepted_command_is_immediate(self):
        with tempfile.TemporaryDirectory() as temp:
            log = SessionLogger(temp)
            values = dict(mode='STANDBY', mode_source='manual', raw_rms=.1,
                          dbfs=-20, smoothed_dbfs=-22, noise_state='quiet',
                          voice_command='', pattern='solid')
            log.write(**values)
            log.write(**values)
            changed = values | {'noise_state': 'loud'}
            log.write(**changed)
            command = changed | {'voice_command': '切換休息'}
            log.write(**command)
            log.close()
            with log.path.open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[-1]['voice_command'], '切換休息')
            self.assertEqual(list(rows[0]), list(SessionLogger.columns))

    def test_routine_row_resumes_after_one_second_and_close_flushes(self):
        with tempfile.TemporaryDirectory() as temp:
            log = SessionLogger(temp)
            values = dict(mode='STANDBY', mode_source='manual', raw_rms=.1,
                          dbfs=-20, smoothed_dbfs=-22, noise_state='quiet',
                          voice_command=None, pattern='solid')
            log.write(**values)
            log.write(**(values | {'raw_rms': .2}))
            time.sleep(1.02)
            log.write(**(values | {'raw_rms': .3}))
            path = log.path
            log.close()
            with path.open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[-1]['raw_rms'], '0.3')

    def test_disabled_logger_creates_no_file_and_write_is_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            log = SessionLogger(temp, enabled=False)
            log.write(mode='x', mode_source='x', raw_rms=None, dbfs=None,
                      smoothed_dbfs=None, noise_state='x', voice_command='', pattern='x')
            log.close()
            self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
