"""Symlink-aware macOS release staging and verification (also testable on Linux)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

MANIFEST = 'resource_manifest.json'


def sha256_stream(stream):
    return hashlib.file_digest(stream, 'sha256').hexdigest()


def tree_paths(root):
    """Include empty directories and links, without descending into linked directories."""
    for directory, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        for name in sorted(directories + files):
            yield Path(directory) / name


def safe_path(name):
    if (not isinstance(name, str) or not name or '\\' in name or '\x00' in name
            or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError(f'Unsafe package path: {name!r}')
    return PurePosixPath(name)


def check_symlink(path, root):
    target = os.readlink(path)
    if not target or os.path.isabs(target) or '\\' in target:
        raise ValueError(f'Nonportable symlink: {path}')
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f'Broken or cyclic symlink: {path}') from exc
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f'Symlink escapes package: {path}')
    return target


def tree_records(root):
    records = []
    for path in sorted(tree_paths(root)):
        name = path.relative_to(root).as_posix()
        safe_path(name)
        if name == MANIFEST:
            continue
        mode = path.lstat().st_mode
        record = {'path': name, 'mode': stat.S_IMODE(mode)}
        if stat.S_ISLNK(mode):
            target = check_symlink(path, root)
            data = target.encode('utf-8')
            record.update(type='symlink', target=target, bytes=len(data),
                          sha256=hashlib.sha256(data).hexdigest())
        elif stat.S_ISREG(mode):
            with path.open('rb') as stream:
                record.update(type='file', bytes=path.stat().st_size,
                              sha256=sha256_stream(stream))
        elif stat.S_ISDIR(mode):
            record.update(type='directory')
        else:
            raise ValueError(f'Unsupported package entry: {path}')
        records.append(record)
    return records


def stage_release(bundle, destination, extras, *, version, architecture, python_version):
    """Copy a sealed bundle unchanged; documentation/manifest are its siblings."""
    if architecture not in ('arm64', 'x86_64'):
        raise ValueError(f'Unsupported macOS architecture: {architecture}')
    if bundle.suffix != '.app' or not bundle.is_dir() or bundle.is_symlink():
        raise ValueError('Expected a regular .app bundle directory')
    # Bundle links must remain within the bundle, even when release extras exist.
    for path in tree_paths(bundle):
        if path.is_symlink():
            check_symlink(path, bundle)
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(bundle, destination / bundle.name, symlinks=True)
    for extra in extras:
        if extra.name in (bundle.name, MANIFEST):
            raise ValueError(f'Reserved release filename: {extra.name}')
        shutil.copyfile(extra, destination / extra.name)
    manifest = {'app': 'Responsive Classroom', 'version': version,
                'platform': f'macos-{architecture}', 'python_version': python_version,
                'files': tree_records(destination)}
    (destination / MANIFEST).write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return destination / bundle.name


def verify_tree(root, *, version, architecture):
    manifest_path = root / MANIFEST
    if manifest_path.is_symlink():
        raise ValueError('Release manifest must be a regular file')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('version') != version or manifest.get('platform') != f'macos-{architecture}':
        raise ValueError('Release manifest version/platform mismatch')
    if not manifest.get('files') or manifest['files'] != tree_records(root):
        raise ValueError('Release tree differs from its manifest (content, mode, link, or path)')
    return manifest


def write_archive(root, archive):
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in sorted(tree_paths(root)):
            name = path.relative_to(root).as_posix()
            safe_path(name)
            if path.is_symlink():
                target = check_symlink(path, root)
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = path.lstat().st_mode << 16
                zipped.writestr(info, target.encode('utf-8'))
            else:
                # ZipFile.write retains Unix modes, including the executable bit.
                zipped.write(path, name)


def verify_archive(root, archive, *, version, architecture):
    manifest = verify_tree(root, version=version, architecture=architecture)
    records = {record['path']: record for record in manifest['files']}
    manifest_path = root / MANIFEST
    with manifest_path.open('rb') as stream:
        records[MANIFEST] = {'type': 'file', 'bytes': manifest_path.stat().st_size,
                             'sha256': sha256_stream(stream),
                             'mode': stat.S_IMODE(manifest_path.stat().st_mode)}
    with zipfile.ZipFile(archive) as zipped:
        infos = zipped.infolist()
        names = [entry.filename.rstrip('/') for entry in infos]
        for name in names:
            safe_path(name)
        if len(names) != len(set(names)) or set(names) != set(records):
            raise ValueError('ZIP paths differ from release manifest or contain duplicates')
        for info in infos:
            name = info.filename.rstrip('/')
            record = records[name]
            mode = info.external_attr >> 16
            kind = ('symlink' if stat.S_ISLNK(mode) else
                    'directory' if stat.S_ISDIR(mode) else
                    'file' if stat.S_ISREG(mode) else 'unsupported')
            if (info.create_system != 3 or kind != record['type']
                    or stat.S_IMODE(mode) != record['mode']
                    or info.is_dir() != (kind == 'directory')):
                raise ValueError(f'ZIP entry type/permissions differ: {name}')
            if kind == 'directory':
                if info.file_size or zipped.read(info):
                    raise ValueError(f'Nonempty ZIP directory: {name}')
                continue
            with zipped.open(info) as stream:
                digest = sha256_stream(stream)  # Reading verifies the ZIP CRC too.
            if info.file_size != record['bytes'] or digest != record['sha256']:
                raise ValueError(f'ZIP content differs from manifest: {name}')
    return {'status': 'ok', 'version': version, 'platform': f'macos-{architecture}',
            'manifest_entries': len(manifest['files']), 'zip_entries': len(infos),
            'symlinks': sum(r['type'] == 'symlink' for r in records.values())}
