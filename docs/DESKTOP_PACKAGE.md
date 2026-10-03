# One Windows + macOS desktop package

The combined archive contains the complete Windows x64 portable application and
one universal2 macOS application. Intel and Apple Silicon Macs use the same app.
The macOS minimum deployment target is **macOS 14.0**. This is a packaging
package, not a publication command or a claim of completed manual acceptance.

## Recipient layout

```text
ResponsiveClassroom-Desktop-v3.1.0.zip
├── README.txt
├── desktop_manifest.json
├── Windows/
│   ├── ResponsiveClassroom.exe
│   ├── _internal/                 # complete runtime, models, media and notices
│   ├── resource_manifest.json
│   └── ...                       # all original Windows package files
└── macOS/
    ├── ResponsiveClassroom.app/   # one universal2 app; retain the complete bundle
    ├── resource_manifest.json
    └── ...                       # all original macOS manuals and notices
```

- Windows: extract the complete archive, then open
  `Windows/ResponsiveClassroom.exe`. Moving only the EXE breaks the portable
  application; keep `_internal` and the other files alongside it.
- macOS: extract with macOS Archive Utility or `ditto` so framework symlinks and
  executable permissions survive, then open `macOS/ResponsiveClassroom.app`.
  Keep the entire app bundle intact. The native runtime is included; recipients
  do not install Python. Nested helper apps inside the main app are normal.
- The macOS package does **not** have an Apple Developer ID signature and is
  **not notarized**. Ad-hoc signing is not Developer ID distribution approval.
  If macOS blocks it, stop and obtain a properly signed/notarized build from the
  maintainer. Do not disable Gatekeeper, the Chromium sandbox or other security
  protections.
- A successful frozen model/UI test is separate from Finder launch, microphone
  and local-network/TCC consent, physical audio-device tests and Gatekeeper
  acceptance. Perform the manual acceptance checklist in [MACOS.md](../MACOS.md)
  on Intel and Apple Silicon using the final extracted archive.

The original platform notices and licenses remain untouched. The supplied Blue
Danube recording's redistribution rights have not been independently verified;
source attribution does not grant recording rights. Combining the packages does
not establish a new license or clear public distribution. See the retained
[third-party notices](../source/resources/THIRDPARTY_NOTICES.md).

## Assemble after native verification

Run the standard-library-only helper on macOS (recommended) or Linux with a POSIX filesystem.
macOS can preserve non-0777 symlink permissions; Linux rejects such inputs
explicitly rather than changing a sealed bundle’s recorded permissions.
Inputs must already have passed their platform build and frozen model/UI checks.
The macOS input must be the universal2 package, not either thin-architecture
ZIP. The assembler does not execute either application or publish anything.

```sh
python source/tools/assemble_desktop_archive.py \
  --windows artifacts/ResponsiveClassroom-Portable-windows-x64-v3.1.0.zip \
  --macos artifacts/ResponsiveClassroom-Portable-macos-universal2-v3.1.0.zip \
  --output artifacts/ResponsiveClassroom-Desktop-v3.1.0.zip \
  --version 3.1.0 \
  --validation-evidence build/reports/native-validation.json \
  --report build/reports/desktop-integrity.json
```

`--validation-evidence` and `--report` are optional. Validation evidence is an
existing nonempty JSON object produced by the build workflow. Its complete
content and SHA-256 are embedded in `desktop_manifest.json`. Do not include
secrets or private machine details. Recording supplied evidence does not run or
independently authenticate those tests. Omit the flag when no evidence is
available; the assembly report labels its own scope as `archive-integrity`.

The version is explicit and must match both input manifests and the macOS
bundle's version metadata. The helper accepts any plain `major.minor.patch`
version; it does not change application versions. Keep inputs from the same
reviewed source revision and record that revision with the native evidence.

## Outputs and guarantees

Successful exit status `0` produces:

1. One combined ZIP at `--output`.
2. An adjacent `--output` plus `.sha256` checksum file, in standard
   `<sha256>  <archive-name>` format.
3. A single JSON status object on stdout, also written to `--report` when set.
   It includes `status`, `version`, `candidate`, `archive`, `archive_bytes`,
   `expanded_bytes`, `archive_sha256`, `manifest_entries`, `symlinks`,
   `validation_scope` and `published` (`false`).

Failure exits `1`; an error is written to stderr. Existing ZIPs or checksum
sidecars are never overwritten. A verification failure emits no candidate ZIP.
If writing an optional report fails after the ZIP is verified, the validated ZIP
and its checksum may already exist; inspect those files before retrying with a
new output name.

The helper:

- Rejects mismatched versions/platforms and changed files by independently
  verifying both original manifests. Windows must include its EXE and
  `_internal` payload; macOS must have exactly one top-level
  `ResponsiveClassroom.app` with macOS 14.0 metadata and an executable entry.
- Extracts into a fresh temporary staging tree without using `extractall`.
  Traversal/absolute paths, Windows drive/alternate-stream/device names, duplicate
  and case/Unicode-colliding paths, encrypted entries, special files/permissions,
  links with nondirectory ancestors, and escaping/broken/cyclic symlinks are
  rejected. App-internal symlinks must remain within the app bundle.
- Preserves macOS file bytes, symlink targets, empty directories and Unix modes.
  It never modifies the sealed app. Windows file bytes are preserved; synthetic
  Windows directory and file modes are normalized for a stable container.
- Caps each input ZIP and the final ZIP at **less than 2 GiB**. Total expanded
  content including generated metadata must remain **less than 8 GiB**. The
  larger extraction allowance accounts for two complete platform runtimes;
  metadata and actual streamed bytes are both checked. There is also a
  200,000-entry cap per archive. Allow enough runner disk for original inputs,
  staging, the final archive and a second extracted verification tree.
- Writes a deterministic, sorted manifest covering every payload entry's type,
  permissions and content or link-target SHA-256. It records both input ZIP
  checksums and preserves both original platform manifests. The manifest cannot
  hash itself; the adjacent archive checksum covers it.
- Uses fixed ZIP timestamps and sorted paths. The same input ZIPs, evidence and
  Python/zlib toolchain produce identical combined ZIP bytes, independent of
  staging path or the runner umask. Native application builds themselves are not
  claimed to be reproducible.
- Re-extracts the final ZIP safely, compares all recorded modes/bytes/links, and
  validates both original manifests again before exposing the new package.

The root manifest's architecture labels come from the verified universal2 build
pipeline, not a new binary audit performed by this wrapper. Retain native Mach-O
architecture, code-signature and frozen model/UI results for both Mac CPU types.
The existing release publisher is a separate tool with separate authorization;
this helper never calls it and never replaces an existing release.

## Offline tests

From the repository root:

```sh
PYTHONPATH=source python -m unittest tests.test_desktop_archive -v
```

These use synthetic executable bytes and do not launch software, use the network,
access the microphone or prove real native compatibility. Native build tests and
manual acceptance remain separate.

## Publication cleanup in v3.1.0

The old Windows-only release workflow, three-archive cross-platform publisher, and one-time v3.0.3 draft recovery workflow have been retired. Their Git history and published releases remain intact. There is one current publication entry point, the manual `publish-unified.yml`; `macos.yml` remains an artifact-only architecture-specific diagnostic. The assembly output is not automatically published.

Validation evidence is hashed with the same canonical UTF-8 JSON encoding as the manifest: sorted keys, two-space indentation, unescaped Unicode and a trailing newline. Input whitespace/key ordering may differ without changing the evidence digest; any changed value changes it.
