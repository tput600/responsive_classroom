"""Fail-closed validation for a universal2 frozen app."""
import tempfile
from pathlib import Path
import unittest
from unittest import mock
from tools import verify_universal_macos as universal


class UniversalBundleAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binary = self.root / 'Contents/MacOS/App'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(bytes.fromhex('cffaedfe') + b'native')

    def run_tool(self, arguments, **options):
        if arguments[0].endswith('lipo'):
            return mock.Mock(stdout='x86_64 arm64\n')
        return mock.Mock(stdout=f'{self.binary}:\n\t@rpath/libpython.dylib (compatibility version 3.12.0)\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n')

    def test_audits_both_slices_and_skips_symlink_aliases(self):
        try:
            (self.root / 'alias').symlink_to('Contents/MacOS/App')
        except (NotImplementedError, OSError):
            self.skipTest('Symlink creation is unavailable on this host')
        with mock.patch.object(universal.subprocess, 'run', side_effect=self.run_tool) as run:
            records = universal.audit(self.root)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['architectures'], ['arm64', 'x86_64'])
        self.assertEqual(run.call_count, 3)

    def test_rejects_thin_binary(self):
        with mock.patch.object(universal.subprocess, 'run', return_value=mock.Mock(stdout='arm64\n')):
            with self.assertRaisesRegex(RuntimeError, 'Non-universal'):
                universal.audit(self.root)

    def test_rejects_external_dependency(self):
        def tool(arguments, **options):
            if arguments[0].endswith('otool'):
                return mock.Mock(stdout=f'{self.binary}:\n\t/opt/homebrew/lib/libbad.dylib (compatibility version 1.0.0)\n')
            return self.run_tool(arguments, **options)
        with mock.patch.object(universal.subprocess, 'run', side_effect=tool):
            with self.assertRaisesRegex(RuntimeError, 'Nonportable dependency'):
                universal.audit(self.root)

    def test_rejects_tree_without_macho(self):
        self.binary.write_bytes(b'plain text')
        with self.assertRaisesRegex(RuntimeError, 'No Mach-O'):
            universal.audit(self.root)
