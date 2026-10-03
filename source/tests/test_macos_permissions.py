"""Exercise the macOS permission lifecycle without accessing any microphone."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import classroom_app  # Initialize shared OpenGL contexts before QApplication.
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from classroom_core import SettingsRepository
from classroom_service import ClassroomService

APP = QApplication.instance() or QApplication([])


class MacMicrophoneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.service = ClassroomService(SettingsRepository(root / 'settings.json'), root, start_io=False)
        self.service.start_io = True
        self.runtime = self.enterContext(patch('classroom_service.AudioRuntime'))
        self.app = MagicMock()
        self.enterContext(patch('classroom_service.QCoreApplication.instance', return_value=self.app))
        self.enterContext(patch('classroom_service.sys.platform', 'darwin'))
        self.enterContext(patch('classroom_service.sys.frozen', True, create=True))

    def tearDown(self):
        self.service._timer.stop()
        self.service.deleteLater()
        self.temporary.cleanup()

    def test_granted_starts_once(self):
        self.app.checkPermission.return_value = Qt.PermissionStatus.Granted
        self.service._start_audio()
        self.service._start_audio()
        self.runtime.assert_called_once()
        self.runtime.return_value.start.assert_called_once()
        self.app.requestPermission.assert_not_called()

    def test_pending_defers_and_granted_callback_starts(self):
        self.app.checkPermission.return_value = Qt.PermissionStatus.Undetermined
        self.service._start_audio()
        self.service._start_audio()
        self.app.requestPermission.assert_called_once()
        self.runtime.assert_not_called()
        self.assertTrue(self.service._microphone_permission_pending)
        self.app.checkPermission.return_value = Qt.PermissionStatus.Granted
        callback = self.app.requestPermission.call_args.args[2]
        callback(MagicMock())
        self.runtime.return_value.start.assert_called_once()
        self.assertFalse(self.service._microphone_permission_pending)

    def test_denied_does_not_open_audio_or_prompt_again(self):
        self.app.checkPermission.return_value = Qt.PermissionStatus.Denied
        self.service._start_audio()
        self.service._start_audio()
        self.runtime.assert_not_called()
        self.app.requestPermission.assert_not_called()
        self.assertIn('系統設定', self.service._microphone['message'])

    def test_denied_callback_keeps_audio_stopped(self):
        self.app.checkPermission.return_value = Qt.PermissionStatus.Undetermined
        self.service._start_audio()
        self.app.checkPermission.return_value = Qt.PermissionStatus.Denied
        self.app.requestPermission.call_args.args[2](MagicMock())
        self.runtime.assert_not_called()
        self.assertFalse(self.service._microphone_permission_pending)

    def test_close_while_pending_never_starts_audio(self):
        self.app.checkPermission.return_value = Qt.PermissionStatus.Undetermined
        self.service._start_audio()
        self.service._closing = True
        self.app.checkPermission.return_value = Qt.PermissionStatus.Granted
        self.app.requestPermission.call_args.args[2](MagicMock())
        self.runtime.assert_not_called()

    def test_source_macos_does_not_use_bundle_only_qt_api(self):
        with patch('classroom_service.sys.frozen', False):
            self.service._start_audio()
        self.app.checkPermission.assert_not_called()
        self.runtime.return_value.start.assert_called_once()

    def test_windows_does_not_use_macos_gate(self):
        with patch('classroom_service.sys.platform', 'win32'):
            self.service._start_audio()
        self.app.checkPermission.assert_not_called()
        self.runtime.return_value.start.assert_called_once()

    def test_io_disabled_smoke_test_never_requests_permission(self):
        self.service.start_io = False
        self.service._start_audio()
        self.app.checkPermission.assert_not_called()
        self.runtime.assert_not_called()
