"""Fetch upstream offline speech models, validating every installed SHA-256."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request
from urllib.parse import urlsplit

MODEL_URL = ('https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/'
             'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2')
VAD_URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(url, destination):
    if urlsplit(url).scheme != 'https' or urlsplit(url).hostname != 'github.com':
        raise ValueError('Only the pinned HTTPS upstream URLs are accepted')
    request = urllib.request.Request(url, headers={'User-Agent': 'Responsive-Classroom-Model-Setup'})
    # Both accepted upstream URLs are fixed HTTPS GitHub assets.
    with urllib.request.urlopen(request, timeout=60) as response, destination.open('wb') as stream:  # nosec B310
        if not response.url.startswith('https://'):
            raise RuntimeError('Model download redirected away from HTTPS')
        count = 0
        while block := response.read(1024 * 1024):
            count += len(block)
            if count > 1024 * 1024 * 1024:
                raise RuntimeError('Unexpected model archive size')
            stream.write(block)


def main():
    root = Path(__file__).resolve().parents[1] / 'resources/models/sensevoice'
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    expected, sizes = manifest['files'], manifest['sizes']
    if set(expected) != {'model.int8.onnx', 'tokens.txt', 'silero_vad.onnx'}:
        raise RuntimeError('Unexpected model manifest filenames')
    missing = [name for name, sha in expected.items()
               if not (root / name).is_file() or digest(root / name) != sha]
    if not missing:
        print('Offline model assets already match the pinned manifest.')
        return
    print('Downloading SenseVoiceSmall / Silero VAD. Their separate license terms are in resources/licenses.')
    with tempfile.TemporaryDirectory(prefix='classroom-models-') as temporary:
        stage = Path(temporary)
        if any(name != 'silero_vad.onnx' for name in missing):
            archive = stage / 'sensevoice.tar.bz2'
            download(MODEL_URL, archive)
            with tarfile.open(archive, 'r:bz2') as package:
                for name in missing:
                    if name == 'silero_vad.onnx':
                        continue
                    matches = [item for item in package.getmembers()
                               if item.isfile() and Path(item.name).name == name]
                    if len(matches) != 1 or matches[0].size != sizes[name]:
                        raise RuntimeError(f'Archive must contain exactly one regular {name}')
                    with package.extractfile(matches[0]) as source, (stage / name).open('wb') as target:
                        shutil.copyfileobj(source, target)
        if 'silero_vad.onnx' in missing:
            download(VAD_URL, stage / 'silero_vad.onnx')
        for name in missing:
            if digest(stage / name) != expected[name]:
                raise RuntimeError(f'Upstream asset hash changed: {name}; existing assets were retained.')
        for name in missing:
            destination = root / name
            partial = destination.with_suffix(destination.suffix + '.part')
            try:
                shutil.copyfile(stage / name, partial)
                partial.replace(destination)
            finally:
                partial.unlink(missing_ok=True)
    print('Offline models installed and SHA-256 verified.')


if __name__ == '__main__':
    main()
