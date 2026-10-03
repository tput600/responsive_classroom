import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from PySide6.QtCore import QUrl

from classroom_core import BaseMode, ClassroomState, DEFAULT_REST_AUDIO, NoiseState, RestStage, Settings
from classroom_playback import AudioPlayback


class AudioPlaybackTests(unittest.TestCase):
    def test_import_copies_supported_audio_into_mode_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            player = AudioPlayback(root / "data", Settings(), enabled=False)
            for extension in (".wav", ".MP3"):
                source = root / f"my cue{extension}"
                source.write_bytes(b"original audio bytes")
                stored = player.import_file("discussion", source)
                target = (root / "data" / stored).resolve()
                self.assertEqual(stored.split("/")[:2], ["audio", "discussion"])
                self.assertEqual(target.suffix.lower(), extension.lower())
                self.assertEqual(target.read_bytes(), source.read_bytes())
                self.assertTrue(source.is_file())
            player.close()

    def test_cancelled_import_removes_partial_temporary_file(self):
        class CancelAfterFirstChunk:
            checks = 0

            def is_set(self):
                self.checks += 1
                return self.checks >= 3

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "large cue.wav"
            source.write_bytes(b"a" * (2 * 1024 * 1024))
            player = AudioPlayback(root / "data", Settings(), enabled=False)
            with self.assertRaises(InterruptedError):
                player.import_file("discussion", source, cancellation=CancelAfterFirstChunk())
            folder = root / "data" / "audio" / "discussion"
            self.assertEqual(list(folder.iterdir()), [])
            player.close()

    def test_mode_cue_dedupes_noise_updates_and_rest_music_pauses_detection(self):
        class Signal:
            def connect(self, callback):
                self.callback = callback

        class Output:
            def __init__(self, _parent=None):
                self.level = 0

            def setVolume(self, value):
                self.level = value

            def volume(self):
                return self.level

        class Player:
            class PlaybackState:
                StoppedState, PlayingState = 0, 1

            class Loops:
                Infinite = -1

            class MediaStatus:
                EndOfMedia = 7

            def __init__(self, _parent=None):
                self.errorOccurred = Signal()
                self.mediaStatusChanged = Signal()
                self.state = self.PlaybackState.StoppedState
                self.play_count = 0
                self.source = QUrl()

            def setAudioOutput(self, _output): pass
            def setLoops(self, loops): self.loops = loops
            def setSource(self, source): self.source = source
            def play(self): self.state = self.PlaybackState.PlayingState; self.play_count += 1
            def stop(self): self.state = self.PlaybackState.StoppedState
            def playbackState(self): return self.state

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "cue.wav"
            source.write_bytes(b"wav")
            settings = Settings(audio_fade_ms=0)
            with patch("classroom_playback.QMediaPlayer", Player), patch("classroom_playback.QAudioOutput", Output):
                playback = AudioPlayback(root / "data", settings)
                settings = Settings(audio_fade_ms=0, audio_files={
                    **Settings().audio_files,
                    "discussion": playback.import_file("discussion", source),
                    "rest": playback.import_file("rest", source),
                })
                playback.apply_settings(settings)
                playback.handle_state(ClassroomState(BaseMode.DISCUSSION))
                self.assertEqual(playback._player.play_count, 1)
                self.assertTrue(playback.suppress_detection)
                playback.handle_state(ClassroomState(BaseMode.DISCUSSION, noise_state=NoiseState.LOUD))
                self.assertEqual(playback._player.play_count, 1)
                playback.apply_settings(Settings(question_seconds=17, audio_fade_ms=0,
                                                 audio_files=settings.audio_files))
                self.assertEqual(playback._player.play_count, 1)
                playback.handle_state(ClassroomState(BaseMode.REST, rest_stage=RestStage.RESTING))
                self.assertEqual(playback._player.loops, Player.Loops.Infinite)
                self.assertTrue(playback.suppress_detection)
                playback.apply_settings(Settings(voice_enabled=True, audio_fade_ms=0,
                                                 audio_files=settings.audio_files))
                self.assertEqual(playback._player.playbackState(), Player.PlaybackState.StoppedState)
                playback.apply_settings(Settings(voice_enabled=False, audio_fade_ms=0,
                                                 audio_files=settings.audio_files))
                self.assertEqual(playback._player.loops, Player.Loops.Infinite)
                playback.close()

                default_player = AudioPlayback(root / "default-data", Settings(voice_enabled=True),
                                               bundled_rest_source=source)
                default_player.handle_state(ClassroomState(BaseMode.REST, rest_stage=RestStage.RESTING))
                copied = root / "default-data" / DEFAULT_REST_AUDIO
                self.assertEqual(copied.read_bytes(), source.read_bytes())
                self.assertEqual(default_player._player.loops, Player.Loops.Infinite)
                self.assertTrue(default_player.suppress_detection)
                default_player.close()

    def test_import_rejects_unknown_mode_and_unsupported_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "cue.ogg"
            source.write_bytes(b"audio")
            player = AudioPlayback(root / "data", Settings(), enabled=False)
            with self.assertRaises(ValueError):
                player.import_file("../../outside", source)
            with self.assertRaises(ValueError):
                player.import_file("notice", source)
            self.assertFalse((root / "data").exists())
            player.close()


if __name__ == "__main__":
    unittest.main()
