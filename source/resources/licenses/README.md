# License files and source provenance

This directory contains complete upstream license texts or unmodified upstream attribution documents for dependencies currently identified in the local runtime. It does not grant a single license over the components: each file's upstream license remains applicable. HTML snapshots were fetched as page source without executing page scripts.

| File or directory | Component / version | Authoritative source |
|---|---|---|
| `GNU-LGPL-3.0.txt` | GNU LGPL v3 | <https://www.gnu.org/licenses/lgpl-3.0.txt> |
| `GNU-GPL-3.0.txt` | GNU GPL v3 | <https://www.gnu.org/licenses/gpl-3.0.txt> |
| `GNU-LGPL-2.1.txt` | GNU LGPL v2.1 | <https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt> |
| `GNU-FDL-1.3.txt` | Qt documentation pages copied below are published under GFDL 1.3 | <https://www.gnu.org/licenses/fdl-1.3.txt> |
| `CFFI-MIT-0.txt` | CFFI 2.1.1 `_cffi_backend` extension, MIT No Attribution | Installed distribution `cffi-2.1.1.dist-info/licenses/LICENSE`; upstream project <https://foss.heptapod.net/pypy/cffi> |
| `Silero-VAD-MIT.txt` | Silero VAD | <https://github.com/snakers4/silero-vad/blob/master/LICENSE> |
| `ONNXRuntime-1.28.2-MIT.txt` | ONNX Runtime 1.28.2 | <https://github.com/microsoft/onnxruntime/blob/v1.28.2/LICENSE> |
| `ONNXRuntime-1.28.2-ThirdPartyNotices.txt` | ONNX Runtime 1.28.2 bundled third parties | <https://github.com/microsoft/onnxruntime/blob/v1.28.2/ThirdPartyNotices.txt> |
| `PortAudio-MIT.txt` | PortAudio | <https://github.com/PortAudio/portaudio/blob/master/LICENSE.txt> |
| `PyInstaller-6.22.3-COPYING.txt` | PyInstaller 6.22.3, GPLv2-or-later with Bootloader Exception | Installed distribution `pyinstaller-6.22.3.dist-info/licenses/COPYING.txt`; upstream <https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt> |
| `sherpa-onnx-LICENSE.txt` | sherpa-onnx 1.13.8 and sherpa-onnx-core 1.13.8, Apache-2.0 | The core distribution declares Apache-2.0 but has no separate license file; this is the full license from the companion sherpa-onnx distribution. Upstream <https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE> |
| Python runtime license | CPython 3.12, PSF License Version 2 and historical notices | Copied from the exact interpreter distribution into the portable package. Its patch version and official source URL are recorded in `licenses/Python-runtime/python-runtime.json`. |
| `sounddevice/sounddevice-LICENSE.txt` | sounddevice 0.5.6, MIT | Installed distribution license; upstream <https://github.com/spatialaudio/python-sounddevice> |
| `soxr/` | Python-SoXR 1.1.0, libsoxr and PFFFT notices | Installed distribution notices from `soxr-1.1.0.dist-info/licenses/` |
| `Qt/` | Qt 6.11 / Qt Multimedia 6.11.2 / PySide6 6.11.2 licensing and source notes | Individual original URLs are listed in `Qt/README.md`. The FFmpeg component-version discrepancy is described there. |
| `QtWebEngine/` | Qt WebEngine / Chromium 6.11 published third-party attributions | 126 individual official Qt 6.11 page URLs are listed in `QtWebEngine/README.md`. |

The model-specific terms and attribution are in `../models/sensevoice/LICENSE` and `../models/sensevoice/README.md`; the font's full OFL text is in `../assets/fonts/OFL.txt`. Qt's saved documentation snapshots retain their original notices and are covered by their stated GFDL terms; the full GFDL text is included above. NumPy's complete license/notices directory is part of the runtime package and must be carried into any portable output.

Before release, reconcile this directory with the final package inventory. In particular, retain all applicable package-provided NumPy notices, exclude non-redistributable PortAudio ASIO binaries unless separately authorized, and verify the final Qt DLL/plugin/QML set does not contain GPL-only modules.
