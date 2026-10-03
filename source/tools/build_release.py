"""Build a complete public portable release from a reproducible source checkout."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'source'


def run(arguments, **options):
    # Build invocations use argument lists and never a shell.
    subprocess.run(arguments, check=True, **options)  # nosec B603


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def assert_public_package(package):
    names = [path.relative_to(package).as_posix() for path in package.rglob('*') if path.is_file()]
    prohibited = re.compile(r'(?i)(?:/fixtures/|/qml/|(?:^|/)(?:\.codex|settings\.json)(?:/|$)|Qt6(?:Graphs|Charts|DataVisualization|Quick3D|QuickTimeline|VirtualKeyboard)|portaudio[^/]*asio)')
    wrong = [name for name in names if prohibited.search(name)]
    if wrong:
        raise RuntimeError(f'Private or incompatible files in package: {wrong[:10]}')
    required = ('LICENSE', 'THIRDPARTY_NOTICES.md', 'models/sensevoice/LICENSE',
                'models/sensevoice/manifest.json', 'models/sensevoice/model.int8.onnx',
                'models/sensevoice/tokens.txt', 'models/sensevoice/silero_vad.onnx',
                'audio/default-rest.mp3', 'audio/blue-danube.mp3',
                'licenses/project/GNU-LGPL-3.0.txt',
                'licenses/project/GNU-LGPL-2.1.txt', 'licenses/project/ONNXRuntime-1.28.2-ThirdPartyNotices.txt',
                'licenses/project/CFFI-MIT-0.txt', 'licenses/Python-runtime/python-runtime.json')
    for suffix in required:
        if not any(name == suffix or name.endswith('/' + suffix) for name in names):
            raise RuntimeError(f'Missing release asset: {suffix}')


def frozen_checks(package, reports, windows):
    # Short packaging checks do not access the microphone or send board packets.
    with tempfile.TemporaryDirectory(prefix='Responsive Classroom 測試 ') as temporary:
        stage = Path(temporary) / package.name
        shutil.copytree(package, stage, symlinks=not windows)
        executable = (stage / 'ResponsiveClassroom.exe' if windows else
                      stage / 'Contents/MacOS/ResponsiveClassroom')
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        if windows:
            system = Path(env.get('SystemRoot', r'C:\Windows'))
            env['PATH'] = os.pathsep.join((str(system / 'System32'), str(system)))
        else:
            env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
        for flag, filename in (('--self-test-report', 'frozen-model.json'),
                               ('--ui-smoke-report', 'frozen-ui.json')):
            report = Path(temporary) / filename
            with (reports / (filename + '.log')).open('w', encoding='utf-8') as output:
                run([str(executable), flag, str(report)], cwd=temporary, env=env,
                    stdout=output, stderr=subprocess.STDOUT, timeout=100)
            if not report.is_file():
                raise RuntimeError(f'Frozen application did not write {filename}')
            value = json.loads(report.read_text(encoding='utf-8'))
            if flag == '--ui-smoke-report':
                if value.get('passed') is not True:
                    raise RuntimeError('Frozen Web UI check failed')
            elif (not all(value.get('model_assets', {}).values()) or
                  not value.get('silent_audio_empty') or
                  not all(value.get('text_parser', {}).values()) or
                  value.get('synthetic_voice_available')):
                raise RuntimeError('Public model/parser checks failed or private fixtures were included')
            shutil.copyfile(report, reports / filename)


def main():
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError('Use the pinned Python 3.12 development environment')
    windows = sys.platform == 'win32'
    if not windows and sys.platform != 'darwin':
        raise RuntimeError('Build Windows releases on Windows; macOS bundles on macOS')
    version = re.search(r'VERSION = "([^"]+)"',
                        (SOURCE / 'classroom_resources.py').read_text(encoding='utf-8')).group(1)
    run([sys.executable, str(SOURCE / 'tools/check_public_tree.py')], cwd=ROOT)
    run([sys.executable, str(SOURCE / 'tools/fetch_models.py')], cwd=ROOT)
    reports = ROOT / 'build/reports'
    reports.mkdir(parents=True, exist_ok=True)
    with (reports / 'unit-tests.log').open('w', encoding='utf-8') as output:
        run([sys.executable, '-X', 'utf8', '-m', 'unittest', 'discover', '-s',
             'source/tests', '-t', 'source', '-v'], cwd=ROOT,
            stdout=output, stderr=subprocess.STDOUT, timeout=180)
    env = dict(os.environ, RESPONSIVE_CLASSROOM_PUBLIC_BUILD='1', PYTHONUTF8='1')
    if windows:
        system = Path(env.get('SystemRoot', r'C:\Windows'))
        env['PATH'] = os.pathsep.join((str(Path(sys.executable).parent),
                                       str(system / 'System32'), str(system)))
    with (reports / 'pyinstaller.log').open('w', encoding='utf-8') as output:
        run([sys.executable, '-X', 'utf8', '-m', 'PyInstaller', '--noconfirm', '--clean',
             '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build/pyinstaller'),
             str(SOURCE / 'tools/responsive_classroom.spec')], cwd=ROOT, env=env,
            stdout=output, stderr=subprocess.STDOUT, timeout=900)
    package = ROOT / 'dist' / ('ResponsiveClassroom' if windows else 'ResponsiveClassroom.app')
    assert_public_package(package)
    frozen_checks(package, reports, windows)
    for filename in ('README.md', 'README.en.md', 'LICENSE'):
        shutil.copyfile(ROOT / filename, package / filename)
    entries = [{'path': path.relative_to(package).as_posix(), 'bytes': path.stat().st_size,
                'sha256': sha256(path)} for path in sorted(package.rglob('*')) if path.is_file()]
    target = 'windows-x64' if windows else f'macos-{platform.machine()}'
    manifest = {'app': 'Responsive Classroom', 'version': version, 'platform': target,
                'python_version': platform.python_version(),
                'files': entries}
    (package / 'resource_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    artifacts = ROOT / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    archive = artifacts / f'ResponsiveClassroom-Portable-{target}-v{version}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in sorted(package.rglob('*')):
            relative=path.relative_to(package).as_posix()
            name=relative if windows else package.name+'/'+relative
            if not windows and path.is_symlink():
                # Preserve framework links and the enclosing .app on macOS.
                info=zipfile.ZipInfo(name)
                info.create_system=3
                info.external_attr=(0o120777 << 16)
                zipped.writestr(info,os.readlink(path).encode('utf-8'))
            elif path.is_file():
                zipped.write(path,name)
    if windows:
        run([sys.executable, str(SOURCE / 'tools/verify-portable.py'), '--package', str(package),
             '--archive', str(archive), '--report', str(reports / 'integrity.json'),
             '--expected-version', version], cwd=ROOT)
    digest = sha256(archive)
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(f'Created {archive.name} ({archive.stat().st_size} bytes); SHA-256 {digest}')
    print('Extract the complete ZIP and run the application. Python is not needed by recipients.')


if __name__ == '__main__':
    main()
