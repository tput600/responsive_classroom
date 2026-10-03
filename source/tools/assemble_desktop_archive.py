"""Assemble one verified Windows + universal2 macOS candidate; never publish it.

Run on a POSIX filesystem so macOS symlinks and executable modes can be checked.
Only the standard library is needed. Platform builds and native smoke tests must
have passed before invoking this packaging-only tool.
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

if __package__:
    from .macos_package import (
        check_symlink,
        safe_path,
        sha256_stream,
        tree_paths,
        tree_records,
        verify_tree,
    )
else:
    from macos_package import (
        check_symlink,
        safe_path,
        sha256_stream,
        tree_paths,
        tree_records,
        verify_tree,
    )

MAX_BYTES = 2 * 1024**3  # Final ZIP must be below the 2 GiB per-asset limit.
MAX_EXPANDED_BYTES = 8 * 1024**3  # Bounded extraction allowance for both full runtimes.
MAX_ENTRIES = 200_000
MANIFEST = 'desktop_manifest.json'
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def sha256(path):
    with path.open('rb') as stream:
        return sha256_stream(stream)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def portable_path(name, *, windows=False):
    """Disallow traversal, drive/ADS paths, ambiguous extraction and device names."""
    relative = safe_path(name)
    for part in relative.parts:
        if ':' in part or any(ord(character) < 32 for character in part):
            raise ValueError(f'Unsafe package path: {name!r}')
        if windows and (part.endswith((' ', '.')) or any(c in part for c in '<>"|?*')
                        or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)):
            raise ValueError(f'Unsafe Windows package path: {name!r}')
    return relative


def entry_kind(info, *, windows):
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if info.flag_bits & 1:
        raise ValueError(f'Encrypted ZIP entries are not supported: {info.filename}')
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ValueError(f'Unsupported ZIP compression: {info.filename}')
    if mode & 0o7000:
        raise ValueError(f'Special permission bits are not supported: {info.filename}')
    if windows:
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError(f'Unsupported Windows ZIP entry: {info.filename}')
        if kind == stat.S_IFDIR and not info.is_dir():
            raise ValueError(f'ZIP entry type mismatch: {info.filename}')
        return 'directory' if info.is_dir() else 'file'
    if info.create_system != 3 or kind not in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
        raise ValueError(f'macOS ZIP must retain Unix entry types: {info.filename}')
    if info.is_dir() != (kind == stat.S_IFDIR):
        raise ValueError(f'ZIP entry type mismatch: {info.filename}')
    return {stat.S_IFREG: 'file', stat.S_IFDIR: 'directory', stat.S_IFLNK: 'symlink'}[kind]


def extract_checked(archive, destination, *, windows, max_bytes):
    """Extract in a fresh directory, never following a link from an input ZIP.

    Preflight the complete namespace. Regular files/directories are written
    first, then links. No member can have a symlink or regular-file ancestor.
    Bytes are capped before extraction and while streaming; reads verify CRCs.
    """
    if archive.is_symlink() or not archive.is_file():
        raise ValueError(f'Input ZIP must be a regular file: {archive}')
    if archive.stat().st_size >= MAX_BYTES:
        raise ValueError('Input ZIP exceeds the 2 GiB size cap')
    with zipfile.ZipFile(archive) as zipped:
        infos = zipped.infolist()
        if not infos or len(infos) > MAX_ENTRIES:
            raise ValueError('ZIP entry count is empty or exceeds the safety cap')
        members = {}
        aliases = {}
        expanded = 0
        for info in infos:
            if info.orig_filename != info.filename:
                raise ValueError(f'ZIP filename was truncated or normalized: {info.orig_filename!r}')
            # Strip one directory suffix only; repeated slashes stay invalid.
            name = info.filename[:-1] if info.is_dir() else info.filename
            relative = portable_path(name, windows=windows)
            alias = unicodedata.normalize('NFC', name).casefold()
            if name in members or (alias in aliases and aliases[alias] != name):
                raise ValueError(f'Duplicate or case/Unicode-colliding ZIP path: {name}')
            aliases[alias] = name
            kind = entry_kind(info, windows=windows)
            if kind == 'directory' and info.file_size:
                raise ValueError(f'Nonempty ZIP directory: {name}')
            if kind == 'symlink' and info.file_size > 4096:
                raise ValueError(f'Symlink target is too long: {name}')
            expanded += info.file_size
            if expanded >= max_bytes:
                raise ValueError('Expanded payload exceeds the 8 GiB safety cap')
            members[name] = (info, relative, kind)
        # Include implicit directory names when checking case/Unicode collisions.
        for name, (_, relative, _) in members.items():
            for parent in relative.parents:
                if parent == PurePosixPath('.'):
                    continue
                parent_name = parent.as_posix()
                alias = unicodedata.normalize('NFC', parent_name).casefold()
                if alias in aliases and aliases[alias] != parent_name:
                    raise ValueError(f'Case/Unicode-colliding ZIP directory: {parent_name}')
                aliases[alias] = parent_name
                if parent_name in members and members[parent_name][2] != 'directory':
                    raise ValueError(f'ZIP entry has a nondirectory ancestor: {name}')
        destination.mkdir(parents=True, exist_ok=False)
        directories = []
        links = []
        actual_total = 0
        for name in sorted(members):
            info, relative, kind = members[name]
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            permissions = stat.S_IMODE(info.external_attr >> 16)
            if kind == 'directory':
                # Read even empty entries so invalid CRCs cannot pass silently.
                if zipped.read(info):
                    raise ValueError(f'Nonempty ZIP directory: {name}')
                target.mkdir(exist_ok=True)
                directories.append((target, 0o755 if windows else permissions))
                continue
            if kind == 'symlink':
                data = zipped.read(info)
                actual_total += len(data)
                value = data.decode('utf-8')
                if not value or '\x00' in value or '\\' in value or ':' in value or value.startswith('/'):
                    raise ValueError(f'Nonportable symlink: {name}')
                links.append((target, value, permissions))
                continue
            count = 0
            with zipped.open(info) as source, target.open('xb') as output:
                while block := source.read(1024 * 1024):
                    count += len(block)
                    actual_total += len(block)
                    if count > info.file_size or actual_total >= max_bytes:
                        raise ValueError('Expanded payload exceeds declared size or 8 GiB safety cap')
                    output.write(block)
            if count != info.file_size:
                raise ValueError(f'ZIP uncompressed size mismatch: {name}')
            target.chmod(0o644 if windows else permissions)
        if actual_total != expanded:
            raise ValueError('ZIP expanded byte count differs from declared size')
        for target, value, _ in links:
            target.symlink_to(value)
        for target, _, permissions in links:
            check_symlink(target, destination)
            if stat.S_IMODE(target.lstat().st_mode) != permissions:
                try:
                    os.chmod(target, permissions, follow_symlinks=False)
                except (OSError, NotImplementedError) as exc:
                    raise ValueError(
                        f'Host cannot preserve symlink mode {permissions:o}: {target.name}; '
                        'assemble on macOS with symlink chmod support') from exc
                if stat.S_IMODE(target.lstat().st_mode) != permissions:
                    raise ValueError(f'Symlink mode was not preserved: {target.name}')
        for target, permissions in sorted(directories, key=lambda pair: len(pair[0].parts), reverse=True):
            target.chmod(permissions)
    return expanded


def verify_windows(root, version):
    manifest_path = root / 'resource_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if not isinstance(manifest, dict):
        raise TypeError('Windows manifest must be a JSON object')
    if manifest.get('version') != version or manifest.get('platform') != 'windows-x64':
        raise ValueError('Windows manifest version/platform mismatch')
    files = manifest.get('files')
    if not isinstance(files, list) or not files:
        raise ValueError('Windows manifest must contain file entries')
    expected = {}
    for record in files:
        if not isinstance(record, dict):
            raise TypeError('Windows manifest file record must be a JSON object')
        name = portable_path(record.get('path'), windows=True).as_posix()
        if name == 'resource_manifest.json' or name in expected:
            raise ValueError(f'Duplicate or reserved Windows manifest path: {name}')
        expected[name] = record
    actual = {}
    for path in tree_paths(root):
        name = path.relative_to(root).as_posix()
        if path.is_file() and name != 'resource_manifest.json':
            actual[name] = {'path': name, 'bytes': path.stat().st_size, 'sha256': sha256(path)}
    if expected != actual:
        raise ValueError('Windows payload differs from its manifest')
    if ('ResponsiveClassroom.exe' not in actual or not (root / '_internal').is_dir()
            or not any(name.startswith('_internal/') for name in actual)):
        raise ValueError('Windows payload must contain the EXE and complete _internal directory')
    return manifest


def verify_macos(root, version):
    manifest_file = root / 'resource_manifest.json'
    if manifest_file.is_symlink() or not isinstance(json.loads(manifest_file.read_text(encoding='utf-8')), dict):
        raise ValueError('macOS manifest must be a regular JSON object file')
    manifest = verify_tree(root, version=version, architecture='universal2')
    bundles = sorted(path.name for path in root.glob('*.app'))
    if bundles != ['ResponsiveClassroom.app']:
        raise ValueError('macOS payload must contain exactly one ResponsiveClassroom.app at its root')
    bundle = root / bundles[0]
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError('macOS application must be a regular bundle directory')
    for path in tree_paths(bundle):
        if path.is_symlink():
            check_symlink(path, bundle)
    executable = bundle / 'Contents/MacOS/ResponsiveClassroom'
    if (executable.is_symlink() or not executable.is_file()
            or not executable.stat().st_mode & 0o111):
        raise ValueError('macOS application executable is missing or not executable')
    with (bundle / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    if (info.get('CFBundleVersion') != version or info.get('CFBundleShortVersionString') != version
            or info.get('LSMinimumSystemVersion') != '14.0'):
        raise ValueError('macOS version/minimum-system metadata mismatch')
    return manifest


def readme(version):
    return f'''Responsive Classroom {version} - Windows + macOS 桌面候選包

Windows x64：完整解壓縮後，開啟 Windows/ResponsiveClassroom.exe。
請保留 Windows/_internal 及其他檔案，不要只移動 EXE；不需另裝 Python。

macOS 14 或更新版本：Intel 與 Apple Silicon 共用一個 universal2 App。
請用 macOS「封存工具程式」或 ditto 解壓縮，以保留符號連結及執行權限，
再開啟 macOS/ResponsiveClassroom.app。請保持整個 App 完整。
原生模型／介面自動測試與人工驗收不同；兩種 Mac 都仍需檢查 Finder 啟動、
麥克風／區域網路權限、實體音訊裝置及 Gatekeeper。

這是候選包，尚未核准公開發佈。Mac App 沒有 Apple Developer ID 簽章，
也未公證；臨時簽章不代表 Gatekeeper 會接受。如果系統阻擋，請停止並向
維護者取得正式簽章／公證版本。不要關閉 Gatekeeper、Chromium 沙箱或
其他安全保護。Blue Danube 錄音的再散布授權尚未獨立確認；來源標註
不等於授權，本次封裝不新增媒體授權。請保留所有原始授權與第三方聲明。

desktop_manifest.json 記錄檔案、權限、連結及輸入 ZIP 的 SHA-256；
旁邊的 .zip.sha256 可驗證完整 ZIP。封裝檢查不等於原生功能或人工驗收。

English
Windows x64: extract everything and open Windows/ResponsiveClassroom.exe.
Keep _internal and all other files alongside the EXE. Python is not needed.

macOS 14+ (Intel x86_64 and Apple Silicon arm64): one universal2 application,
macOS/ResponsiveClassroom.app. Extract with macOS Archive Utility or ditto to
retain symlinks and executable modes. Keep the complete app intact. Native
model/UI smoke tests are separate from manual Finder, microphone/local-network
consent, physical-device and Gatekeeper acceptance on both architectures.

This candidate is not an approved public release. The macOS app has no Apple
Developer ID signature and is not notarized. Ad-hoc signing does not guarantee
Gatekeeper acceptance. If blocked, stop and obtain a properly signed/notarized
build from the maintainer. Do not disable Gatekeeper, Chromium's sandbox or
other system protections.

Original notices, licenses and platform manifests are retained. Blue Danube
recording redistribution rights have not been independently verified. Source
attribution does not grant recording rights. Assembly grants no new media
license and does not clear public distribution.

The desktop manifest records payload files, modes, links and input ZIP hashes.
The adjacent .zip.sha256 checks this ZIP. Assembly integrity checks do not prove
native builds or acceptance passed; supplied native test evidence is separate.
'''.encode()


def desktop_records(root):
    return [record for record in tree_records(root) if record['path'] != MANIFEST]


def write_deterministic_archive(root, archive):
    """Normalize ZIP timestamps/order, while preserving payload bytes and modes."""
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in sorted(tree_paths(root)):
            name = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            info = zipfile.ZipInfo(name + ('/' if stat.S_ISDIR(mode) else ''), FIXED_TIME)
            info.create_system = 3
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            if stat.S_ISDIR(mode):
                info.external_attr |= 0x10
                zipped.writestr(info, b'')
            elif stat.S_ISLNK(mode):
                zipped.writestr(info, check_symlink(path, root).encode('utf-8'))
            else:
                with path.open('rb') as source, zipped.open(info, 'w', force_zip64=True) as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)


def assemble(windows, macos, output, *, version, validation_evidence=None):
    if os.name != 'posix':
        raise ValueError('Assemble on macOS or Linux with a POSIX filesystem')
    if not re.fullmatch(r'(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)', version):
        raise ValueError('Expected a plain release version such as 3.0.3')
    windows, macos, output = Path(windows), Path(macos), Path(output)
    checksum = output.with_suffix(output.suffix + '.sha256')
    if output.suffix.lower() != '.zip' or output.exists() or output.is_symlink() or checksum.exists() or checksum.is_symlink():
        raise ValueError('Output must be a new .zip path with no existing checksum')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.desktop-candidate-', dir=output.parent) as temporary:
        temporary = Path(temporary)
        root = temporary / 'payload'
        root.mkdir()
        expanded = extract_checked(windows, root / 'Windows', windows=True, max_bytes=MAX_EXPANDED_BYTES)
        expanded += extract_checked(macos, root / 'macOS', windows=False, max_bytes=MAX_EXPANDED_BYTES - expanded)
        verify_windows(root / 'Windows', version)
        verify_macos(root / 'macOS', version)
        # Stable synthetic parent directory modes, independent of the runner umask.
        for path in tree_paths(root / 'Windows'):
            if path.is_dir():
                path.chmod(0o755)
        for path in (root / 'Windows', root / 'macOS'):
            path.chmod(0o755)
        (root / 'README.txt').write_bytes(readme(version))
        (root / 'README.txt').chmod(0o644)
        manifest = {
            'schema_version': 1, 'app': 'Responsive Classroom', 'version': version,
            'kind': 'combined-desktop-candidate',
            'platforms': ['windows-x64', 'macos-universal2'],
            'macos': {'minimum_system_version': '14.0', 'architectures': ['arm64', 'x86_64'],
                      'developer_id_signed': False, 'notarized': False,
                      'manual_acceptance': 'Finder-TCC-Gatekeeper-checks-not-established-by-assembly'},
            'publication': 'not-performed', 'validation_scope': 'archive-integrity',
            'media_rights': {'blue_danube_recording': 'redistribution-rights-not-independently-verified'},
            'inputs': {'windows': {'bytes': windows.stat().st_size, 'sha256': sha256(windows)},
                       'macos': {'bytes': macos.stat().st_size, 'sha256': sha256(macos)}},
            'files': desktop_records(root),
        }
        if validation_evidence is not None:
            evidence = json.loads(Path(validation_evidence).read_text(encoding='utf-8'))
            if not isinstance(evidence, dict) or not evidence:
                raise ValueError('Validation evidence must be a nonempty JSON object')
            manifest['validation_evidence'] = {'sha256': sha256(Path(validation_evidence)), 'report': evidence}
        (root / MANIFEST).write_bytes(json_bytes(manifest))
        (root / MANIFEST).chmod(0o644)
        expanded += (root / MANIFEST).stat().st_size + (root / 'README.txt').stat().st_size
        if expanded >= MAX_EXPANDED_BYTES:
            raise ValueError('Combined expanded payload exceeds the 8 GiB safety cap')
        candidate = temporary / 'combined.zip'
        write_deterministic_archive(root, candidate)
        if candidate.stat().st_size >= MAX_BYTES:
            raise ValueError('Combined ZIP exceeds the 2 GiB size cap')
        # Round trip with our safe extractor: verify every mode, byte and link,
        # then revalidate the original platform manifests inside the final ZIP.
        restored = temporary / 'round-trip'
        extract_checked(candidate, restored, windows=False, max_bytes=MAX_EXPANDED_BYTES)
        if (desktop_records(restored) != manifest['files']
                or (restored / MANIFEST).read_bytes() != json_bytes(manifest)):
            raise ValueError('Combined ZIP round-trip differs from its manifest')
        verify_windows(restored / 'Windows', version)
        verify_macos(restored / 'macOS', version)
        digest = sha256(candidate)
        report = {'status': 'ok', 'version': version, 'candidate': True,
                  'archive': output.name, 'archive_bytes': candidate.stat().st_size,
                  'expanded_bytes': expanded, 'archive_sha256': digest,
                  'manifest_entries': len(manifest['files']),
                  'symlinks': sum(record['type'] == 'symlink' for record in manifest['files']),
                  'validation_scope': 'archive-integrity', 'published': False}
        # Create-only publication within this local artifact directory. Inputs and
        # existing releases are never replaced, including if another job races us.
        os.link(candidate, output)
        with checksum.open('x', encoding='ascii') as stream:
            stream.write(f'{digest}  {output.name}\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--windows', required=True, type=Path, help='Verified Windows x64 portable ZIP')
    parser.add_argument('--macos', required=True, type=Path, help='Verified universal2 macOS ZIP')
    parser.add_argument('--output', required=True, type=Path, help='New combined candidate ZIP')
    parser.add_argument('--version', required=True, help='Exact version shared by both payloads')
    parser.add_argument('--report', type=Path, help='Optional assembly-only integrity report JSON')
    parser.add_argument('--validation-evidence', type=Path, help='Optional existing native test evidence JSON to retain')
    args = parser.parse_args(argv)
    try:
        if args.report and args.report.resolve() in {
                args.windows.resolve(), args.macos.resolve(), args.output.resolve(),
                args.output.with_suffix(args.output.suffix + '.sha256').resolve()}:
            raise ValueError('Report path must not overwrite an input or output artifact')
        if args.report and args.validation_evidence and args.report.resolve() == args.validation_evidence.resolve():
            raise ValueError('Report path must not overwrite validation evidence')
        report = assemble(args.windows, args.macos, args.output, version=args.version,
                          validation_evidence=args.validation_evidence)
    except (OSError, TypeError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        print(f'Assembly failed: {exc}', file=sys.stderr)
        return 1
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(json_bytes(report))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
