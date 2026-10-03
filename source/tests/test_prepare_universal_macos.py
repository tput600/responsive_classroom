"""Portable unit tests for fail-closed universal2 wheel preparation."""
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools import prepare_universal_macos as prep


class UniversalWheelTests(unittest.TestCase):
    def test_same_resources(self):
        self.assertEqual(prep.compare_resources({'x.py': b'x'}, {'x.py': b'x'}), {})

    def test_reject_changed_python(self):
        with self.assertRaisesRegex(RuntimeError, 'resource mismatch'):
            prep.compare_resources({'x.py': b'x'}, {'x.py': b'y'})

    def test_reject_missing_resources(self):
        with self.assertRaisesRegex(RuntimeError, 'resource mismatch'):
            prep.compare_resources({'x.py': b'x'}, {})

    def test_allow_wheel_metadata_only(self):
        self.assertEqual(prep.compare_resources({'x.dist-info/WHEEL': b'a'},
                                               {'x.dist-info/WHEEL': b'b'}), {})
        with self.assertRaisesRegex(RuntimeError, 'resource mismatch'):
            prep.compare_resources({'x.dist-info/METADATA': b'a'},
                                   {'x.dist-info/METADATA': b'b'})

    def test_preserve_numpy_generated_configuration(self):
        path = 'numpy/__config__.py'
        self.assertEqual(prep.compare_resources({path: b'arm'}, {path: b'intel'}),
                         {path: (b'arm', b'intel')})

    def test_allow_paired_native(self):
        arm = bytes.fromhex('cffaedfe') + b'arm'
        intel = bytes.fromhex('cffaedfe') + b'intel'
        self.assertEqual(prep.compare_resources({'x.so': arm}, {'x.so': intel}), {})

    def test_reject_native_vs_python(self):
        with self.assertRaisesRegex(RuntimeError, 'resource mismatch'):
            prep.compare_resources({'x.so': bytes.fromhex('cffaedfe')}, {'x.so': b'abc'})

    def test_reject_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.whl'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('../oops', b'bad')
            with self.assertRaisesRegex(RuntimeError, 'Unsafe wheel'):
                prep.safe_members(path)

    def test_select_universal_over_native(self):
        names = ['numpy-2.5.3-cp312-cp312-macosx_14_0_arm64.whl',
                 'numpy-2.5.3-cp312-cp312-macosx_14_0_universal2.whl']
        release = {'urls': [{'filename': name, 'packagetype': 'bdist_wheel'} for name in names]}
        self.assertEqual(prep.choose_wheel(release, 'universal2')['filename'], names[1])
        self.assertEqual(prep.choose_wheel(release, 'arm64')['filename'], names[0])

    def test_ignores_yanked_and_unsupported(self):
        release = {'urls': [
            {'filename': 'x-1-cp312-cp312-macosx_15_0_universal2.whl', 'packagetype': 'bdist_wheel'},
            {'filename': 'x-1-cp312-cp312-macosx_14_0_universal2.whl', 'packagetype': 'bdist_wheel', 'yanked': True}]}
        self.assertIsNone(prep.choose_wheel(release, 'universal2'))

    def test_reject_thin_in_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'x').write_bytes(b'anything')
            with (patch.object(prep, 'native_arches', return_value={'arm64'}),
                  self.assertRaisesRegex(RuntimeError, 'Non-universal')):
                prep.audit_tree(Path(tmp))


if __name__ == '__main__':
    unittest.main()
