"""Local runtime paths and bundled offline speech checks."""
from __future__ import annotations
import hashlib
import json
import os
import platform
from pathlib import Path
import sys
import wave
from classroom_core import Settings
from classroom_audio import CommandParser
APP_NAME = "Responsive Classroom"
VERSION = "3.0.1"

def config_dir():
    if os.name == 'nt':
        return Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) / APP_NAME
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support' / APP_NAME
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'responsive-classroom'


def application_root():
    if not getattr(sys, 'frozen', False):
        return Path(__file__).resolve().parent / "resources"
    extracted = Path(sys._MEIPASS)
    resources = Path(sys.executable).resolve().parents[1] / 'Resources'
    return resources if (resources / 'models').is_dir() else extracted


def model_dir():
    return application_root() / 'models/sensevoice'


def default_rest_source(root=None):
    """Keep a local supplied cue; public checkouts use the original demo cue."""
    resources = Path(root) if root is not None else application_root()
    local = resources / 'audio/blue-danube.mp3'
    return local if local.is_file() else resources / 'audio/default-rest.mp3'


def verify_model_assets():
    manifest = model_dir() / 'manifest.json'
    if not manifest.is_file():
        raise FileNotFoundError('缺少離線語音資源清單，請使用完整程式資料夾')
    entries = json.loads(manifest.read_text(encoding='utf-8'))['files']
    results = {}
    for name, expected in entries.items():
        path = model_dir() / name
        digest = hashlib.sha256()
        if path.is_file():
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
        results[name] = path.is_file() and digest.hexdigest() == expected
    return results


def self_test_report(path):
    results = {'python_version': platform.python_version(), 'model_assets': verify_model_assets()}
    if not results['model_assets'] or not all(results['model_assets'].values()):
        raise RuntimeError('SenseVoice 模型檔案與 manifest 驗證失敗')
    from classroom_audio import SenseVoiceSpeech
    speech = SenseVoiceSpeech(model_dir())
    results['silent_audio_empty'] = speech.decode([0.0] * 16000) == ''
    parser = CommandParser(Settings(), cooldown_seconds=0)
    results['text_parser'] = {
        'chinese_question': parser.parse('課堂提問').intent == 'QUESTION',
        'english_question': parser.parse('question').intent == 'QUESTION',
        'optional_prefix': parser.parse('class question').intent == 'QUESTION',
        'english_discussion': parser.parse('discussion').intent == 'DISCUSSION',
        'chinese_discussion': parser.parse('討論').intent == 'DISCUSSION',
        'embedded_question': parser.parse('one two three question').intent == 'QUESTION',
        'embedded_wrong': parser.parse('you are wrong').intent == 'WRONG',
    }
    import numpy as np
    import soxr

    def decode_fixture(name, expected, start_seconds=0):
        source = application_root() / 'fixtures' / name
        with wave.open(str(source), 'rb') as audio:
            channels, width, rate, count = audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()
            if width != 2:
                raise ValueError(f'{source.name} 必須使用 PCM16 音訊')
            samples = np.frombuffer(audio.readframes(count), dtype='<i2').astype(np.float32) / 32768
            if channels > 1:
                samples = samples.reshape(-1, channels).mean(axis=1)
        if rate != 16000:
            samples = soxr.resample(samples, rate, 16000).astype(np.float32)
        samples = samples[int(start_seconds * 16000):]
        raw = speech.decode(samples)
        result = parser.parse(raw)
        speech.reset()
        padded = np.concatenate((np.zeros(16000, dtype=np.float32), samples, np.zeros(16000, dtype=np.float32)))
        segments = []
        for offset in range(0, len(padded), 160):
            segments.extend(speech.accept(padded[offset:offset + 160]))
        streaming = [parser.parse(speech.decode(segment)) for segment in segments]
        return {'synthetic': True, 'fixture_start_seconds': start_seconds, 'raw_text': raw,
                'corrected_text': result.corrected_text, 'intent': result.intent,
                'expected_intent': expected, 'streaming_text': [item.text for item in streaming],
                'streaming_intents': [item.intent for item in streaming],
                'passed': result.intent == expected and len(streaming) == 1 and streaming[0].intent == expected}

    fixtures = {
        'en_sentence_question': ('en-sentence-question.wav', 'QUESTION', 0),
        'en_sentence_wrong': ('en-sentence-wrong.wav', 'WRONG', 0),
        'zh': ('zh-command.wav', 'NOTICE', 0),
        'en': ('en-command.wav', 'QUESTION', 0),
        'en_bare': ('en-command.wav', 'QUESTION', .55),
        'en_notice': ('en-notice.wav', 'NOTICE', 0),
        'en_discussion': ('en-discussion.wav', 'DISCUSSION', 0),
    }
    results['synthetic_voice_available'] = all(
        (application_root() / 'fixtures' / filename).is_file()
        for filename, _, _ in fixtures.values())
    results['synthetic_voice'] = ({key: decode_fixture(*spec) for key, spec in fixtures.items()}
                                if results['synthetic_voice_available'] else {})
    if not results['synthetic_voice_available']:
        results['voice_fixture_note'] = 'Private SAPI fixtures are not distributed; model inference and parser checks ran.'
    Path(path).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    passed = (results['silent_audio_empty'] and all(results['text_parser'].values()) and
              all(item['passed'] for item in results['synthetic_voice'].values()))
    if not passed:
        raise RuntimeError(f'自我檢查失敗：{path}')
