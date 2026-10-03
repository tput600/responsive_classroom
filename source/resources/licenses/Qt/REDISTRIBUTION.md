# Qt / PySide6 open-source redistribution and relinking notes

These notes document the open-source license path for the Qt 6.11.2 / PySide6 6.11.2 runtime used by this project. No commercial Qt license is asserted. Qt for Python and Qt are available under LGPLv3/GPLv3 and commercial terms, with some Qt modules available only under GPLv3 in the open-source offering. The exact licensing route and package contents must remain clear in each release. Copied Qt documentation pages retain their original notices and are published under the GNU Free Documentation License 1.3; that complete text is in `../GNU-FDL-1.3.txt`.

The official HTML references are saved next to this file: Qt's 6.11 licensing page, 6.11 third-party code page, 6.11 SBOM guide, QtWebEngine 6.11 licensing page, Qt for Python licensing page, and Qt's LGPL obligations page. Full LGPLv3, GPLv3, and LGPLv2.1 text is saved in `../GNU-LGPL-3.0.txt`, `../GNU-GPL-3.0.txt`, and `../GNU-LGPL-2.1.txt`. The full version-matched FFmpeg 7.1.5 attribution is saved in `FFmpeg-7.1.5-Attribution-Qt-6.8.9.html`; it comes from Qt's official documentation. A separate Qt 6.11.2 documentation snapshot identifies FFmpeg 7.1.3 but does not match the installed DLL build metadata described below.

## Source for the version used

- Qt framework source, version 6.11.2: <https://download.qt.io/archive/qt/6.11/6.11.2/single/qt-everywhere-src-6.11.2.tar.xz>
- PySide6 / Shiboken source, version 6.11.2: <https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.tar.xz>
- Qt upstream source archive index: <https://download.qt.io/archive/qt/6.11/6.11.2/>
- Qt for Python source archive index: <https://download.qt.io/official_releases/QtForPython/pyside6/>

These are official versioned source archives, not a statement that the current DLLs have already been rebuilt from them. Keep the matching archive (or an accessible copy) available to recipients for the period required by the applicable license. Qt's Qt 6.11 SBOM documentation describes build-generated SBOMs and says the installer places them under `<INSTALL_DIR>/6.11.2/<target>/sbom/`; preserve the SBOM for the actual installed build when available.

## Practical LGPLv3 distribution steps

For every public source or portable release containing LGPL libraries:

1. Include the LGPLv3 text, the applicable notices and attribution pages, and clear identification of the Qt/PySide components and versions. Also provide the corresponding source for the LGPL-covered libraries and the scripts/instructions needed to build them, or use another source-delivery method permitted by the license.
2. Keep Qt/PySide shared libraries as separately replaceable DLLs. Do not statically link the LGPL libraries into the application, lock them to an unreplaceable signed image, or impose technical restrictions that prevent recipients from installing a modified compatible library and running it. Preserve ABI compatibility where the application's own interfaces require it.
3. Give recipients a practical, documented way to replace Qt/PySide/other LGPL shared libraries in the portable package and relaunch the application. Keep application code and library files separate, and do not use license terms to prohibit reverse engineering for debugging modifications to LGPL-covered libraries.
4. If a frozen executable links to LGPL libraries, provide the relinkable application object material required by the applicable LGPL section 4 route, or ensure the package's shared-library arrangement satisfies the relevant relinking and user-modification provisions. A separate DLL directory alone should not be represented as a complete legal analysis.
5. Preserve the license text and notices in every redistribution. If you modify an LGPL library, mark the changes and dates, license the modification under the same LGPL terms, and provide its corresponding source.

These steps summarize operational requirements; use the complete license texts for the controlling terms. PySide's Python extension modules are also native shared objects and must be accounted for when evaluating how a recipient can modify and relink the Qt libraries. The project's MIT license remains separate and does not supersede Qt's terms.

## GPL-only modules

Qt's official 6.11 licensing page lists Qt Graphs, Qt Quick 3D, Qt Quick 3D Physics, Qt Quick Timeline, and Qt Virtual Keyboard among GPLv3-only modules (along with other modules). An MIT application bundle must exclude GPL-only modules unless the complete application is separately distributed under compatible GPL terms. Confirm the final package's DLLs, `.pyd`/plugin files, QML imports, and plugin dependency closure; source-spec exclusions alone do not prove that a frozen package is clear.

## WebEngine and multimedia notices

Qt WebEngine combines Qt-specific WebEngine code and Chromium; keep the main Qt WebEngine license page and all linked component attribution pages under `../QtWebEngine/`. The WebEngine page notes GPL entries used only for Linux system-resource access are not linked into or distributed by WebEngine binaries; this Windows package must be checked against the actual binary set. The 126 official Qt 6.11 linked component pages have been downloaded and indexed, but package-specific credits are still a release check.

For Qt Multimedia, the Qt 6.11.2 attribution page identifies FFmpeg 7.1.3, but the local PySide6 runtime FFmpeg DLLs report product version 7.1.5. The matching official Qt 7.1.5 attribution page is also included, from Qt 6.8.9, while the version discrepancy is investigated. Identify the exact 6.11.2 package build and retain its matching attribution and source. Qt states its online-installer FFmpeg binary excludes optional GPL and LGPLv3 components, but verify the shipped backend DLLs actually match that configuration. Source for Qt Multimedia is included in the Qt 6.11.2 source archive above; its bundled FFmpeg source is in `qtmultimedia/src/3rdparty/ffmpeg`.
The installed PySide6_Addons 6.11.2 RECORD SHA-256 values match the DLLs, whose embedded version/license/configuration metadata identifies FFmpeg 7.1.5 built as LGPL 2.1-or-later shared libraries with static linking disabled. It contains none of the `--enable-gpl`, `--enable-version3`, or `--enable-nonfree` configure flags. Use the full FFmpeg 7.1.5 attribution page included in this directory; the separate Qt 6.11.2 docs page that says 7.1.3 is retained only as the historical documentation source for the earlier mismatch.

The DLLs report this upstream FFmpeg configuration (the `/c/FFmpeg...` and `C:/zlib...` values are configure-prefix/include/library paths recorded inside the upstream binaries, not paths on the recipient's machine):

```text
--prefix=/c/FFmpeg-n7.1.5/build/msvc/installed --disable-programs --disable-doc --disable-debug --enable-network --disable-lzma --enable-pic --disable-vulkan --disable-v4l2-m2m --disable-decoder=truemotion1 --disable-avdevice --disable-avfilter --enable-zlib --extra-cflags='-IC:/zlib-1.3.1/build/amd64' --extra-ldflags='-LIBPATH:C:/zlib-1.3.1/build/amd64' --toolchain=msvc --enable-shared --disable-static
```

The three DLLs are from the official PySide6_Addons 6.11.2 package RECORD and have these SHA-256 hashes:

| DLL | SHA-256 |
|---|---|
| `avcodec-61.dll` | `3823070d38dc7dce277ccec165f99bbc02ad96bacc9afde1a00818e9d8350eb6` |
| `avformat-61.dll` | `87ea82ab0620ea817c6bab0a4ff0f91a16080842f9b3208c49238458b99754d1` |
| `avutil-59.dll` | `9d744dc6d44189a690d30b491b53aec1a9822a59aeb7b6f79253d219c0f0862d` |

For source and rebuilding, provide the official [FFmpeg 7.1.5 source archive](https://ffmpeg.org/releases/ffmpeg-7.1.5.tar.xz) (or the upstream [n7.1.5 source tag](https://git.ffmpeg.org/ffmpeg.git/tag/?h=n7.1.5)) and the actual build script/toolchain/dependency inputs used for these DLLs. The recorded prefix flags document the binary configuration but are not a substitute for source and build scripts. To relink a modified LGPL library, rebuild the shared FFmpeg DLLs from this source using a compatible MSVC toolchain and required zlib dependency, install them to the distribution's replaceable library directory, and verify the application loads the replacements. The Qt source archive above contains the integration under `qtmultimedia/src/3rdparty/ffmpeg`; this application's FFmpeg DLLs are shared libraries, not static links. Preserve the matching license and attribution files and the SHA/build provenance with each release.

## Release evidence to retain

For each frozen Windows release, record the Qt/PySide version, source archive or build source, DLL/plugin/QML inventory, applicable SBOM, generated dependency and license scan, and a check that no GPL-only Qt module or optional non-redistributable ASIO component is present. Re-check when dependencies, hooks, or build settings change.
