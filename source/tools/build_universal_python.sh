#!/bin/bash
# Build a genuine arm64+x86_64 CPython runtime on a native macOS runner.
# Usage: bash source/tools/build_universal_python.sh /absolute/empty/prefix
# No Homebrew packages, binary Python installers, sudo, or Rosetta installation.
set -euo pipefail

usage() {
    printf 'Usage: %s /absolute/empty/prefix\n' "$0"
    printf 'Requires native macOS 14+, Xcode/CLT SDK 14+, and Internet access.\n'
}
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then usage; exit 0; fi
[[ $# == 1 ]] || { usage >&2; exit 2; }
[[ "$(uname -s)" == Darwin ]] || die 'Run this source build on macOS, not Linux.'
[[ "$1" =~ ^/[A-Za-z0-9_./-]+$ && "$1" != / ]] ||
    die 'The build prefix must be absolute and contain only letters, numbers, /, _, ., or -.'
[[ "$(/usr/sbin/sysctl -in sysctl.proc_translated 2>/dev/null || true)" != 1 ]] ||
    die 'Run in the native macOS architecture, not under Rosetta.'

readonly PYTHON_VERSION=3.12.15
readonly PYTHON_SHA256=c2c4321961fab0fb999d66e0cecf521c2ab3994c7992873ea99e306c1094fd5a
readonly OPENSSL_VERSION=3.5.9
readonly OPENSSL_SHA256=603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a
readonly XZ_VERSION=5.8.4
readonly XZ_SHA256=0014c7886930454fe8bd4228665b51af55eeae560ea135c9c4cd33f55b2591d9
readonly PYTHON_URL="https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz"
readonly OPENSSL_URL="https://github.com/openssl/openssl/releases/download/openssl-${OPENSSL_VERSION}/openssl-${OPENSSL_VERSION}.tar.gz"
readonly XZ_URL="https://github.com/tukaani-project/xz/releases/download/v${XZ_VERSION}/xz-${XZ_VERSION}.tar.gz"
# Pin hashes from the official release pages/assets, never a moving download.
# https://www.python.org/downloads/release/python-31215/
# https://openssl-library.org/source/
# https://github.com/tukaani-project/xz/releases/tag/v5.8.4

jobs="${UNIVERSAL_PYTHON_JOBS:-$(/usr/sbin/sysctl -n hw.logicalcpu)}"
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || die 'UNIVERSAL_PYTHON_JOBS must be a positive integer.'

# In particular, setup-python, Homebrew, pkg-config, virtualenvs, and a runner's
# inherited compiler/cache flags must not choose a thin dependency silently.
for variable in $(compgen -e); do
    case "$variable" in
        PYTHON_VERSION|PYTHON_SHA256|PYTHON_URL|OPENSSL_VERSION|OPENSSL_SHA256|OPENSSL_URL)
            ;; # Already replaced with the exact readonly source pins above.
        PYTHON*|DYLD_*|LD_*|LIBRARY_PATH|CPATH|C_INCLUDE_PATH|CPLUS_INCLUDE_PATH|OBJC_INCLUDE_PATH|\
        CC|CXX|CPP|CFLAGS|CXXFLAGS|CPPFLAGS|LDFLAGS|LIBS|ARCHFLAGS|SDKROOT|\
        MACOSX_DEPLOYMENT_TARGET|CONFIG_SITE|CONFIG_SHELL|MAKEFLAGS|MFLAGS|\
        PKG_CONFIG*|OPENSSL*|LIBCRYPTO*|LIBLZMA*|LIBSQLITE3*|LIBFFI*|BZIP2*|ZLIB*|TCLTK*|\
        ac_cv_*|py_cv_*|VIRTUAL_ENV|CONDA_PREFIX|CONDA_DEFAULT_ENV)
            unset "$variable" ;;
    esac
done
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export LC_ALL=C
export SDKROOT="$(xcrun --sdk macosx --show-sdk-path)"
sdk_version="$(xcrun --sdk macosx --show-sdk-version)"
host_version="$(sw_vers -productVersion)"
[[ "${sdk_version%%.*}" -ge 14 && "${host_version%%.*}" -ge 14 ]] ||
    die "Requires macOS and SDK 14+ (host=$host_version, SDK=$sdk_version)."
export MACOSX_DEPLOYMENT_TARGET=14.0
export CC="$(xcrun --sdk macosx --find clang)"
export CXX="$(xcrun --sdk macosx --find clang++)"
export AR="$(xcrun --sdk macosx --find ar)"
export RANLIB="$(xcrun --sdk macosx --find ranlib)"
export PKG_CONFIG=/usr/bin/false
export PKG_CONFIG_PATH= PKG_CONFIG_LIBDIR=
export CONFIG_SITE=/dev/null
[[ -s /etc/ssl/cert.pem ]] || die 'The macOS system CA bundle /etc/ssl/cert.pem is missing.'
export SSL_CERT_FILE=/etc/ssl/cert.pem

mkdir -p "$1"
prefix="$(cd "$1" && pwd -P)"
[[ -z "$(ls -A "$prefix")" ]] || die "Build prefix must be empty: $prefix"
work="$(mktemp -d /tmp/responsive-python-universal.XXXXXXXX)"
cleanup() {
    status=$?
    if [[ "$status" -eq 0 ]]; then
        rm -rf "$work"
    else
        printf 'Build failed; source/configuration logs retained at %s\n' "$work" >&2
        printf 'The partial prefix must not be reused as a completed runtime: %s\n' "$prefix" >&2
    fi
}
trap cleanup EXIT
trap 'printf "Failure at line %s: %s\n" "$LINENO" "$BASH_COMMAND" >&2' ERR
deps="$prefix/deps"
licenses="$prefix/share/licenses"
mkdir -p "$work/downloads" "$work/src" "$deps/lib" "$deps/include" "$licenses"

download() {
    local url="$1" hash="$2" destination="$3" actual
    curl --fail --location --retry 4 --retry-delay 2 --connect-timeout 30 \
        --proto '=https' --tlsv1.2 "$url" --output "$destination"
    actual="$(shasum -a 256 "$destination" | awk '{print $1}')"
    [[ "$actual" == "$hash" ]] || die "SHA-256 mismatch for $url: $actual"
}
download "$PYTHON_URL" "$PYTHON_SHA256" "$work/downloads/python.tar.xz"
download "$OPENSSL_URL" "$OPENSSL_SHA256" "$work/downloads/openssl.tar.gz"
download "$XZ_URL" "$XZ_SHA256" "$work/downloads/xz.tar.gz"
tar -xJf "$work/downloads/python.tar.xz" -C "$work/src"
tar -xzf "$work/downloads/openssl.tar.gz" -C "$work/src"
tar -xzf "$work/downloads/xz.tar.gz" -C "$work/src"
python_source="$work/src/Python-$PYTHON_VERSION"
openssl_source="$work/src/openssl-$OPENSSL_VERSION"
xz_source="$work/src/xz-$XZ_VERSION"

# Build each OpenSSL slice separately; its generated configuration headers are
# architecture-specific even though its public C API is identical.
for architecture in arm64 x86_64; do
    stage="$work/stage/$architecture"
    build="$work/openssl-$architecture"
    mkdir -p "$build" "$stage"
    (
        cd "$build"
        export CFLAGS="-O2 -fPIC -arch $architecture -isysroot $SDKROOT -mmacosx-version-min=14.0"
        export LDFLAGS="-arch $architecture -isysroot $SDKROOT -mmacosx-version-min=14.0"
        /usr/bin/perl "$openssl_source/Configure" "darwin64-$architecture-cc" \
            no-shared no-module no-tests no-apps no-docs no-zlib \
            "--prefix=$stage" --libdir=lib --openssldir=/etc/ssl
        make -j "$jobs" build_libs
        # install_dev never writes configuration or certificates into /etc/ssl.
        make install_dev
    )

    build="$work/xz-$architecture"
    mkdir -p "$build"
    if [[ "$architecture" == arm64 ]]; then host=aarch64-apple-darwin; else host=x86_64-apple-darwin; fi
    (
        cd "$build"
        export CFLAGS="-O2 -fPIC -arch $architecture -isysroot $SDKROOT -mmacosx-version-min=14.0"
        export LDFLAGS="-arch $architecture -isysroot $SDKROOT -mmacosx-version-min=14.0"
        "$xz_source/configure" "--prefix=$stage" \
            "--build=$($xz_source/build-aux/config.guess)" "--host=$host" \
            --disable-shared --enable-static --enable-pic --disable-dependency-tracking \
            --disable-nls --disable-xz --disable-xzdec --disable-lzmadec \
            --disable-lzmainfo --disable-lzma-links --disable-scripts --disable-doc
        make -j "$jobs"
        make install
    )
done

for library in libssl.a libcrypto.a liblzma.a; do
    lipo -create "$work/stage/arm64/lib/$library" "$work/stage/x86_64/lib/$library" \
        -output "$deps/lib/$library"
    "$RANLIB" "$deps/lib/$library"
    lipo -verify_arch arm64 x86_64 "$deps/lib/$library"
done

# Preserve both versions of every differing header, instead of accidentally
# using an x86 configuration header when compiling the arm64 slice.
diff -u <(cd "$work/stage/arm64/include" && find . -type f | sort) \
        <(cd "$work/stage/x86_64/include" && find . -type f | sort)
cp -R "$work/stage/arm64/include/." "$deps/include/"
while IFS= read -r -d '' header; do
    relative="${header#"$work/stage/arm64/include/"}"
    if ! cmp -s "$header" "$work/stage/x86_64/include/$relative"; then
        for architecture in arm64 x86_64; do
            mkdir -p "$deps/include/$architecture/$(dirname "$relative")"
            cp "$work/stage/$architecture/include/$relative" "$deps/include/$architecture/$relative"
        done
        cat > "$deps/include/$relative" <<EOF
/* Generated dispatch header for the pinned universal2 dependency build. */
#if defined(__arm64__) || defined(__aarch64__)
# include <arm64/$relative>
#elif defined(__x86_64__)
# include <x86_64/$relative>
#else
# error "This dependency build supports only arm64 and x86_64"
#endif
EOF
    fi
done < <(find "$work/stage/arm64/include" -type f -print0)

# Choose only the SDK's system libraries. OpenSSL and liblzma are our own fat
# static archives; no third-party dylib path can leak into the frozen app.
for library in sqlite3 bz2 z ffi; do
    [[ -f "$SDKROOT/usr/lib/lib$library.tbd" ]] || die "Missing SDK library: lib$library.tbd"
done
export CFLAGS='-O2 -fPIC -mmacosx-version-min=14.0'
export CPPFLAGS="-I$deps/include -isystem $SDKROOT/usr/include"
export LDFLAGS="-L$deps/lib -L$SDKROOT/usr/lib -mmacosx-version-min=14.0"
export LIBLZMA_CFLAGS="-I$deps/include"
export LIBLZMA_LIBS="$deps/lib/liblzma.a"
export LIBSQLITE3_CFLAGS="-I$SDKROOT/usr/include"
export LIBSQLITE3_LIBS="$SDKROOT/usr/lib/libsqlite3.tbd"
export BZIP2_CFLAGS="-I$SDKROOT/usr/include"
export BZIP2_LIBS="$SDKROOT/usr/lib/libbz2.tbd"
export ZLIB_CFLAGS="-I$SDKROOT/usr/include"
export ZLIB_LIBS="$SDKROOT/usr/lib/libz.tbd"
build="$work/python-build"
mkdir -p "$build/Modules"
cat > "$build/Modules/Setup.local" <<'EOF'
*disabled*
_tkinter
readline
_gdbm
_dbm
_curses
_curses_panel
nis
EOF
(
    cd "$build"
    "$python_source/configure" "--prefix=$prefix" \
        "--enable-universalsdk=$SDKROOT" --with-universal-archs=universal2 \
        --enable-shared --without-static-libpython --with-ensurepip=install \
        --with-pkg-config=no "--with-openssl=$deps" --with-openssl-rpath=no
    make -j "$jobs"
    # The upstream install otherwise adds an intentionally thin -intel64 helper.
    make -j "$jobs" install LIPO_INTEL64_FLAGS=
)

cp "$python_source/LICENSE" "$prefix/LICENSE.txt"
cp "$python_source/LICENSE" "$licenses/Python-$PYTHON_VERSION-LICENSE.txt"
cp "$openssl_source/LICENSE.txt" "$licenses/OpenSSL-$OPENSSL_VERSION-LICENSE.txt"
if [[ -f "$openssl_source/NOTICE" ]]; then
    cp "$openssl_source/NOTICE" "$licenses/OpenSSL-$OPENSSL_VERSION-NOTICE.txt"
fi
cp "$xz_source/COPYING" "$licenses/XZ-$XZ_VERSION-COPYING.txt"
cp "$xz_source/COPYING.0BSD" "$licenses/XZ-$XZ_VERSION-0BSD.txt"
cp "$python_source/Modules/expat/COPYING" "$licenses/CPython-bundled-Expat-COPYING.txt"
# These CPython-vendored libraries carry their licenses in source headers,
# rather than standalone LICENSE files. Retain the exact upstream notices.
sed -n '1,/^[[:space:]]*\*\//p' "$python_source/Modules/_decimal/libmpdec/mpdecimal.h" \
    > "$licenses/CPython-bundled-libmpdec-LICENSE.txt"
"$prefix/bin/python3.12" -I - "$python_source/Modules/_hacl" "$licenses/CPython-bundled-HACL-LICENSE.txt" <<'PY'
import pathlib
import re
import sys
root = pathlib.Path(sys.argv[1])
notices = {}
for path in sorted(root.rglob('*')):
    if path.suffix in {'.h', '.c'}:
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(r'/\*.*?\*/', text, flags=re.S):
            notice = match.group()
            if 'Copyright' in notice and ('License' in notice or 'Permission' in notice):
                notices.setdefault(notice, []).append(path.relative_to(root).as_posix())
assert notices, 'Missing bundled HACL license notices'
pathlib.Path(sys.argv[2]).write_text('\n\n'.join(
    'Source files: ' + ', '.join(paths) + '\n' + notice
    for notice, paths in notices.items()) + '\n', encoding='utf-8')
PY
cat > "$licenses/Universal-Python-BUILD-NOTICES.txt" <<EOF
Responsive Classroom universal2 Python runtime

CPython $PYTHON_VERSION: $PYTHON_URL
SHA-256: $PYTHON_SHA256
OpenSSL $OPENSSL_VERSION (LTS): $OPENSSL_URL
SHA-256: $OPENSSL_SHA256
XZ/liblzma $XZ_VERSION: $XZ_URL
SHA-256: $XZ_SHA256

Built from the exact upstream release archives for arm64 and x86_64.
OpenSSL and liblzma are statically linked into CPython extension modules.
Differing dependency headers use an architecture-specific dispatch wrapper.
Only liblzma is used from XZ; no GPL-licensed XZ scripts are redistributed.
OpenSSL uses the macOS system CA bundle at /etc/ssl/cert.pem.
SQLite, bzip2, zlib, libffi, and operating-system frameworks are supplied by
macOS and linked using the Xcode SDK. They are not copied into this prefix.
Expat, libmpdec, and HACL are bundled with the pinned CPython source release.
Tcl/Tk, readline, GDBM/DBM, curses, and NIS modules are deliberately disabled;
they are unnecessary for the Qt-based application and are not Homebrew inputs.
Build SDK: $sdk_version
Deployment target: macOS $MACOSX_DEPLOYMENT_TARGET
Compiler: $CC

Source and checksum references:
https://www.python.org/downloads/release/python-31215/
https://openssl-library.org/source/
https://github.com/openssl/openssl/releases/download/openssl-$OPENSSL_VERSION/openssl-$OPENSSL_VERSION.tar.gz.sha256
https://github.com/tukaani-project/xz/releases/tag/v$XZ_VERSION
EOF

# The installed interpreter must work without DYLD_LIBRARY_PATH, an activated
# venv, or the build tree. Audit both slices of every installed runtime Mach-O.
"$prefix/bin/python3.12" -I - "$prefix" "$sdk_version" "$PYTHON_VERSION" "$OPENSSL_VERSION" <<'PY'
import bz2
import ctypes
import hashlib
import json
import lzma
import pathlib
import platform
import sqlite3
import ssl
import subprocess
import sys
import sysconfig
import zlib

prefix = pathlib.Path(sys.argv[1])
assert platform.system() == 'Darwin'
assert platform.python_version() == sys.argv[3], sys.version
assert ssl.OPENSSL_VERSION.startswith('OpenSSL ' + sys.argv[4] + ' '), ssl.OPENSSL_VERSION
assert sysconfig.get_config_var('Py_ENABLE_SHARED') == 1
assert sysconfig.get_platform().endswith('-universal2'), sysconfig.get_platform()
assert (prefix / 'lib/libpython3.12.dylib').is_file()
payload = bytes(range(256)) * 100
for module in (bz2, lzma, zlib):
    assert module.decompress(module.compress(payload)) == payload, module.__name__
with sqlite3.connect(':memory:') as database:
    assert database.execute('select 6 * 7').fetchone() == (42,)
assert hashlib.sha256(b'abc').hexdigest() == 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)(lambda value: value + 1)
assert callback(41) == 42
context = ssl.create_default_context()
assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
assert context.cert_store_stats()['x509_ca'] > 0, 'System TLS trust roots not loaded'

magics = {bytes.fromhex(value) for value in (
    'feedface', 'cefaedfe', 'feedfacf', 'cffaedfe',
    'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca')}
records = []
for path in sorted(prefix.rglob('*')):
    if path.is_symlink() or not path.is_file() or path.suffix == '.a':
        continue  # Universal dependency archives were verified before linking.
    with path.open('rb') as stream:
        if stream.read(4) not in magics:
            continue
    architectures = subprocess.check_output(['/usr/bin/lipo', '-archs', str(path)], text=True).split()
    assert set(architectures) == {'arm64', 'x86_64'}, (str(path), architectures)
    linked = {}
    for architecture in architectures:
        output = subprocess.check_output(
            ['/usr/bin/otool', '-arch', architecture, '-L', str(path)], text=True)
        dependencies = [line.strip().split(' (')[0] for line in output.splitlines() if line.startswith('\t')]
        for dependency in dependencies:
            assert (dependency.startswith(('/usr/lib/', '/System/Library/')) or
                    dependency == str(prefix / 'lib/libpython3.12.dylib')), (
                        'Unexpected non-system dependency', str(path), architecture, dependency)
        linked[architecture] = dependencies
    records.append({'path': path.relative_to(prefix).as_posix(),
                    'architectures': sorted(architectures), 'dependencies': linked})
assert len(records) >= 20, 'Missing interpreter/standard-library binary modules'
report = {'python': platform.python_version(), 'openssl': ssl.OPENSSL_VERSION,
          'sqlite': sqlite3.sqlite_version, 'zlib': zlib.ZLIB_RUNTIME_VERSION,
          'native_machine': platform.machine(), 'sdk': sys.argv[2],
          'deployment_target': '14.0', 'ssl_trust': context.cert_store_stats(),
          'binaries': records}
(prefix / 'share/licenses/Universal-Python-build-report.json').write_text(
    json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(f'PASS: {len(records)} universal2 runtime binaries; native smoke on {platform.machine()}')
print(ssl.OPENSSL_VERSION, sys.version)
PY

"$prefix/bin/python3.12" -I -m pip --version
printf 'Universal2 CPython %s ready: %s/bin/python3.12\n' "$PYTHON_VERSION" "$prefix"
