"""Audit every Mach-O slice and run the identical universal ZIP natively."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tempfile

if __package__:
    from .build_release import frozen_checks, verify_macos_bundle
    from .macos_package import tree_paths, verify_tree
else:
    from build_release import frozen_checks, verify_macos_bundle
    from macos_package import tree_paths, verify_tree

MAGICS = {bytes.fromhex(value) for value in (
    'feedface', 'cefaedfe', 'feedfacf', 'cffaedfe',
    'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca')}


def audit(bundle):
    records = []
    for path in tree_paths(bundle):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as stream:
            if stream.read(4) not in MAGICS:
                continue
        result = subprocess.run(['/usr/bin/lipo', '-archs', str(path)],
                                check=True, capture_output=True, text=True)  # nosec B603
        architectures = set(result.stdout.split())
        if architectures != {'arm64', 'x86_64'}:
            raise RuntimeError(f'Non-universal binary: {path}: {architectures}')
        records.append({'path': path.relative_to(bundle).as_posix(),
                        'architectures': sorted(architectures)})
        for arch in sorted(architectures):
            result = subprocess.run(['/usr/bin/otool', '-arch', arch, '-L', str(path)],
                                    check=True, capture_output=True, text=True)  # nosec B603
            for line in result.stdout.splitlines()[1:]:
                dependency = line.strip().split(' (')[0]
                if dependency.startswith('/') and not dependency.startswith(('/System/', '/usr/lib/')):
                    raise RuntimeError(f'Nonportable dependency in {path} ({arch}): {dependency}')
    if not records:
        raise RuntimeError('No Mach-O binaries found')
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--machine', choices=('arm64', 'x86_64'), required=True)
    parser.add_argument('--reports', required=True, type=Path)
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != args.machine:
        raise RuntimeError('Validation must run on the requested native macOS architecture')
    args.reports.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='universal-native-') as temp:
        root = Path(temp)
        subprocess.run(['/usr/bin/ditto', '-x', '-k', str(args.archive.resolve()), temp], check=True)  # nosec B603
        verify_tree(root, version=args.version, architecture='universal2')
        bundle = root / 'ResponsiveClassroom.app'
        verify_macos_bundle(bundle, args.version, 'universal2')
        records = audit(bundle)
        (args.reports / 'universal-binaries.json').write_text(json.dumps(records, indent=2) + '\n')
        frozen_checks(bundle, args.reports.resolve(), windows=False,
                      archive=args.archive.resolve(), version=args.version, architecture='universal2')
    model = json.loads((args.reports / 'frozen-model.json').read_text())
    if model.get('machine') != args.machine or model.get('frozen') is not True:
        raise RuntimeError('Frozen process did not execute on the requested native architecture')
    with args.archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    summary = {'archive_sha256': digest, 'machine': args.machine, 'version': args.version,
               'universal_binaries': len(records), 'signature': 'ad-hoc-verified',
               'frozen_model': 'passed', 'frozen_ui': 'passed'}
    (args.reports / 'native-verification.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(f'Verified {len(records)} universal binaries and native model/UI on {args.machine}')


if __name__ == '__main__':
    main()
