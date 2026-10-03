# macOS 適配 / macOS adaptation

## 狀態 / Status

v3.1.0 的整合包同時包含 `Windows/ResponsiveClassroom.exe` 與 `macOS/ResponsiveClassroom.app`。Mac 是單一 universal2 App，包含 Intel x86_64 與 Apple Silicon arm64 執行碼，目標為 macOS 14 或更新版本。請使用 Mac「封存工具程式」完整解壓，不要搬出 App 內的執行檔。

The release gate runs the exact same universal2 Mac archive natively on Intel and Apple Silicon, checks every Mach-O binary for both slices, verifies ad-hoc signatures before and after extraction, and exercises the offline model and WebEngine UI. These checks do not replace Finder, physical audio devices, TCC permissions, or Gatekeeper acceptance.

## 在 Mac 建置 / Build on a Mac

使用原生 macOS 14+ 與 Xcode/Command Line Tools SDK 14+，從 repository 根目錄執行。腳本會依固定版本與 SHA-256 從官方來源編譯 CPython 3.12.15、OpenSSL 與 liblzma，再建立通用依賴環境；初次建置需要網際網路。

```sh
bash source/tools/build_universal_python.sh "$HOME/responsive-universal-python"
"$HOME/responsive-universal-python/bin/python3.12" source/tools/setup_environment.py --models
.venv/bin/python source/tools/prepare_universal_macos.py
RESPONSIVE_CLASSROOM_MAC_ARCH=universal2 .venv/bin/python source/tools/build_release.py
```

Python安裝目錄必須是新的空目錄；不要沿用其他架構的 `.venv`。開發用 Ruff/Bandit/pip-audit 放在另一個環境，不混入成品。`artifacts/` 包含 universal2 ZIP 與 SHA-256，`build/reports/` 包含檢查證據。模型與函式庫在打包完成後可離線使用。

The **Build unified desktop candidate** workflow builds Windows and universal2 macOS, runs the identical Mac archive on both native runner architectures, then assembles one all-platform ZIP. Publication is separate and requires the successful exact-commit evidence. Optional architecture-specific builds remain available through **Build macOS candidates** for diagnosis.

Manuals and manifests live outside the signed app. Do not edit files inside the `.app` after signing.

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
