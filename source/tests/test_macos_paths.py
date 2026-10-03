"""Resource lookup is independent of Finder's working directory."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import classroom_resources as resources


class MacResourcePathsTests(unittest.TestCase):
    def test_bundle_resources_win_over_framework_meipass(self):
        with tempfile.TemporaryDirectory(prefix='Classroom 中文 ') as directory:
            contents = Path(directory) / 'ResponsiveClassroom.app/Contents'
            bundle = contents / 'Resources'
            (bundle / 'models').mkdir(parents=True)
            with (patch.object(resources.sys, 'frozen', True, create=True),
                  patch.object(resources.sys, '_MEIPASS', str(contents / 'Frameworks'), create=True),
                  patch.object(resources.sys, 'executable', str(contents / 'MacOS/ResponsiveClassroom'))):
                # Windows temporary paths may use an 8.3 alias; the runtime
                # intentionally resolves the executable to its canonical path.
                self.assertEqual(resources.application_root(), bundle.resolve())

    def test_non_bundle_frozen_layout_preserves_meipass_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            extracted = Path(directory) / '_internal'
            with (patch.object(resources.sys, 'frozen', True, create=True),
                  patch.object(resources.sys, '_MEIPASS', str(extracted), create=True),
                  patch.object(resources.sys, 'executable', str(Path(directory) / 'ResponsiveClassroom'))):
                self.assertEqual(resources.application_root(), extracted)

    def test_source_resources_do_not_depend_on_working_directory(self):
        with patch.object(resources.sys, 'frozen', False, create=True):
            self.assertEqual(resources.application_root(), Path(resources.__file__).resolve().parent / 'resources')

    def test_mac_settings_live_outside_the_bundle(self):
        with (patch.object(resources.sys, 'platform', 'darwin'),
              patch.object(resources, 'os', SimpleNamespace(name='posix', environ={})),
              patch.object(Path, 'home', return_value=Path('/test-home'))):
            self.assertEqual(resources.config_dir(), Path('/test-home/Library/Application Support/Responsive Classroom'))


class NativeSetupTests(unittest.TestCase):
    def test_mac_setup_requires_binary_wheels(self):
        from tools import setup_environment
        with (patch.object(setup_environment.sys, 'argv', ['setup_environment.py']),
              patch.object(setup_environment.sys, 'platform', 'darwin'),
              patch.object(Path, 'is_file', return_value=True),
              patch.object(setup_environment.subprocess, 'run') as run):
            setup_environment.main()
        self.assertIn('--only-binary=:all:', run.call_args.args[0])
        self.assertTrue(run.call_args.args[0][0].replace('\\', '/').endswith('.venv/bin/python'))

    def test_windows_install_command_is_preserved(self):
        from tools import setup_environment
        with (patch.object(setup_environment.sys, 'argv', ['setup_environment.py']),
              patch.object(setup_environment.sys, 'platform', 'win32'),
              patch.object(Path, 'is_file', return_value=True),
              patch.object(setup_environment.subprocess, 'run') as run):
            setup_environment.main()
        self.assertNotIn('--only-binary=:all:', run.call_args.args[0])
        self.assertTrue(run.call_args.args[0][0].replace('\\', '/').endswith('.venv/Scripts/python.exe'))
