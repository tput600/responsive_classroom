"""Verify a portable onedir tree and its matching ZIP, including CRC and SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import zipfile
import zlib
from pathlib import Path, PurePosixPath


def sha256_stream(stream):
    digest = hashlib.sha256()
    crc = 0
    size = 0
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
        crc = zlib.crc32(block, crc)
        size += len(block)
    return size, digest.hexdigest(), crc & 0xFFFFFFFF


def safe_manifest_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"Invalid manifest path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"Unsafe manifest path: {value!r}")
    return path


def reject_symlink_path(root: Path, relative: PurePosixPath):
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Reparse/symlink entry is not allowed: {current}")


def verify(args):
    package = Path(args.package).resolve(strict=True)
    archive = Path(args.archive).resolve(strict=True)

    manifest_path = package / "resource_manifest.json"
    reject_symlink_path(package, PurePosixPath("resource_manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("version") != args.expected_version:
        raise ValueError(f"Expected manifest version {args.expected_version}, got {manifest.get('version')}")
    if manifest.get("platform") != "windows-x64":
        raise ValueError(f"Unexpected platform: {manifest.get('platform')}")

    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ValueError("Manifest has no file entries")
    by_path = {}
    for record in records:
        rel = safe_manifest_path(record.get("path"))
        name = rel.as_posix()
        if name in by_path:
            raise ValueError(f"Duplicate manifest path: {name}")
        if not isinstance(record.get("bytes"), int) or record["bytes"] < 0:
            raise ValueError(f"Invalid byte count for {name}")
        digest = record.get("sha256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid SHA-256 for {name}")
        by_path[name] = record

    expected_paths = set(by_path) | {"resource_manifest.json"}
    file_checks = []
    package_root = package
    for name, record in by_path.items():
        rel = PurePosixPath(name)
        reject_symlink_path(package_root, rel)
        target = (package_root / Path(*rel.parts)).resolve(strict=True)
        if not target.is_relative_to(package_root):
            raise ValueError(f"Manifest path escapes package: {name}")
        if not target.is_file():
            raise ValueError(f"Manifest entry is not a file: {name}")
        with target.open("rb") as stream:
            size, digest, _ = sha256_stream(stream)
        if size != record["bytes"] or digest != record["sha256"]:
            raise ValueError(f"Package file failed size/SHA check: {name}")
        file_checks.append({"path": name, "bytes": size, "sha256": digest})

    zip_checks = []
    with zipfile.ZipFile(archive, "r") as zipped:
        infos = zipped.infolist()
        names = [entry.filename for entry in infos]
        if len(names) != len(set(names)):
            raise ValueError("ZIP contains duplicate paths")
        if set(names) != expected_paths:
            missing = sorted(expected_paths - set(names))
            extra = sorted(set(names) - expected_paths)
            raise ValueError(f"ZIP path set differs from manifest; missing={missing[:8]}, extra={extra[:8]}")
        for info in infos:
            rel = safe_manifest_path(info.filename)
            if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                raise ValueError(f"ZIP contains symlink: {info.filename}")
            with zipped.open(info, "r") as stream:
                size, digest, crc = sha256_stream(stream)
            if size != info.file_size or crc != info.CRC:
                raise ValueError(f"ZIP CRC/size check failed: {rel.as_posix()}")
            if rel.as_posix() == "resource_manifest.json":
                if digest != hashlib.sha256(manifest_path.read_bytes()).hexdigest():
                    raise ValueError("ZIP resource manifest differs from package manifest")
            else:
                record = by_path[rel.as_posix()]
                if size != record["bytes"] or digest != record["sha256"]:
                    raise ValueError(f"ZIP payload differs from manifest: {rel.as_posix()}")
            zip_checks.append({"path": rel.as_posix(), "bytes": size,
                               "sha256": digest, "crc32": f"{crc:08x}"})

    if args.require_root_zip:
        root_zips = sorted(package.glob("ResponsiveClassroom-Portable-Windows-x64-v*.zip"))
        if len(root_zips) != 1 or root_zips[0].resolve() != archive:
            raise ValueError(f"Expected exactly one current root ZIP, found {[p.name for p in root_zips]}")

    with archive.open("rb") as stream:
        _, archive_sha256, _ = sha256_stream(stream)
    return {
        "status": "ok",
        "version": manifest["version"],
        "package": str(package),
        "archive": str(archive),
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": archive_sha256,
        "manifest_files": len(records),
        "zip_entries": len(zip_checks),
        "manifest_mismatches": 0,
        "zip_crc_failures": 0,
        "zip_path_mismatches": 0,
        "files": file_checks,
        "zip_files": zip_checks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--require-root-zip", action="store_true")
    args = parser.parse_args()
    report_path = Path(args.report)
    try:
        report = verify(args)
    except Exception as exc:
        report = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(report["error"], file=sys.stderr)
        return 1
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {report['manifest_files']} manifest files and {report['zip_entries']} ZIP entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
