"""Upgrade an existing macOS CPython 3.12 venv to verified universal2 wheels.

Run AFTER setup_environment.py --dev --models, using the universal2 interpreter.
Never merge frozen PyInstaller executables: their embedded archives cannot be
combined by lipo. This merges original same-version wheels before freezing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from importlib import metadata
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse
from urllib.request import urlopen

ARCHES = {'arm64', 'x86_64'}
MACH_MAGICS = {bytes.fromhex(value) for value in (
    'feedface', 'cefaedfe', 'feedfacf', 'cffaedfe',
    'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca')}
ROOT = Path(__file__).resolve().parents[2]


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)  # nosec B603


def is_native_bytes(data):
    return data[:4] in MACH_MAGICS or data.startswith(b'!<arch>\n')


def native_arches(path):
    if not path.is_file():
        return None
    with path.open('rb') as stream:
        if not is_native_bytes(stream.read(8)):
            return None
    return set(run(['/usr/bin/lipo', '-archs', path], capture_output=True,
                   text=True).stdout.split())


def audit_tree(root):
    """Inspect contents, not suffixes; include frameworks and helper executables."""
    records = []
    failures = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            if not path.exists():
                failures.append(f'Broken symlink: {path}')
            continue
        arches = native_arches(path)
        if arches is not None:
            records.append({'path': str(path.relative_to(root)), 'arches': sorted(arches)})
            if not ARCHES.issubset(arches):
                failures.append(f'{path}: {sorted(arches)}')
    if failures:
        raise RuntimeError('Non-universal native binaries:\n' + '\n'.join(failures))
    return records


def safe_members(wheel):
    with zipfile.ZipFile(wheel) as archive:
        result = {}
        for info in archive.infolist():
            name = info.filename
            parts = PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts or '\\' in name:
                raise RuntimeError(f'Unsafe wheel member: {name}')
            if name in result:
                raise RuntimeError(f'Duplicate wheel member: {name}')
            if not info.is_dir():
                result[name] = archive.read(info)
        return result


def compare_resources(left, right):
    """Reject silent pure-resource overwrites; retain known build-info variants."""
    changes = {}
    for name in sorted(left.keys() | right.keys()):
        a, b = left.get(name), right.get(name)
        if a == b:
            continue
        if '.dist-info/' in name and name.rsplit('/', 1)[-1] in {'RECORD', 'WHEEL'}:
            continue
        if a is not None and b is not None and is_native_bytes(a) and is_native_bytes(b):
            continue
        # These generated NumPy files describe the architecture used to build
        # each wheel. Preserve both rather than replacing one with the other.
        if name in {'numpy/__config__.py', 'numpy/_core/include/numpy/_numpyconfig.h'} and a and b:
            changes[name] = (a, b)
            continue
        raise RuntimeError(f'Wheel resource mismatch needs explicit review: {name}')
    return changes


def supported_tags(architecture):
    from packaging.tags import compatible_tags, cpython_tags, mac_platforms
    platforms = list(mac_platforms(version=(14, 0), arch=architecture))
    if architecture == 'universal2':
        platforms = [p for p in platforms if p.endswith('_universal2')]
    return list(cpython_tags((3, 12), platforms=platforms)) + list(
        compatible_tags((3, 12), interpreter='cp312', platforms=platforms))


def choose_wheel(release, architecture):
    from packaging.utils import parse_wheel_filename
    rank = {tag: index for index, tag in enumerate(supported_tags(architecture))}
    candidates = []
    for item in release['urls']:
        if item.get('yanked') or item.get('packagetype') != 'bdist_wheel':
            continue
        filename = item['filename']
        _, _, _, tags = parse_wheel_filename(filename)
        # A platform-independent wheel cannot repair a native distribution.
        if not any(tag.platform.startswith('macosx_') for tag in tags):
            continue
        matched = [rank[tag] for tag in tags if tag in rank]
        if matched:
            candidates.append((min(matched), filename, item))
    return min(candidates, default=(0, '', None), key=lambda entry: entry[:2])[2]


def fetch_json(url):
    # URL is constructed from the fixed PyPI HTTPS origin, never user input.
    with urlopen(url, timeout=90) as response:  # nosec B310
        return json.load(response)


def download(item, directory):
    url = urlparse(item['url'])
    if url.scheme != 'https' or url.hostname != 'files.pythonhosted.org':
        raise RuntimeError('Unexpected PyPI download host')
    filename = item['filename']
    if Path(filename).name != filename:
        raise RuntimeError('Unsafe wheel filename')
    path = directory / filename
    digest = hashlib.sha256()
    with urlopen(item['url'], timeout=180) as response, path.open('wb') as output:  # nosec B310
        while block := response.read(1024 * 1024):
            digest.update(block)
            output.write(block)
    if digest.hexdigest() != item['digests']['sha256']:
        raise RuntimeError(f'PyPI SHA256 mismatch: {filename}')
    return path


def merge_wheels(left, right, output, tools_python):
    differences = compare_resources(safe_members(left), safe_members(right))
    run([tools_python.parent / 'delocate-merge', left, right, '-w', output])
    merged = list(output.glob('*.whl'))
    if len(merged) != 1:
        raise RuntimeError(f'Expected exactly one merged wheel, got {merged}')
    if differences:
        # wheel unpack/pack regenerates RECORD, keeping wheel integrity valid.
        unpacked = output / 'unpacked'
        run([tools_python, '-m', 'wheel', 'unpack', merged[0], '-d', unpacked])
        roots = list(unpacked.iterdir())
        if len(roots) != 1:
            raise RuntimeError('Unexpected wheel unpack layout')
        tree = roots[0]
        for name, (arm, intel) in differences.items():
            target = tree / name
            if name.endswith('.py'):
                for arch, data in (('arm64', arm), ('x86_64', intel)):
                    (target.parent / f'_universal_config_{arch}.py').write_bytes(data)
                target.write_text(
                    '"""Architecture-specific upstream NumPy build configuration."""\n'
                    'import platform as _platform\n'
                    'if _platform.machine() == "arm64":\n'
                    '    from . import _universal_config_arm64 as _config\n'
                    'elif _platform.machine() == "x86_64":\n'
                    '    from . import _universal_config_x86_64 as _config\n'
                    'else:\n'
                    '    raise RuntimeError("Unsupported NumPy architecture")\n'
                    'globals().update({k: v for k, v in vars(_config).items()\n'
                    '                  if k not in {"__name__", "__loader__", "__package__",\n'
                    '                               "__spec__", "__file__", "__cached__", "__builtins__"}})\n',
                    encoding='utf-8')
            else:
                target.write_bytes(b'#if defined(__arm64__) || defined(__aarch64__)\n' + arm
                                   + b'\n#elif defined(__x86_64__)\n' + intel
                                   + b'\n#else\n#error Unsupported architecture\n#endif\n')
        repacked = output / 'repacked'
        repacked.mkdir()
        run([tools_python, '-m', 'wheel', 'pack', tree, '-d', repacked])
        merged = list(repacked.glob('*.whl'))
    return merged[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=ROOT / 'build/reports/universal-environment.json')
    args = parser.parse_args()
    if sys.platform != 'darwin' or sys.version_info[:2] != (3, 12):
        parser.error('Run with macOS universal2 CPython 3.12 in the build venv')
    if sys.prefix == sys.base_prefix:
        parser.error('Run from the isolated application virtual environment')
    if not ARCHES.issubset(native_arches(Path(sys.executable).resolve()) or set()):
        raise RuntimeError('The base Python executable must already be universal2')
    site = Path(sysconfig.get_path('platlib'))
    # Stdlib extensions are not wheel dependencies and cannot be repaired here.
    audit_tree(Path(sysconfig.get_config_var('DESTSHARED')))
    report = {'python': sys.version, 'replacements': []}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='responsive-universal-') as temporary:
        work = Path(temporary)
        tools_env = work / 'merge-tools'
        run([sys.executable, '-m', 'venv', tools_env])
        tools_python = tools_env / 'bin/python'
        run([tools_python, '-m', 'pip', 'install', 'delocate==0.13.0', 'wheel==0.45.1'])
        # Capture before modifying distributions; no dependency version upgrades.
        installed = list(metadata.distributions(path=[str(site)]))
        for distribution in installed:
            files = [Path(distribution.locate_file(f)) for f in distribution.files or ()]
            thin = [str(path) for path in files
                    if (arches := native_arches(path)) is not None and not ARCHES.issubset(arches)]
            if not thin:
                continue
            name, version = distribution.metadata['Name'], distribution.version
            print(f'Preparing universal2 {name}=={version}', flush=True)
            release = fetch_json(f'https://pypi.org/pypi/{quote(name, safe="")}/{quote(version, safe="")}/json')
            directory = work / name
            directory.mkdir()
            universal = choose_wheel(release, 'universal2')
            selected = []
            if universal:
                selected.append(universal)
                wheel = download(universal, directory)
            else:
                for arch in ('arm64', 'x86_64'):
                    item = choose_wheel(release, arch)
                    if not item:
                        raise RuntimeError(f'No compatible wheel for {name}=={version} {arch}')
                    selected.append(item)
                arm, intel = [download(item, directory) for item in selected]
                output = directory / 'merged'
                output.mkdir()
                wheel = merge_wheels(arm, intel, output, tools_python)
            # Inspect the actual bytes BEFORE installing anything into the app venv.
            checked = directory / 'checked'
            run([tools_python, '-m', 'wheel', 'unpack', wheel, '-d', checked])
            records = audit_tree(checked)
            if not records:
                raise RuntimeError(f'Wheel unexpectedly contains no native binaries: {wheel.name}')
            run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--force-reinstall', wheel])
            report['replacements'].append({'name': name, 'version': version,
                'inputs': [{'filename': item['filename'], 'sha256': item['digests']['sha256']}
                           for item in selected], 'wheel': wheel.name, 'native_files': records})
            args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    report['native_files'] = audit_tree(site)
    run([sys.executable, '-m', 'pip', 'check'])
    report['passed'] = True
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Universal2 environment verified: {args.report}')


if __name__ == '__main__':
    main()
