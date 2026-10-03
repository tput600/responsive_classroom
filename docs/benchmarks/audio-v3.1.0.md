# v3.1.0 audio pipeline verification

## Scope and limits

These are **synthetic smoke benchmarks, not natural classroom recognition accuracy**.
SenseVoice matched the intended command in **7 of 18 speech cases in both revisions**.
The eSpeak NG voice is deliberately not a recording of a person; several short
words are poorly transcribed. No claim of improved ASR accuracy is supported.
No user microphone, physical audio device, speaker, or external speech API was used.
A single microphone cannot separate overlapping speech/environmental sources or
identify teachers versus students. VAD detects likely speech, not speaker identity.

## Reproduction

- Baseline: `cbfc097`, `source/classroom_audio.py`; current: v3.1.0 audio implementation.
- Python 3.12, sherpa-onnx 1.13.8, identical pinned SenseVoice/Silero resources.
- Optional development-only dependency: `espeakng-loader==0.2.4`; eSpeak NG is
  GPL-3.0 software and is **not bundled in the application**.
- Original English text, eSpeak `en-us`, 150 words/minute; 16 kHz mono, 10 ms frames.
- Six utterances, each clean, 0.1 amplitude, and deterministic white-noise 10 dB SNR.
- Four non-speech cases: white noise, 120 Hz fan-like hum, brief random impacts,
  digital silence. Seed 73. Each JSON includes every waveform SHA-256.
- No fixture was removed to improve the reported result; both JSONs retain failures.

From `source/`, with the optional generator installed in the active environment:

```sh
git show cbfc097:source/classroom_audio.py > /tmp/classroom_audio_baseline31.py
python tools/benchmark_audio.py --module /tmp/classroom_audio_baseline31.py --models resources/models/sensevoice --output baseline.json
python tools/benchmark_audio.py --models resources/models/sensevoice --output new.json
python -m unittest tests.test_audio_pipeline -v
```

The committed JSONs are the local Linux cloud run. Wall-clock timings vary with
CPU load and scheduling; they are not device-specific performance guarantees.

## Results

Raw speech-guard latency, seconds (18 paired speech variants):

| Metric | Baseline min / median / p95 / max | v3.1.0 min / median / p95 / max |
|---|---|---|
| First guard onset after acoustic onset | .270 / .327 / .593 / .780 | .140 / .191 / .329 / .660 |
| Last guard-active frame after acoustic end | .343 / .473 / .817 / .867 | .183 / .313 / .657 / .707 |

Onset improved by 120–380 ms in paired cases (median 130 ms). The raw VAD tail
was 160 ms shorter in every case. Notice mode still adds a **350 ms recovery
hold** after raw VAD activity; this intentional pause protection is not included
in the raw-tail row. Discussion mode deliberately continues grading strong or
sustained speech as classroom activity. These policies are unchanged.

All 7 recognized speech cases had **identical first-command audio timestamps**
in both revisions. The 4 non-speech fixtures produced no speech-guard activity
and no accepted commands in either revision. This limited set cannot establish
real-world false-positive/false-negative rates.

Synthetic real-time replay of the full callback → resampler → VAD → bounded queue
→ SenseVoice → command pipeline (one run each, seconds from first callback;
includes 500 ms leading silence):

| Fixture | Baseline | v3.1.0 |
|---|---:|---:|
| Wrong, clean | 1.120 | 1.130 |
| Continuous sentence, clean | 1.457 | 1.484 |

The new runs dropped zero capture frames. These timings demonstrate the tested
pipeline stays responsive; they do **not** show a meaningful ASR latency gain.
They exclude model load (both runs waited for ready) and physical device latency.
A faster command-VAD onset was tested and rejected because it regressed some
short/paused synthetic commands; the existing 200 ms onset / 320 ms endpoint
settings remain intact. Only the independent raw noise VAD uses 96 / 160 ms.

## Correctness improvements verified independently of speech models

- Capture frames retain callback monotonic time and sequence number. More than
  150 ms queued age is dropped; missing frames reset resampler, VAD, command
  context, noise holds and result epoch instead of stitching discontinuous words.
- After a gap/overload, commands remain blocked and the speech guard is explicitly
  unavailable until 500 ms uninterrupted fresh input. Normal noise-only fallback
  remains available with an explicit warning rather than falsely claiming speech
  discrimination. A simulated 100-stale-frame overload followed by 70 fresh frames
  processed all 70 fresh frames, restored readiness, and cleared overload.
- At most one final utterance and one latest live probe wait for decode. One
  hundred subsequent live probes cannot erase the pending final endpoint.
- Endpoint completion closes the rolling utterance identity, including short
  inter-utterance pauses; long pauses retain only 480 ms onset context instead
  of carrying a previous spoken command in the 1.2 s live window.
- Manual revision, stopped listening, capture overload and result age prevent
  stale command application. Stopping never waits on model inference by default.
- Notice speech/tail exclusion, Discussion grading, unavailable-VAD fallback and
  fresh environmental-noise resumption have model-independent regression tests.

Diagnostics exposed for the UI: capture timestamp, latest capture lag, dropped
frame count, overload flag, speech excluded and speech guard ready. A cumulative
drop count is historical; the overload flag identifies current recovery state.
