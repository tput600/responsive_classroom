# Responsive Classroom 3.1.1

English · [繁體中文](README.md)

This manual covers the Web UI, WLED panel connections, microphone calibration, noise thresholds, voice commands, timers, custom audio, and local/cloud development. Users of the complete portable package do not need to install Python.

Select **EN** in the application's toolbar to use the English interface, or **中文** to switch back to Traditional Chinese. The links above switch the manual's language.

Source: [tput600/responsive_classroom](https://github.com/tput600/responsive_classroom). Executable packages: [GitHub Releases](https://github.com/tput600/responsive_classroom/releases). Download the portable ZIP from a Release. GitHub's **Download ZIP** button provides source code rather than a runnable application.

## 1. Prepare and start the application

Flash and configure WLED first, connect the panel to Wi-Fi, and configure an 8×8 matrix with 64 LEDs. The computer and panels must be on a network that allows them to communicate. This application does not flash firmware or perform the initial Wi-Fi setup. Campus or public Wi-Fi may isolate devices; use a network that permits communication between them.

Download and fully extract `ResponsiveClassroom-Portable-windows-x64-v3.1.1.zip`; Python is not required. Open `ResponsiveClassroom.exe` and keep the complete `_internal` directory and other files beside it; do not copy only the EXE. Starting with v3.1.1, only the Windows x64 portable package is maintained; the older Mac package is not updated.

Offline speech models, the Web UI, fonts, playback components, and microphone capture dependencies are included. Speech recognition runs locally. The SHA-256 sidecar verifies download integrity and is not a code signature.

## 2. Connect and orient light panels

Open **Connection**, enter your panel's IP address, and select **Add panel**. Alternatively, select **Auto search** in the same panel-adding area, check the discovered devices, and select **Add selected panels**. A single available subnet starts immediately; multiple subnets reveal a chooser instead of scanning every network. Discovery covers only the selected local subnet. Enter an IP address manually for devices outside it.

The **Sync** checkbox in each added panel's row determines whether that panel receives the classroom display. Up to eight panels can synchronize. Example addresses in the interface are format examples, not addresses that will work on your network.

Connection status initially shows that confirmation is pending. **Receiving confirmed** appears only after the device reports reception. Under **Panel orientation**, choose a panel, adjust rotation, serpentine wiring, and horizontal/vertical mirroring, then select **Save**. If a pattern appears as scattered dots or faces the wrong direction, check these settings and avoid applying the same pixel rearrangement twice in WLED and the application. Removing a panel deletes only its saved configuration in this application.

## 3. Choose a microphone and calibrate

On a new computer, open **Windows Settings → Privacy & security → Microphone** and allow microphone access for desktop apps. If a device saved on another computer is unavailable, the app falls back to this computer's system default input. You can also refresh and select a device on **Connection**. Confirm that **Live input level** changes, keep the microphone position and Windows input gain fixed, then select **Calibrate · 12 s** in a quiet room. The first two seconds are excluded from the sample.

Speech, clipping, interrupted input, or excessive background variation can cause calibration to fail. A failed calibration preserves the previous valid baseline.

The displayed value is the microphone's input level in dBFS, not a calibrated environmental dBA measurement. Classification uses the difference between the current input and the quiet baseline. The baseline remains fixed; sustained noise does not automatically raise it. Recalibrate after changing microphones, input gain, or classrooms.

## 4. Noise thresholds and adjustment

**Attention** is intended for listening to the teacher. **Discussion** allows more conversation. Each mode has separate activity thresholds and lower recovery thresholds.

| Threshold relative to the quiet baseline | Attention | Discussion |
|---|---:|---:|
| Enter growing activity | +8 dB | +12 dB |
| Enter active | +18 dB | +22 dB |
| Return from growing activity to steady | Below +5 dB | Below +9 dB |
| Return from active to growing activity | Below +14 dB | Below +17 dB |

For example, a baseline of −55 dBFS and an input of −35 dBFS produce a +20 dB difference. Attention has reached its active threshold. Discussion has reached growing activity but remains below its +22 dB active threshold. The same volume can therefore produce different classroom rhythms depending on the activity.

Calibrate first, then observe readings during teaching and student discussion. If a level triggers too easily, raise its entry threshold by about 2 dB at a time. If it rarely triggers, lower it by about 2 dB. Change one setting, observe one activity, and then decide whether another change is needed. Discussion thresholds cannot be lower than Attention thresholds.

If the lights repeatedly switch between adjacent levels, maintain a gap between entry and recovery thresholds and consider increasing the hold time. Do not compensate for a misplaced microphone or poor calibration by greatly increasing thresholds.

Default timing is 1.5 seconds to enter growing activity, 2 seconds to enter active, and 3 stable seconds to recover. Level smoothing is 750 ms. A brief impact does not change the state solely because of its instantaneous peak. Expand **Recovery thresholds and timing** with its disclosure arrow to change entry hold times, the shared recovery hold, smoothing, and each mode's recovery thresholds.

Select **Save settings** beside **Input and detection** to save noise thresholds, voice handling, command aliases, and recognition corrections together. All fields must validate before the settings are saved; an invalid alias or threshold cannot produce a partial save. Timers and playback have separate **Save timers** and **Save audio settings** buttons. Enter numeric values with the keyboard. The mouse wheel does not change parameters.

## 5. Seven modes and twelve light patterns

On **Classroom**, the left side shows panel, microphone, and voice status; the center shows the current mode, countdown, and live 8×8 display; the right side provides manual controls. A manual base-mode selection takes effect immediately and has priority over an older speech result that is still processing.

The preview and physical panels share the same pixel frame. Each panel applies its own orientation settings. Brightness is limited to 30%. Normal mode changes blend over 1.4 seconds; noise-level changes blend over 3 seconds. The lights provide gentle background rhythm without rapid warning flashes or warning symbols. A changing preview still needs device reception status and physical observation to confirm panel output.

| Mode or activity | Color and pattern | Motion |
|---|---|---|
| Standby | Soft warm gold | Slow drifting glow and breathing |
| Attention · steady | Central warm-green light | Smooth breathing |
| Attention · growing activity | Orange horizontal ribbon | Slow flow |
| Attention · active | Coral vertical curved wave | Slow movement |
| Discussion · steady | Two honey-gold lights with short trails | Relay around the left and right sides; 10-second cycle |
| Discussion · growing activity | Teal four-arm pinwheel | Smooth 12-second rotation |
| Discussion · active | Two rose-colored orbiting lights | Follow the outer edge; 12-second cycle |
| Question | Open violet circular arc | One rotation every 4 seconds |
| Correct | Complete emerald-green circle | 3-second breathing cycle |
| Wrong | Complete magenta X | 3.2-second sweeping light |
| Rest | Indigo waveform | Flows across the matrix |
| Rest reminder | Warm-white hourglass | Upper sand decreases, lower sand accumulates in layers, and sand falls through the center |

Attention and Discussion continuously respond to the noise context and do not return to Standby because of voice inactivity. Question lasts 10 seconds by default. Correct and Wrong each last 5 seconds. These temporary responses return to the previous base mode when finished. Rest defaults to 600 seconds followed by a 20-second reminder. Manually switching the base mode cancels the current temporary response.

The lights indicate the whole class's activity rhythm. They do not score or label individual students.

## 6. Voice handling and offline commands

One microphone stream supplies both volume analysis and speech recognition. Silero VAD evaluates unamplified audio separately, so gain applied to quiet commands does not alter noise classification.

**Voice handling** is enabled by default and does not require a prefix or fixed sentence. In Attention, it excludes the full detected speech segment and approximately 350 ms of trailing audio while retaining the last valid environmental-noise state. In Discussion, the start of ordinary conversation is buffered for at most one second; sustained conversation is then classified. Loud conversation bypasses this short buffer.

Non-speech sound must still satisfy the thresholds and hold times before changing state. If VAD is unavailable, the interface reports this and volume measurement continues. Attention excludes all detected speech, not only the teacher. A single microphone cannot separate speech and environmental sound occurring simultaneously or identify the speaker. Disable **Voice handling** if you want to measure all conversation. VAD can make mistakes; complete source separation is not guaranteed.

Select **Enable voice** on Classroom to use local SenseVoiceSmall INT8 and Silero VAD. Wait for the models to finish loading and the interface to report readiness. Speak a short command directly:

| Mode | English command | Chinese command |
|---|---|---|
| Standby | `standby` | `待機` |
| Attention | `notice` | `注意` |
| Discussion | `discussion` | `討論` |
| Rest | `rest` | `休息` |
| Question | `question` | `提問` |
| Correct | `correct` | `答對` |
| Wrong | `wrong` | `答錯` |

Complete trigger words can also appear inside a sentence, such as “one two three question” or “you are wrong.” English matching respects whole-word boundaries: `questionnaire` does not trigger `question`. If a recognized segment contains several commands, the last complete trigger wins. At the same position, a longer alias has priority. Repeated commands have an approximately two-second cooldown. Manual operations have priority.

Under **Voice commands**, separate aliases with commas. Before adding a short word, consider whether it occurs frequently in ordinary teaching. For a consistent recognition error, add one **Corrections** entry per line using `misheard text = trigger word`, such as `questions = question`. Corrections are explicit text replacements rather than fuzzy guesses. Ensure a replacement will not accidentally match another command. Save aliases and corrections with **Save settings** beside the input section.

During continuous speech, the recognizer submits up to 1.2 seconds of context every 250 ms. It can begin once at least 320 ms of audio and speech activity are available; a full window is unnecessary. A separate complete-segment pass uses approximately 320 ms of ending silence and a maximum four-second segment. A command already triggered by a short window is not triggered again by the complete pass.

The recognition queue retains only the newest pending segment. Results from before a manual operation, stopped capture, or disconnection are discarded when stale.

Earlier local fixed-audio tests measured about 59–86 ms for short-window CPU decoding. An English replay through the complete capture and recognition queue triggered approximately **0.47 seconds after the target word's audio ended**. These are sample-specific results, not guaranteed performance for every person or computer. Accent, distance, background sound, CPU load, and segmentation affect speed and accuracy. Prefer short, distinct custom triggers over long sentences. The private speech fixtures are not distributed; public tests explicitly report the skipped replays.

## 7. Five classroom timers

Open **Timers**, edit the durations, and select **Save timers**. Updated durations apply the next time the corresponding mode starts.

| Timer | Default | Purpose |
|---|---:|---|
| Question | 10 s | Temporary question response |
| Correct / Wrong | 5 s | Temporary answer feedback |
| Rest | 600 s | Break countdown |
| Rest reminder | 20 s | Reminder before returning to class |
| Voice idle timeout | 0 s | Zero disables the automatic return to Standby |

The voice idle timeout applies only to eligible voice-controlled states. Attention and Discussion remain active regardless of this timer. Use the keyboard to change numeric values; the mouse wheel cannot change them.

## 8. Custom audio and music-responsive brightness

Under **Connection → Custom audio**, choose, preview, or clear MP3/WAV files independently for all seven modes. Set playback volume and fade duration in **Playback settings**.

The package includes the supplied *Blue Danube* MP3. On first setup it becomes the default Rest cue, loops during Rest, and fades out during the reminder. An original gentle demo MP3 is included as a fallback. Existing local audio and settings are preserved.

Imported files are copied into the user's application data directory, so moving the original does not break playback. Choosing or clearing a file and changing the music-response checkbox save immediately. Save volume and fade settings with **Save audio settings**. Clearing the default track prevents it from being automatically added again at every launch.

The project owner supplied the *Blue Danube* recording and requested its inclusion. Source: [女神来了 on YouTube](https://www.youtube.com/watch?v=32fMNpLsvRY); the description credits “Blue Danube by Sergey Pervov.” Its package filename is `audio/blue-danube.mp3`.

This recording is **not covered by the project's MIT license**. Rights remain with the original right holders. YouTube attribution is not a recording redistribution license, and a verifiable recording redistribution license has not been provided. Confirm recording rights before separate public use or relicensing. The included original `audio/default-rest.mp3` is an MIT-licensed alternative.

Enable **Music-responsive light** separately for each mode. Actual decoded audio amplitude smoothly changes overall brightness while preserving the mode's pattern, color, and animation. Stopping playback, disabling the checkbox, or having no valid audio restores the normal mode frame. Brightness remains below 30%. This is not a spectrum display and does not open another microphone stream.

Voice recognition and noise detection pause during audio playback to avoid classifying the music as speech or environmental noise. Manual controls remain available. Select **Stop audio** before using voice commands. Use **Save audio settings** for playback settings.

This version strengthens the brightness variation: quiet passages visibly dim, louder passages brighten smoothly, with a short attack and slower release. In a comparison using the same fixed *Blue Danube* PCM segment at 50% playback volume, brightness variation spanned approximately 3.56 times the previous version's range. This is an algorithm comparison, not a physical-panel perception study. Track dynamics and playback volume affect the result. Silence does not generate artificial music variation.

## 9. Suggested workflow and troubleshooting

Before class, check WLED and microphone status, select the microphone, and calibrate in a quiet room. Choose Attention or Discussion, enable voice if needed, and use manual controls to switch to Rest, Question, or answer feedback. Test short commands and observe the relative input level during quiet periods and conversation before adjusting thresholds.

If lighting is too sensitive or responds too slowly, first check live dBFS, baseline, microphone distance, and input gain. Then adjust one threshold by about 2 dB as described in section 4. For calibration failures, reduce background sound and check capture. Recalibrate after changing devices or gain.

If a command is missed, confirm that voice is enabled, the microphone works, and the full trigger word was recognized. Inspect **Last heard**. Use manual controls when needed, then revise aliases or a consistent misrecognition.

Explain the lights to students as a shared classroom rhythm rather than personal scoring or surveillance. To assess acceptance, compare similar activities with and without gentle lighting and ask anonymously whether the atmosphere was comfortable, the transitions were understandable, or the lighting felt like a warning or monitoring. Respect negative feedback. Reduced volume does not by itself demonstrate acceptance. No student-acceptance results are currently claimed.

## 10. Settings, privacy, and supported platforms

On Windows, settings, imported audio, and activity records are stored in `%APPDATA%\Responsive Classroom`. Settings use UTF-8 and atomic writes, with a backup before an upgrade migration. Custom timers, audio, triggers, quiet baselines, and panel orientation are preserved. This version adjusts Discussion thresholds only when the entire profile still matches the previous defaults and does not conflict with custom Attention thresholds.

Activity logs can contain state changes, volume readings, and accepted commands. Raw microphone recordings are not saved. To disable activity records, set `session_logging_enabled` to `false` in the settings JSON. Speech runs offline; microphone audio is not uploaded to the cloud.

The v3.1.1 release builds and verifies a Windows x64 ZIP only; the Mac version is no longer maintained. Clean Windows VM acceptance, microphones on other computers, classroom speakers, and physical panels still require hardware testing. If no input level appears, check Windows microphone privacy, system mute, and the selected input, then calibrate after the live level responds.

For panel connection problems, confirm that WLED is online and the network permits communication between the computer and panel. Try a manual IP or discovery on the local subnet. For orientation problems, check that panel's rotation, mirroring, and serpentine settings.

## 11. Development environment and project structure

The environment is named `responsive_classroom` and uses Python 3.12. Direct dependencies are pinned in `source/requirements.txt`; development checks are in `source/requirements-dev.txt`. Portable recipients already have the runtime. Only contributors editing source need Python.

| Location | Purpose |
|---|---|
| `source/classroom_core.py` | Settings, state, timers, and PatternMapper; ClassroomController owns all state changes |
| `source/classroom_service.py` | Web Channel, background jobs, and service coordination |
| `source/classroom_audio.py` | Shared capture, noise levels, VAD, SenseVoice, and command parsing |
| `source/classroom_hardware.py` | WLED devices, JSON state, and real-time pixel output |
| `source/pattern_renderer.py` | Shared pixel frames and smooth transitions |
| `source/classroom_playback.py` | Audio import, playback, fades, and music-responsive brightness |
| `source/resources/web/` | Local Web UI; no externally exposed web control server |
| `source/tests/`, `source/tools/` | Automated tests, environment setup, model verification, and packaging |
| `.devcontainer/`, `.github/workflows/` | Codespaces environment and GitHub validation/releases |

Clone the project and run:

```powershell
git clone https://github.com/tput600/responsive_classroom.git
cd responsive_classroom
py -3.12 source/tools/setup_environment.py --dev --models
.venv/Scripts/python.exe source/classroom_app.py
```

Initial setup requires internet access to download dependencies and models. Model sources, licenses, and SHA-256 values are pinned in the repository. A download or hash failure stops setup without replacing valid models. Model binaries are excluded from Git; complete Releases bundle them for offline use.

On macOS or in a cloud container, use `python3.12` and `.venv/bin/python`. The macOS adaptation and native build/acceptance instructions are in [MACOS.md](MACOS.md). Download the matching Mac architecture; automated packaging checks do not replace Finder, microphone, and physical-panel acceptance.

## 12. Edit locally and in the cloud

Local development and GitHub share the `main` branch. Public history starts from a clean version without the original owner's private test history. Personal settings, imported audio, activity logs, `.codex/`, `.venv/`, and build outputs stay local through `.gitignore`. Do not force-add them with `git add -f`.

A typical workflow is:

```powershell
git pull --ff-only
git switch -c feature/my-change
# Edit source or the manuals.
.venv/Scripts/python.exe -m unittest discover -s source/tests -t source -v
git add source README.md README.en.md
.venv/Scripts/python.exe source/tools/check_public_tree.py
git diff --cached
git commit -m "Describe the change"
git push -u origin feature/my-change
```

Open a Pull Request and merge after Windows checks pass. Locally, return to `main` with `git switch main` and synchronize using `git pull --ff-only`. A sole maintainer can also test, commit, and push directly to `main`. If a fast-forward pull reports divergence, reconcile your branch before merging; do not force-push over someone else's changes.

Select **Code → Codespaces → Create codespace** on GitHub. The container prepares the fixed dependencies and offline models. Edit the UI, core, or algorithms, then commit and push; Windows automation validates the change. Codespaces cannot directly access your computer's microphone or classroom LAN panels. Test physical capture, playback, and panel output locally. Pull cloud changes locally to synchronize them.

## 13. Publish a new version

Use **Build Windows desktop candidate** to build and verify the Windows x64 ZIP, then manually run **Publish Windows desktop release** from `main`. Publication requires the successful build commit to match the exact main tip, then checks the ZIP, checksum, Windows resource manifest, and remote asset hashes. Pushing main never publishes automatically, and existing versions are never overwritten.

Update `VERSION` in `source/classroom_resources.py` and both manuals. After testing, run in the Windows development environment:

```powershell
.venv/Scripts/python.exe source/tools/build_release.py
```

The builder checks public files, verifies models, runs unit tests, and bundles the runtime. It briefly tests the frozen model, Web UI, timers, wheel protection, and MP3 decoding in a path containing Chinese characters and spaces with development PATH entries removed. It creates a complete ZIP, SHA-256 checksum, and resource manifest under `artifacts/`, including both manuals.

These checks do not constitute clean Windows VM or physical-panel acceptance. Detailed reports remain in `build/reports/`; private runtime data is not uploaded.

Alternatively, select **Actions → Build Windows release → Run workflow** on GitHub and enter `v` followed by the version matching `VERSION`. It uses the GitHub environment `responsive_classroom` and does not require storing a personal GitHub token. A Release is created only after the build succeeds. An existing version is not silently overwritten; use a new version number.

Automation checks Python syntax, Bandit security rules, known dependency vulnerabilities with pip-audit, and public-file privacy. Model hashes detect corruption. Private IP/MAC addresses, absolute user paths, credentials, and activity records must not enter public content. Scans do not prove that software is vulnerability-free. Rebuild Qt and native components when upstream security updates require it.

## 14. Code, model, and third-party licenses

Project source and the original demo track use the [MIT license](LICENSE). SenseVoiceSmall model weights use the **FunASR Model Open Source License Agreement v1.1**, not MIT. Silero VAD, sherpa-onnx, Qt/PySide6, FFmpeg, NumPy, PortAudio, SoXR, and fonts have separate terms.

In a source checkout, full license texts, provenance, and instructions for replacing/rebuilding LGPL shared libraries are in the [third-party notices](source/resources/THIRDPARTY_NOTICES.md) and `source/resources/licenses/`. In a portable package, open `_internal/THIRDPARTY_NOTICES.md` and `_internal/licenses/`.

Public packages do not include WLED firmware, personal account tokens, runtime settings, microphone recordings, or private development history. Public audio is limited to the attributed default track and original demo described in section 8. The application is not commercially code-signed; Windows may display a source warning. Download from this repository's Releases and verify the checksum. Recording attribution and recording redistribution permission are different matters; this project does not grant an MIT license for the *Blue Danube* recording.
