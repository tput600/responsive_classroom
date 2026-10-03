from importlib import metadata
from pathlib import Path
import os
import platform
import re
import sys
import sysconfig

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
resources = root / "resources"
public_build = os.environ.get("RESPONSIVE_CLASSROOM_PUBLIC_BUILD") == "1"
datas = [(str(resources / "models" / "sensevoice"), "models/sensevoice"),
         (str(resources / "assets"), "assets"),
         (str(resources / "web"), "web"),
         (str(resources / "licenses"), "licenses/project"),
         (str(root.parent / "LICENSE"), "."),
         (str(resources / "THIRDPARTY_NOTICES.md"), ".")]
if public_build:
    datas.extend([(str(resources / "audio" / "default-rest.mp3"), "audio"),
                  (str(resources / "audio" / "blue-danube.mp3"), "audio")])
else:
    datas.extend([(str(resources / "fixtures"), "fixtures"),
                  (str(resources / "audio"), "audio")])
binaries = []
# CPython's runtime is redistributed too, separately from this project's MIT.
python_license_candidates = (
    Path(sys.base_prefix) / "LICENSE.txt",
    Path(sys.base_prefix) / "LICENSE",
    # CPython's Unix install target places the license beside the stdlib.
    Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
    Path(sys.base_prefix) / "Resources/English.lproj/License.rtf",
)
python_license = next((path for path in python_license_candidates if path.is_file()), None)
if python_license is None:
    raise RuntimeError("The build interpreter's full license text is required")
datas.append((str(python_license), "licenses/Python-runtime"))
runtime_notice = root.parent / "build" / "python-runtime.json"
runtime_notice.parent.mkdir(parents=True, exist_ok=True)
import json
runtime_notice.write_text(json.dumps({"component": "CPython", "version": platform.python_version(),
    "source": f"https://github.com/python/cpython/tree/v{platform.python_version()}",
    "license_file": python_license.name}, indent=2), encoding="utf-8")
datas.append((str(runtime_notice), "licenses/Python-runtime"))
# PySide6 6.11 links Darwin microphone permission support into QtCore.abi3.so.
# There is no separate permissions plugin directory in the pinned macOS wheels.
# https://github.com/pyside/pyside-setup/blob/6.11/sources/pyside6/PySide6/QtCore/CMakeLists.txt
hiddenimports = ["PySide6.QtCore", "PySide6.QtNetwork", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
                 "PySide6.QtWebChannel", "PySide6.QtMultimedia", "sherpa_onnx", "sounddevice", "soxr"]

# Keep sherpa's native recognizer/VAD runtime and PortAudio available offline.
for package in ("sherpa_onnx", "sounddevice", "soxr"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hidden)

optional_distributions = {"PyYAML", "charset-normalizer", "typing_extensions"}
for distribution_name in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6",
                          "numpy", "sounddevice", "soxr", "sherpa-onnx", "sherpa-onnx-core", "cffi",
                          *sorted(optional_distributions)):
    try:
        distribution = metadata.distribution(distribution_name)
    except metadata.PackageNotFoundError:
        if distribution_name in optional_distributions:
            continue
        raise
    package_name = distribution.metadata["Name"].replace("/", "_")
    for entry in distribution.files or ():
        relative = str(entry).replace("\\", "/")
        marker = ".dist-info/licenses/"
        if marker in relative:
            source = Path(distribution.locate_file(entry))
            if source.is_file():
                destination = f"licenses/{package_name}/{relative.split(marker, 1)[1]}"
                datas.append((str(source), destination.rpartition("/")[0]))

analysis = Analysis(
    [str(root / "classroom_app.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "PySide6.QtMultimediaWidgets", "faster_whisper", "whisper",
              "ctranslate2", "av", "huggingface_hub", "tokenizers",
              "onnxruntime", "PySide6.QtCharts", "PySide6.QtDataVisualization",
              "PySide6.QtGraphs", "PySide6.QtQuick3D", "PySide6.QtQuickTimeline",
              "PySide6.QtVirtualKeyboard"],
    noarchive=False,
    optimize=1,
)
# This application uses Widgets + WebEngine, never QML or GPL-only add-ons.
# Removing unused QML plug-ins also avoids collecting their native dependencies.
gpl_addons = re.compile(r"charts|datavisualization|graphs|quick3d|quicktimeline|virtualkeyboard", re.I)
def needed(entry):
    # Analysis also creates top-level aliases to bundled native libraries.
    # Removing a framework but retaining its SYMLINK alias breaks codesign.
    paths = [entry[0]] + ([entry[1]] if entry[2] == "SYMLINK" else [])
    for name in paths:
        path = name.replace("\\", "/")
        if (path.startswith(("PySide6/qml/", "PySide6/Qt/qml/"))
                or ("PySide6/" in path and gpl_addons.search(path))
                or "-asio." in path.lower()):
            return False
    return True

analysis.datas = [entry for entry in analysis.datas if needed(entry)]
analysis.binaries = [entry for entry in analysis.binaries if needed(entry)]
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True,
         name="ResponsiveClassroom", console=False, upx=False,
         target_arch=platform.machine() if sys.platform == "darwin" else None)
collect = COLLECT(exe, analysis.binaries, analysis.datas,
                  strip=False, upx=False, name="ResponsiveClassroom")

if sys.platform == "darwin":
    version = re.search(r'VERSION = "([^"]+)"',
                        (root / "classroom_resources.py").read_text(encoding="utf-8")).group(1)
    app = BUNDLE(collect, name="ResponsiveClassroom.app",
                 version=version,
                 bundle_identifier="org.responsiveclassroom.desktop",
                 info_plist={"CFBundleDisplayName": "Responsive Classroom",
                             "NSHighResolutionCapable": True,
                             "CFBundleShortVersionString": version,
                             "CFBundleVersion": version,
                             "LSMinimumSystemVersion": "14.0",
                             "NSLocalNetworkUsageDescription":
                             "Responsive Classroom connects to your WLED display on the local network to show classroom signals.",
                             "NSMicrophoneUsageDescription":
                             "Responsive Classroom uses the microphone for classroom noise levels and voice commands. Audio is not recorded or saved."})
