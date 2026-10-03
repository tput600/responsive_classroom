"""Release layout/ZIP regressions that do not require macOS or PySide6."""
import json
import os
from pathlib import Path
import plistlib
import stat
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

from tools import build_release
from tools.macos_package import (
    MANIFEST, safe_path, stage_release, tree_records, verify_archive, verify_tree, write_archive,
)


@unittest.skipIf(os.name == 'nt', 'macOS symlink/mode tests require a POSIX filesystem')
class MacOSPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='release 測試 ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / 'dist/ResponsiveClassroom.app'
        self.binary = self.bundle / 'Contents/MacOS/ResponsiveClassroom'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'fake executable\x00')
        self.binary.chmod(0o755)
        framework = self.bundle / 'Contents/Frameworks/QtCore.framework'
        (framework / 'Versions/A/Resources').mkdir(parents=True)
        (framework / 'Versions/A/QtCore').write_bytes(b'fake framework')
        (framework / 'Versions/A/QtCore').chmod(0o755)
        (framework / 'Versions/Current').symlink_to('A', target_is_directory=True)
        (framework / 'QtCore').symlink_to('Versions/Current/QtCore')
        (framework / 'Resources').symlink_to('Versions/Current/Resources', target_is_directory=True)
        self.extras = []
        for name in ('README.md', 'README.en.md', 'MACOS.md', 'LICENSE'):
            path = self.root / name
            path.write_text(name, encoding='utf-8')
            self.extras.append(path)
        self.release = self.root / 'release'
        self.archive = self.root / 'candidate.zip'
        self.metadata = dict(version='3.0.2', architecture='arm64')

    def stage(self):
        return stage_release(self.bundle, self.release, self.extras,
                             **self.metadata, python_version='3.12.14')

    def build(self):
        self.stage()
        write_archive(self.release, self.archive)

    def rewrite_entry(self, name, *, data=None, transform=None):
        with zipfile.ZipFile(self.archive) as source:
            entries = [(info, source.read(info)) for info in source.infolist()]
        with zipfile.ZipFile(self.archive, 'w') as output:
            for info, value in entries:
                if info.filename == name:
                    if transform:
                        transform(info)
                    if data is not None:
                        value = data
                output.writestr(info, value)

    def test_staging_never_adds_extras_to_sealed_bundle(self):
        before = tree_records(self.bundle)
        staged_bundle = self.stage()
        self.assertEqual(tree_records(self.bundle), before)
        self.assertEqual(tree_records(staged_bundle), before)
        self.assertFalse((staged_bundle / MANIFEST).exists())
        self.assertTrue((self.release / MANIFEST).is_file())
        for extra in self.extras:
            self.assertTrue((self.release / extra.name).is_file())
            self.assertFalse((staged_bundle / extra.name).exists())

    def test_archive_preserves_links_executable_modes_empty_directories_and_app_root(self):
        self.build()
        result = verify_archive(self.release, self.archive, **self.metadata)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['symlinks'], 3)
        with zipfile.ZipFile(self.archive) as zipped:
            base = 'ResponsiveClassroom.app/Contents/'
            info = zipped.getinfo(base + 'MacOS/ResponsiveClassroom')
            self.assertEqual(stat.S_IMODE(info.external_attr >> 16), 0o755)
            link = base + 'Frameworks/QtCore.framework/Versions/Current'
            self.assertTrue(stat.S_ISLNK(zipped.getinfo(link).external_attr >> 16))
            self.assertEqual(zipped.read(link), b'A')
            self.assertIn(base + 'Frameworks/QtCore.framework/Versions/A/Resources/', zipped.namelist())
            self.assertFalse(any('/Versions/Current/' in name for name in zipped.namelist()))

    def test_rejects_external_absolute_broken_and_cyclic_symlinks(self):
        outside = self.root / 'outside'
        outside.write_text('private', encoding='utf-8')
        link = self.bundle / 'bad-link'
        for target in ('../../outside', str(outside), 'missing', 'bad-link'):
            with self.subTest(target=target):
                link.symlink_to(target)
                try:
                    with self.assertRaises(ValueError):
                        self.stage()
                finally:
                    link.unlink()

    def test_rejects_unsupported_architecture(self):
        self.metadata['architecture'] = 'universal2'
        with self.assertRaisesRegex(ValueError, 'architecture'):
            self.stage()

    def test_manifest_detects_content_changes(self):
        self.stage()
        (self.release / 'README.md').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'tree differs'):
            verify_tree(self.release, **self.metadata)

    def test_manifest_detects_executable_mode_changes(self):
        bundle = self.stage()
        (bundle / 'Contents/MacOS/ResponsiveClassroom').chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'tree differs'):
            verify_tree(self.release, **self.metadata)

    def test_manifest_detects_extra_and_missing_entries(self):
        self.stage()
        (self.release / 'unexpected').write_text('extra', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'tree differs'):
            verify_tree(self.release, **self.metadata)
        (self.release / 'unexpected').unlink()
        (self.release / 'README.md').unlink()
        with self.assertRaisesRegex(ValueError, 'tree differs'):
            verify_tree(self.release, **self.metadata)

    def test_archive_detects_payload_tampering_even_with_valid_crc(self):
        self.build()
        self.rewrite_entry('README.md', data=b'tampered')
        with self.assertRaisesRegex(ValueError, 'ZIP content differs'):
            verify_archive(self.release, self.archive, **self.metadata)

    def test_archive_detects_flattened_symlink(self):
        self.build()
        link = 'ResponsiveClassroom.app/Contents/Frameworks/QtCore.framework/QtCore'
        self.rewrite_entry(link, transform=lambda info: setattr(
            info, 'external_attr', (stat.S_IFREG | 0o777) << 16))
        with self.assertRaisesRegex(ValueError, 'type/permissions'):
            verify_archive(self.release, self.archive, **self.metadata)

    def test_archive_detects_duplicate_entries(self):
        self.build()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.archive, 'a') as zipped:
                zipped.writestr('README.md', b'duplicate')
        with self.assertRaisesRegex(ValueError, 'duplicates'):
            verify_archive(self.release, self.archive, **self.metadata)

    def test_archive_rejects_unsafe_entry(self):
        self.build()
        with zipfile.ZipFile(self.archive, 'a') as zipped:
            zipped.writestr('../outside', b'bad')
        with self.assertRaisesRegex(ValueError, 'Unsafe package path'):
            verify_archive(self.release, self.archive, **self.metadata)

    def test_manifest_version_and_platform_are_checked(self):
        self.stage()
        for metadata in (dict(version='3.0.3', architecture='arm64'),
                         dict(version='3.0.2', architecture='x86_64')):
            with self.subTest(metadata=metadata), self.assertRaisesRegex(ValueError, 'mismatch'):
                verify_tree(self.release, **metadata)

    def test_tree_manifest_cannot_be_replaced_by_symlink(self):
        self.stage()
        manifest = self.release / MANIFEST
        manifest.rename(self.root / 'manifest-copy')
        manifest.symlink_to('../manifest-copy')
        with self.assertRaisesRegex(ValueError, 'regular file'):
            verify_tree(self.release, **self.metadata)


class PackagePathTests(unittest.TestCase):
    def test_safe_names(self):
        self.assertEqual(str(safe_path('ResponsiveClassroom.app/Contents/測試')), 'ResponsiveClassroom.app/Contents/測試')
        for path in ('/outside', '../outside', 'a/../b', 'a//b', './a', 'a\\b', '', 'a\x00b'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_path(path)

    def test_windows_smoke_check_retains_executable_path_and_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / 'ResponsiveClassroom'
            package.mkdir()
            (package / 'ResponsiveClassroom.exe').write_bytes(b'test')
            reports = root / 'reports'
            reports.mkdir()

            def fake_run(arguments, **options):
                self.assertEqual(Path(arguments[0]).name, 'ResponsiveClassroom.exe')
                report = ({'passed': True} if arguments[1] == '--ui-smoke-report' else
                          {'model_assets': {'model': True}, 'silent_audio_empty': True,
                           'text_parser': {'command': True}, 'synthetic_voice_available': False})
                Path(arguments[2]).write_text(json.dumps(report), encoding='utf-8')

            with mock.patch.object(build_release, 'run', side_effect=fake_run):
                build_release.frozen_checks(package, reports, windows=True)
            self.assertTrue((reports / 'frozen-model.json').is_file())
            self.assertTrue((reports / 'frozen-ui.json').is_file())


class MacOSBundleChecksTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.bundle = Path(self.temporary.name) / 'ResponsiveClassroom.app'
        (self.bundle / 'Contents/Frameworks/PySide6').mkdir(parents=True)
        self.qtcore = self.bundle / 'Contents/Frameworks/PySide6/QtCore.abi3.so'
        self.qtcore.write_bytes(b'statically linked permissions')
        self.plist = self.bundle / 'Contents/Info.plist'
        self.metadata = {'CFBundleVersion': '3.0.2', 'CFBundleShortVersionString': '3.0.2',
                         'LSMinimumSystemVersion': '14.0',
                         'NSMicrophoneUsageDescription': 'Classroom audio',
                         'NSLocalNetworkUsageDescription': 'WLED display'}
        self.write_plist()

    def write_plist(self):
        self.plist.write_bytes(plistlib.dumps(self.metadata))

    def test_accepts_static_permission_backend_and_verifies_signature(self):
        with mock.patch.object(build_release.subprocess, 'run', return_value=mock.Mock(stdout='arm64\n')) as native:
            build_release.verify_macos_bundle(self.bundle, '3.0.2', 'arm64')
        self.assertEqual(native.call_args_list[0].args[0][:2], ['/usr/bin/lipo', '-archs'])
        self.assertEqual(native.call_args_list[1].args[0],
                         ['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(self.bundle)])

    def test_rejects_missing_or_wrong_bundle_metadata(self):
        for field in list(self.metadata):
            with self.subTest(field=field):
                value = self.metadata.pop(field)
                self.write_plist()
                with self.assertRaisesRegex(RuntimeError, 'metadata'):
                    build_release.verify_macos_bundle(self.bundle, '3.0.2', 'arm64')
                self.metadata[field] = value

    def test_rejects_missing_qtcore_extension(self):
        self.qtcore.unlink()
        with self.assertRaisesRegex(RuntimeError, 'QtCore'):
            build_release.verify_macos_bundle(self.bundle, '3.0.2', 'arm64')

    def test_rejects_wrong_native_architecture(self):
        with mock.patch.object(build_release.subprocess, 'run', return_value=mock.Mock(stdout='x86_64\n')):
            with self.assertRaisesRegex(RuntimeError, 'native arm64'):
                build_release.verify_macos_bundle(self.bundle, '3.0.2', 'arm64')


if __name__ == '__main__':
    unittest.main()
