"""Create the shared responsive_classroom Python development environment."""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', action='store_true', help='Download and verify offline models')
    parser.add_argument('--dev', action='store_true', help='Install audit tools as well')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error('Use Python 3.12 for the pinned desktop runtime.')
    root = Path(__file__).resolve().parents[2]
    environment = root / '.venv'
    python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    requirements = root / 'source' / ('requirements-dev.txt' if args.dev else 'requirements.txt')
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
    if args.models:
        subprocess.run([str(python), str(root / 'source/tools/fetch_models.py')], check=True)
    print('responsive_classroom environment ready in .venv')


if __name__ == '__main__':
    main()
