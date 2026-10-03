import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from classroom_app import ui_smoke_report


class WebUiTests(unittest.TestCase):
    def test_real_local_web_controls_languages_layout_and_wheel_guard(self):
        evidence=Path(__file__).resolve().parents[2]/'build/reports/source-web-ui'
        report_path=evidence/'source-web-ui.json'
        try:
            report=ui_smoke_report(report_path,captures=True)
        except RuntimeError:
            if report_path.is_file():
                self.fail(report_path.read_text(encoding='utf-8'))
            raise
        self.assertTrue(report['passed'],json.dumps(report,ensure_ascii=False))
        self.assertTrue(all(item['actual_viewport']['width'] == item['width'] and
                            item['actual_viewport']['height'] == item['height']
                            for item in report['layouts'] if 'requested_width' in item))
        self.assertEqual(report['audio_fields'],7)
        self.assertEqual(report['audio_decoders'],{'wav':True,'mp3':True})
        self.assertTrue(report['rest_music']['loaded'])
        self.assertGreaterEqual(report['disclosures'],5)


if __name__=='__main__':
    unittest.main()
