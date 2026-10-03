# macOS 適配 / macOS adaptation

## 狀態 / Status

v3.0.3 的 Mac 候選包必須通過對應架構的原生建置及 frozen smoke tests 才會發佈。這些自動檢查不能替代 macOS 的 Finder 啟動、CoreAudio 收音、TCC 權限與 Gatekeeper 驗證。Windows EXE 無法直接在 macOS 開啟。

The release pipeline requires native builds and frozen smoke tests on each Mac architecture. These automated checks are not full user acceptance: Finder launch, CoreAudio capture, TCC permissions, and Gatekeeper still need manual validation.

- 本次候選封裝目標為 macOS 14 或更新版本；目前依賴解析可能選到 NumPy 的 macOS 14 wheel，因此不宣稱支援 macOS 13。Candidate bundles target macOS 14+; the selected NumPy wheel may require 14 even though Qt supports 13.
- 分開建置 Apple Silicon (`arm64`) 與 Intel (`x86_64`)，不要把兩者合稱 universal2。Build each architecture on its native runner using a matching Python 3.12 interpreter.
- 不共用 Windows、Intel 或 Rosetta 建立的 `.venv`。Use a fresh checkout and native virtual environment for each architecture.
- 所有模型及原生套件在首次建置時下載；完成的 `.app` 才能離線執行。Initial setup downloads dependencies and verified models; the completed bundle is offline-capable.

## 在 Mac 建置 / Build on a Mac

在已安裝原生 Python 3.12 的 Mac，從 repository 根目錄執行：

```sh
python3.12 -c 'import platform; print(platform.machine())'
python3.12 source/tools/setup_environment.py --dev --models
.venv/bin/python -m unittest discover -s source/tests -t source -v
.venv/bin/python source/tools/build_release.py
```

`artifacts/` contains the architecture-specific ZIP and SHA-256 checksum; `build/reports/` contains build and smoke-test evidence. Keep the complete `.app` intact when moving it. Do not move its executable or edit files inside it. Manuals and release manifests are placed beside the bundle to avoid invalidating the code signature.

建置必須在 macOS 完成，不能從 Linux 交叉打包。Build on macOS, not via Linux cross-compilation. The manual **Build macOS candidates** workflow prepares artifacts without publication. **Build and release desktop packages** publishes only after all three platform builds pass. Consult the exact release commit and linked Actions run for evidence.

## 麥克風與區域網路 / Microphone and local network

- `.app` 首次啟動會透過 Qt 主執行緒要求麥克風權限，授權前不會啟動 PortAudio 收音。拒絕後可繼續手動控制，畫面會顯示設定路徑。The bundled app waits for microphone consent before starting capture; denial leaves manual controls available.
- 如曾拒絕，在「系統設定 → 隱私權與安全性 → 麥克風」允許 Responsive Classroom，再重新啟動。After granting permission in System Settings → Privacy & Security → Microphone, restart the app.
- 新版 macOS 也可能要求「區域網路」權限以搜尋與控制 WLED。請只在要連接燈板時允許；拒絕時用系統設定管理。Recent macOS versions may request Local Network access for WLED discovery/control. Permission remains the user's choice.
- Qt 的權限 API 需要真正的 `.app`。直接執行 `.venv/bin/python source/classroom_app.py` 的來源模式由 Python／Terminal 的權限環境決定，不能作為 `.app` 權限驗收。Qt's permission API requires an application bundle; source mode depends on the launching Python/Terminal permissions and is only a development convenience.
- 設定、匯入音效及活動紀錄存於 `~/Library/Application Support/Responsive Classroom/`，不寫入 `.app`。Settings, imported audio and activity logs remain in the user's Application Support directory.

## 發佈限制 / Distribution limits

PyInstaller's default ad-hoc signing is not an Apple Developer ID identity, notarization, or a guarantee of Gatekeeper acceptance. This change does not include credentials or a notarization pipeline. Before broad distribution, a maintainer must arrange the appropriate Developer ID signing/notarization workflow, including the entitlements required by Qt WebEngine and audio capture, then verify the final downloaded artifact. Do not disable Gatekeeper, Chromium's sandbox, or other system protections to make a build pass.

## 必須完成的原生驗收 / Native acceptance checklist

Perform on both supported architectures using the extracted artifact, not only source mode:

1. Open the `.app` using Finder from a path containing spaces and Chinese characters. Verify no missing Qt framework/plugin/model errors and no console window.
2. First launch: allow microphone; confirm level meter, quiet calibration, Chinese/English commands. Repeat with permission denied, then grant in System Settings and restart. Close while the permission prompt is open; ensure no background capture starts afterward.
3. Grant/deny Local Network permission and check WLED scan, manual IP connection, output, and clean shutdown. A Terminal launch can be exempt from local network privacy checks and does not replace Finder testing.
4. Test built-in, USB, and Bluetooth input changes, unplug/replug, sleep/wake, and input rate changes. Confirm no stale capture threads or unexpected restart loops.
5. Exercise three UI pages, language switching, resize/Retina, dialogs, timers, repeated clicks, MP3/WAV import/playback, and settings persistence after restart. Check the app's normal shutdown restores panel state.
6. Verify `codesign --verify --deep --strict --verbose=2 ResponsiveClassroom.app` on the extracted artifact. For a distributable signed/notarized build, assess Gatekeeper and stapling on a clean Mac with the downloaded/quarantined artifact. Ad-hoc builds are not expected to satisfy Developer ID distribution acceptance.
7. Confirm the packaged build runs without an installed Python or development PATH; retain architecture, macOS version, commit, checksum, and the resulting reports.

## Primary references

- [Qt for Python permission API limitations](https://doc.qt.io/qtforpython-6/considerations.html#permission-api)
- [Qt application permissions](https://doc.qt.io/qt-6/permissions.html)
- [Apple local network privacy, TN3179](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy)
- [PyInstaller macOS bundles and signing](https://pyinstaller.org/en/stable/feature-notes.html#macos-multi-arch-support)
