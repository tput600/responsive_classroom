import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from classroom_app import ui_smoke_report


class WebUiTests(unittest.TestCase):
    def test_real_local_web_controls_languages_layout_and_wheel_guard(self):
        evidence=Path(__file__).resolve().parents[2]/'.codex/review/2026-10-03/web-ui'
        report=ui_smoke_report(evidence/'source-web-ui.json',captures=True)
        self.assertTrue(report['passed'],json.dumps(report,ensure_ascii=False))
        self.assertEqual(report['audio_fields'],7)
        self.assertEqual(report['audio_decoders'],{'wav':True,'mp3':True})
        self.assertTrue(report['rest_music']['loaded'])
        self.assertGreaterEqual(report['disclosures'],5)


if __name__=='__main__':
    unittest.main()
