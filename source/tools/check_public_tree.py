"""Fail before pushing private runtime files, secrets, or machine-specific paths."""
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    executable = shutil.which('git')
    if not executable:
        raise RuntimeError('Git is required to inspect public files')
    top = subprocess.run([executable, 'rev-parse', '--show-toplevel'], cwd=root,
                         check=True, capture_output=True, text=True)  # nosec B603
    if Path(top.stdout.strip()).resolve() != root:
        raise RuntimeError('Run the public check from the project Git checkout')
    # The resolved Git executable receives fixed commands; no shell is used.
    result = subprocess.run([executable, 'ls-files', '-z'], cwd=root, check=True, capture_output=True)  # nosec B603
    names = result.stdout.decode('utf-8').split('\0')
    if not any(names):
        raise RuntimeError('The public Git index is empty; add reviewed source files first')
    forbidden_roots = {'.codex', '.venv', '_internal', 'dist', 'build', 'artifacts', 'sessions'}
    patterns = (
        re.compile(rb'(?i)\b[A-Z]:[/\\](?:Users|programing|programming)[/\\]'),
        re.compile(rb'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b'),
        re.compile(rb'\bgithub_pat_[A-Za-z0-9_]{30,}\b'),
        re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    )
    failures = []
    for name in filter(None, names):
        relative = Path(name)
        if (relative.parts[0] in forbidden_roots or relative.name in {'settings.json', '.env'}
                or relative.suffix.lower() in {'.zip', '.exe', '.log', '.bak', '.onnx'}
                or name.startswith('source/resources/fixtures/')):
            failures.append(f'{name}: private/generated/unapproved asset is tracked')
            continue
        path = root / relative
        if path.is_symlink() or not path.is_file():
            failures.append(f'{name}: must be a regular file')
            continue
        data = path.read_bytes()
        if len(data) > 25 * 1024 * 1024:
            failures.append(f'{name}: large file belongs in a reviewed release')
        if any(pattern.search(data) for pattern in patterns):
            failures.append(f'{name}: secret or machine-specific path detected')
    if failures:
        print('\n'.join(failures), file=sys.stderr)
        return 1
    print(f'Public tree checked: {len(list(filter(None, names)))} files; no prohibited data found.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
