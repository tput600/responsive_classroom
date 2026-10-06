import math
import os
import struct
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat, QMediaPlayer

from classroom_core import Settings
from classroom_playback import AudioPlayback
from pattern_renderer import music_wave


class MusicEnvelopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    @staticmethod
    def buffer(sample_format, data, channels=1, rate=48_000):
        audio_format = QAudioFormat()
        audio_format.setSampleRate(rate)
        audio_format.setChannelCount(channels)
        audio_format.setSampleFormat(sample_format)
        return QAudioBuffer(data, audio_format)

    def test_pcm_formats_are_normalized_interleaved_and_dc_safe(self):
        cases = (
            (QAudioFormat.SampleFormat.UInt8, bytes((64, 192)), 0.5),
            (QAudioFormat.SampleFormat.Int16, struct.pack("=hh", -8192, 8192), 0.25),
            (QAudioFormat.SampleFormat.Int32, struct.pack("=ii", -536870912, 536870912), 0.25),
            (QAudioFormat.SampleFormat.Float, struct.pack("=ff", -0.2, 0.2), 0.2),
        )
        for sample_format, raw, expected in cases:
            with self.subTest(sample_format=sample_format):
                pcm = self.buffer(sample_format, raw, channels=1)
                self.assertAlmostEqual(AudioPlayback._buffer_rms(pcm), expected, places=3)

        stereo = self.buffer(QAudioFormat.SampleFormat.Float,
                             struct.pack('=ffff', -.5, .25, .5, -.25), channels=2)
        self.assertAlmostEqual(AudioPlayback._buffer_rms(stereo), math.sqrt(.15625), places=4)

        dc = self.buffer(QAudioFormat.SampleFormat.Float, struct.pack("=ff", 0.35, 0.35))
        self.assertEqual(AudioPlayback._buffer_rms(dc), 0.0)
        non_finite = self.buffer(QAudioFormat.SampleFormat.Float,
                                 struct.pack("=ff", float("nan"), float("inf")))
        self.assertEqual(AudioPlayback._buffer_rms(non_finite), 0.0)

    def test_level_tracks_active_preview_mode_and_clears_when_stale_or_stopped(self):
        class FakePlayer:
            playing = True
            def playbackState(self):
                return QMediaPlayer.PlaybackState.PlayingState if self.playing else QMediaPlayer.PlaybackState.StoppedState

            def stop(self):
                self.playing = False

            def setSource(self, _source):
                pass

        class FakeOutput:
            def volume(self):
                return 0.5

        with tempfile.TemporaryDirectory() as directory:
            playback = AudioPlayback(Path(directory), Settings(), enabled=False)
            playback._player = FakePlayer()
            playback._output = FakeOutput()
            playback._playing_mode = "discussion"
            playback._reference_mode = "discussion"
            pcm = self.buffer(QAudioFormat.SampleFormat.Float,
                              struct.pack("=ff", -0.5, 0.5), rate=1000)

            self.assertFalse(playback.has_music_signal)
            playback._on_audio_buffer(QAudioBuffer())
            self.assertFalse(playback.has_music_signal)
            playback._on_audio_buffer(pcm)
            self.assertTrue(playback.has_music_signal)
            self.assertGreater(playback.music_level, 0.0)
            self.assertLessEqual(playback.music_level, 0.5)
            self.assertAlmostEqual(playback.noise_reference_power, 0.0625)
            self.assertEqual(playback.playing_mode, "discussion")

            playback._last_audio_buffer -= 1.0
            playback._expire_music_level()
            self.assertFalse(playback.has_music_signal)
            self.assertEqual(playback.music_level, 0.0)

            playback._playing_mode = "preview:notice"
            playback._clear_music_signal()
            self.assertIsNone(playback.playing_mode)
            self.assertEqual(playback.music_level, 0.0)
            self.assertEqual(playback.noise_reference_power, 0.0)
            playback.close()

    def test_quiet_and_loud_passages_have_strong_smooth_brightness_contrast(self):
        class Player:
            def playbackState(self):
                return QMediaPlayer.PlaybackState.PlayingState

        class Output:
            def volume(self):
                return .5

        with tempfile.TemporaryDirectory() as directory:
            playback = AudioPlayback(Path(directory), Settings(), enabled=False)
            playback._player, playback._output = Player(), Output()
            playback._playing_mode = 'rest'
            def feed(amplitude, count):
                pcm = self.buffer(QAudioFormat.SampleFormat.Float,
                                  struct.pack('=ff', -amplitude, amplitude), rate=100)
                levels = []
                for _ in range(count):
                    playback._on_audio_buffer(pcm)
                    levels.append(playback.music_level)
                return levels
            feed(.10, 45)
            quiet = feed(.008, 45)[-1]
            rising = feed(.09, 45)
            loud = rising[-1]
            frame = bytes((64, 32, 8)) * 64
            dim, bright = music_wave(frame, quiet, 0), music_wave(frame, loud, 0)
            self.assertGreater(sum(bright) / sum(dim), 5)
            self.assertLess(max(abs(a-b) for a,b in zip(rising,rising[1:])), .13)
            self.assertTrue(all(a <= b for a,b in zip(bright,frame)))
            # Keep this fake player out of close(), which stops real Qt players.
            playback._player = None
            playback.close()

    @unittest.skipUnless((Path(__file__).parents[1] / "resources" / "audio" / "blue-danube.mp3").is_file(),
                         "bundled MP3 fixture is unavailable")
    def test_bundled_mp3_produces_pcm_with_muted_output(self):
        source = Path(__file__).parents[1] / "resources" / "audio" / "blue-danube.mp3"
        decoded = []
        with tempfile.TemporaryDirectory() as directory:
            playback = AudioPlayback(Path(directory), Settings(audio_fade_ms=0),
                                     bundled_rest_source=source)
            if playback._buffer_output is None:
                self.skipTest("QAudioBufferOutput is unavailable in this Qt build")
            playback._output.setMuted(True)
            playback._buffer_output.audioBufferReceived.connect(
                lambda buffer: decoded.append(AudioPlayback._buffer_rms(buffer))
            )
            playback.preview("rest")
            # The bundled recording starts almost silently. Decode a fixed
            # musical passage so startup timing cannot change this assertion.
            load_deadline=time.monotonic()+5.0
            while time.monotonic()<load_deadline and playback._player.mediaStatus() not in (
                    QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia,
                    QMediaPlayer.MediaStatus.InvalidMedia):
                self.app.processEvents()
                time.sleep(.01)
            playback._player.setPosition(30_000)
            decoded.clear()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not any(level > 1e-5 for level in decoded):
                self.app.processEvents()
                time.sleep(0.01)
            peak = max(decoded, default=0.0)
            mode = playback.playing_mode
            playback.close()

        self.assertGreater(peak, 1e-5, "muted MP3 decoding produced no nonzero PCM callbacks")
        self.assertEqual(mode, "rest")


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    unittest.main()
