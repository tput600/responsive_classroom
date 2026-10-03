"""Cross-platform candidate assembly tests using tiny, offline synthetic bundles."""
import contextlib
import hashlib
import io
import json
import os
import plistlib
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from tools import assemble_desktop_archive as desktop
from tools.macos_package import tree_records, write_archive


@unittest.skipUnless(os.name == 'posix', 'Assembly requires POSIX symlink and mode support')
class DesktopArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='desktop 候選 ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.windows_tree = self.root / 'windows-tree'
        self.windows_tree.mkdir()
        self.mac_tree = self.root / 'mac-tree'
        self.mac_tree.mkdir()
        self.windows = self.root / 'windows.zip'
        self.macos = self.root / 'macos.zip'
        self.output = self.root / 'desktop.zip'
        self.version = '3.0.3'
        for name, data in {'ResponsiveClassroom.exe': b'windows exe',
                           '_internal/python312.dll': b'windows runtime',
                           '_internal/audio/blue-danube.mp3': b'owner supplied media',
                           '_internal/THIRDPARTY_NOTICES.md': b'Original notices; rights unverified'}.items():
            path = self.windows_tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.app = self.mac_tree / 'ResponsiveClassroom.app'
        self.executable = self.app / 'Contents/MacOS/ResponsiveClassroom'
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b'synthetic universal executable')
        self.executable.chmod(0o755)
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleVersion': self.version, 'CFBundleShortVersionString': self.version,
            'LSMinimumSystemVersion': '14.0'}))
        framework = self.app / 'Contents/Frameworks/QtCore.framework'
        (framework / 'Versions/A/Resources').mkdir(parents=True)
        (framework / 'Versions/A/QtCore').write_bytes(b'synthetic universal QtCore')
        (framework / 'Versions/A/QtCore').chmod(0o755)
        (framework / 'Versions/Current').symlink_to('A', target_is_directory=True)
        (framework / 'QtCore').symlink_to('Versions/Current/QtCore')
        (framework / 'Resources').symlink_to('Versions/Current/Resources', target_is_directory=True)
        (self.mac_tree / 'MACOS.md').write_text('Original native acceptance checklist', encoding='utf-8')
        self.rebuild()

    def rebuild(self, *, mac_platform='macos-universal2', windows_version='3.0.3'):
        windows_files = [{'path': path.relative_to(self.windows_tree).as_posix(),
                          'bytes': path.stat().st_size, 'sha256': desktop.sha256(path)}
                         for path in sorted(self.windows_tree.rglob('*'))
                         if path.is_file() and path.name != 'resource_manifest.json']
        (self.windows_tree / 'resource_manifest.json').write_bytes(desktop.json_bytes({
            'version': windows_version, 'platform': 'windows-x64', 'files': windows_files}))
        # Windows source archives commonly omit directory entries and carry DOS metadata.
        with zipfile.ZipFile(self.windows, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(self.windows_tree.rglob('*')):
                if path.is_file():
                    entry = zipfile.ZipInfo(path.relative_to(self.windows_tree).as_posix())
                    entry.create_system = 0
                    entry.external_attr = 0x20
                    archive.writestr(entry, path.read_bytes())
        (self.mac_tree / 'resource_manifest.json').write_bytes(desktop.json_bytes({
            'version': self.version, 'platform': mac_platform, 'files': tree_records(self.mac_tree)}))
        write_archive(self.mac_tree, self.macos)

    def assemble(self, **kwargs):
        return desktop.assemble(self.windows, self.macos, self.output, version=self.version, **kwargs)

    def rewrite(self, archive, name, *, data=None, mode=None):
        with zipfile.ZipFile(archive) as source:
            entries = [(info, source.read(info)) for info in source.infolist()]
        with zipfile.ZipFile(archive, 'w') as output:
            for info, contents in entries:
                if info.filename == name:
                    if data is not None:
                        contents = data
                    if mode is not None:
                        info.external_attr = mode << 16
                output.writestr(info, contents)

    def test_full_payload_one_app_manifest_checksum_and_round_trip(self):
        report = self.assemble()
        self.assertEqual(report['status'], 'ok')
        self.assertFalse(report['published'])
        self.assertEqual(report['symlinks'], 3)
        self.assertLess(report['archive_bytes'], desktop.MAX_BYTES)
        with zipfile.ZipFile(self.output) as zipped:
            names = zipped.namelist()
            self.assertIn('Windows/ResponsiveClassroom.exe', names)
            self.assertIn('Windows/_internal/python312.dll', names)
            self.assertIn('macOS/ResponsiveClassroom.app/Contents/MacOS/ResponsiveClassroom', names)
            self.assertEqual({name.split('/')[1] for name in names
                              if name.startswith('macOS/') and '.app/' in name}, {'ResponsiveClassroom.app'})
            manifest = json.loads(zipped.read(desktop.MANIFEST))
            self.assertEqual(manifest['platforms'], ['windows-x64', 'macos-universal2'])
            self.assertEqual(manifest['inputs']['macos']['sha256'], desktop.sha256(self.macos))
            self.assertFalse(manifest['macos']['developer_id_signed'])
            self.assertFalse(manifest['macos']['notarized'])
            self.assertIn('redistribution-rights-not-independently-verified', str(manifest['media_rights']))
            original = 'ResponsiveClassroom.app/Contents/'
            with zipfile.ZipFile(self.macos) as mac:
                for info in mac.infolist():
                    copy = zipped.getinfo('macOS/' + info.filename)
                    self.assertEqual(copy.external_attr >> 16, info.external_attr >> 16)
                    self.assertEqual(zipped.read(copy), mac.read(info))
            link = zipped.getinfo('macOS/' + original + 'Frameworks/QtCore.framework/QtCore')
            self.assertTrue(stat.S_ISLNK(link.external_attr >> 16))
            self.assertEqual(zipped.read(link), b'Versions/Current/QtCore')
            binary = zipped.getinfo('macOS/' + original + 'MacOS/ResponsiveClassroom')
            self.assertEqual(stat.S_IMODE(binary.external_attr >> 16), 0o755)
            self.assertIn('macOS/' + original + 'Frameworks/QtCore.framework/Versions/A/Resources/', names)
            self.assertTrue(all(info.date_time == desktop.FIXED_TIME for info in zipped.infolist()))
        self.assertEqual(self.output.with_suffix('.zip.sha256').read_text(),
                         f'{hashlib.sha256(self.output.read_bytes()).hexdigest()}  desktop.zip\n')

    def test_output_is_deterministic_for_identical_inputs_with_different_umask(self):
        self.assemble()
        expected = self.output.read_bytes()
        other = self.root / 'other.zip'
        previous = os.umask(0o077)
        try:
            desktop.assemble(self.windows, self.macos, other, version=self.version)
        finally:
            os.umask(previous)
        self.assertEqual(other.read_bytes(), expected)

    def test_native_test_evidence_is_retained_and_hashed(self):
        evidence = self.root / 'native-evidence.json'
        value = {'windows-x64': {'passed': True}, 'macos-arm64': {'passed': True},
                 'macos-x86_64': {'passed': True}}
        evidence.write_bytes(desktop.json_bytes(value))
        self.assemble(validation_evidence=evidence)
        with zipfile.ZipFile(self.output) as zipped:
            manifest = json.loads(zipped.read(desktop.MANIFEST))
        self.assertEqual(manifest['validation_evidence'], {'report': value, 'sha256': desktop.sha256(evidence)})

    def test_rejects_thin_mac_archive_and_version_mismatch(self):
        for platform, version in [('macos-arm64', self.version), ('macos-x86_64', self.version),
                                   ('macos-universal2', '3.0.2')]:
            with self.subTest(platform=platform, version=version):
                self.rebuild(mac_platform=platform, windows_version=version)
                with self.assertRaisesRegex(ValueError, 'mismatch'):
                    self.assemble()
                self.assertFalse(self.output.exists())

    def test_rejects_altered_windows_content_and_missing_internal(self):
        self.rewrite(self.windows, '_internal/python312.dll', data=b'tampered')
        with self.assertRaisesRegex(ValueError, 'differs from its manifest'):
            self.assemble()
        for path in sorted((self.windows_tree / '_internal').rglob('*'), reverse=True):
            if path.is_file():
                path.unlink()
            else:
                path.rmdir()
        (self.windows_tree / '_internal').rmdir()
        self.rebuild()
        with self.assertRaisesRegex(ValueError, 'EXE and complete _internal'):
            self.assemble()

    def test_rejects_altered_mac_content_mode_and_flattened_link(self):
        base = 'ResponsiveClassroom.app/Contents/'
        for name, changes in [
                (base + 'MacOS/ResponsiveClassroom', {'data': b'tampered'}),
                (base + 'MacOS/ResponsiveClassroom', {'mode': stat.S_IFREG | 0o644}),
                (base + 'Frameworks/QtCore.framework/QtCore', {'mode': stat.S_IFREG | 0o777})]:
            with self.subTest(name=name, changes=changes):
                self.rebuild()
                self.rewrite(self.macos, name, **changes)
                with self.assertRaisesRegex(ValueError, 'tree differs'):
                    self.assemble()

    def test_rejects_second_top_level_mac_app(self):
        (self.mac_tree / 'Other.app').mkdir()
        self.rebuild()
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.assemble()

    def test_rejects_wrong_mac_minimum_os_even_with_valid_manifest(self):
        plist = self.app / 'Contents/Info.plist'
        info = plistlib.loads(plist.read_bytes())
        info['LSMinimumSystemVersion'] = '13.0'
        plist.write_bytes(plistlib.dumps(info))
        self.rebuild()
        with self.assertRaisesRegex(ValueError, 'minimum-system'):
            self.assemble()

    def test_rejects_paths_that_escape_or_have_portable_extraction_collisions(self):
        for name in ('../outside', '/outside', 'a/../outside', 'a//file', 'a\\file',
                     'C:/file', 'file:alternate', 'NUL.txt', 'bad./file', 'new\nline'):
            with self.subTest(name=name):
                self.rebuild()
                with zipfile.ZipFile(self.windows, 'a') as zipped:
                    zipped.writestr(name, b'bad')
                with self.assertRaises(ValueError):
                    self.assemble()
                self.assertFalse(self.output.exists())
                self.assertFalse((self.root / 'outside').exists())

    def test_rejects_duplicates_case_collisions_and_non_directory_ancestors(self):
        for name in ('ResponsiveClassroom.exe', 'responsiveclassroom.exe',
                     'ResponsiveClassroom.exe/child', '_INTERNAL/child'):
            with self.subTest(name=name):
                self.rebuild()
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    with zipfile.ZipFile(self.windows, 'a') as zipped:
                        zipped.writestr(name, b'bad')
                with self.assertRaisesRegex(ValueError, 'Duplicate|colliding|nondirectory'):
                    self.assemble()

    def test_rejects_symlink_ancestors_and_external_broken_cyclic_links(self):
        for target, child in [('../escape', False), ('/tmp/outside', False),
                               ('missing', False), ('evil', False), ('MACOS.md', True)]:
            with self.subTest(target=target, child=child):
                self.rebuild()
                with zipfile.ZipFile(self.macos, 'a') as zipped:
                    link = zipfile.ZipInfo('evil')
                    link.create_system = 3
                    link.external_attr = (stat.S_IFLNK | 0o777) << 16
                    zipped.writestr(link, target)
                    if child:
                        entry = zipfile.ZipInfo('evil/child')
                        entry.create_system = 3
                        entry.external_attr = (stat.S_IFREG | 0o644) << 16
                        zipped.writestr(entry, b'bad')
                with self.assertRaises(ValueError):
                    self.assemble()
                self.assertFalse(self.output.exists())

    def test_rejects_app_symlink_to_sibling_document(self):
        (self.app / 'escaped-document').symlink_to('../MACOS.md')
        self.rebuild()
        with self.assertRaisesRegex(ValueError, 'escapes package'):
            self.assemble()

    def test_rejects_windows_symlinks_and_special_files(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o644, stat.S_IFREG | 0o4755):
            with self.subTest(mode=mode):
                self.rebuild()
                self.rewrite(self.windows, 'ResponsiveClassroom.exe', mode=mode)
                with self.assertRaisesRegex(ValueError, 'Unsupported|Special'):
                    self.assemble()

    def test_extraction_size_cap_and_archive_size_cap_fail_without_output(self):
        with (mock.patch.object(desktop, 'MAX_EXPANDED_BYTES', 16),
              self.assertRaisesRegex(ValueError, 'size cap|safety cap')):
            self.assemble()
        self.assertFalse(self.output.exists())
        # Inputs fit, but deliberately inflated final archive must never be emitted.
        original = desktop.write_deterministic_archive
        def oversized(root, archive):
            original(root, archive)
            with archive.open('ab') as stream:
                stream.write(b'0' * 100_000)
        with (mock.patch.object(desktop, 'MAX_BYTES', 50_000),
              mock.patch.object(desktop, 'write_deterministic_archive', side_effect=oversized),
              self.assertRaisesRegex(ValueError, 'Combined ZIP exceeds')):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_nondefault_link_modes_are_preserved_or_fail_explicitly(self):
        archive = self.root / 'link-mode.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            for name, mode, contents in [('file', stat.S_IFREG | 0o644, b'payload'),
                                          ('link', stat.S_IFLNK | 0o755, b'file')]:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                zipped.writestr(info, contents)
        extracted = self.root / 'link-mode'
        if os.chmod in os.supports_follow_symlinks:
            desktop.extract_checked(archive, extracted, windows=False, max_bytes=1024)
            self.assertEqual(stat.S_IMODE((extracted / 'link').lstat().st_mode), 0o755)
        else:
            with self.assertRaisesRegex(ValueError, 'Host cannot preserve symlink mode'):
                desktop.extract_checked(archive, extracted, windows=False, max_bytes=1024)

    def test_crc_corruption_and_null_truncated_filename_are_rejected(self):
        archive = self.root / 'malformed.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            info = zipfile.ZipInfo('nulx')
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            zipped.writestr(info, b'unique payload')
        clean = archive.read_bytes()
        archive.write_bytes(clean.replace(b'nulx', b'nul\x00'))
        with self.assertRaisesRegex(ValueError, 'truncated or normalized'):
            desktop.extract_checked(archive, self.root / 'null-extracted', windows=False, max_bytes=1024)
        archive.write_bytes(clean.replace(b'unique payload', b'broken payload'))
        with self.assertRaises(zipfile.BadZipFile):
            desktop.extract_checked(archive, self.root / 'crc-extracted', windows=False, max_bytes=1024)

    def test_does_not_overwrite_existing_archive_or_checksum(self):
        for path in (self.output, self.output.with_suffix('.zip.sha256')):
            with self.subTest(path=path):
                path.write_bytes(b'preserve existing')
                with self.assertRaisesRegex(ValueError, 'new .zip'):
                    self.assemble()
                self.assertEqual(path.read_bytes(), b'preserve existing')
                path.unlink()

    def test_cli_emits_report_and_guards_report_input_collision(self):
        report = self.root / 'reports/assembly.json'
        arguments = ['--windows', str(self.windows), '--macos', str(self.macos),
                     '--output', str(self.output), '--version', self.version, '--report', str(report)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(desktop.main(arguments), 0)
        self.assertEqual(json.loads(report.read_text())['status'], 'ok')
        original = self.windows.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(desktop.main(arguments[:-1] + [str(self.windows)]), 1)
        self.assertEqual(self.windows.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
