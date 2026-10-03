"""Publish only the final v3.1.0 archive from an exact, successful build of approved main.

No build output is executed, renamed, patched, or recompressed. GitHub's artifact
transport SHA-256, the inner ZIP checksum, payload manifests, native evidence,
and both uploaded release assets are checked independently. Failures leave a
draft for explicit review; recovery only promotes a fully verified draft by ID.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

if __package__:
    from . import assemble_desktop_archive as desktop
    from .publish_release import current_repository, release_notes, source_tag
else:
    import assemble_desktop_archive as desktop
    from publish_release import current_repository, release_notes, source_tag

ROOT = Path(__file__).resolve().parents[2]
TAG = 'v3.1.0'
VERSION = '3.1.0'
WORKFLOW = '.github/workflows/unified-desktop.yml'
ARTIFACT = f'ResponsiveClassroom-AllPlatforms-{TAG}'
ARCHIVE = ARTIFACT + '.zip'
REPORT = 'unified-integrity.json'
API_VERSION = '2022-11-28'
BUILD_BRANCHES = frozenset(('main', 'codex/unified-desktop-package'))
BOOTSTRAP_BRANCH = 'codex/publish-v3.1.0'
PUBLICATION_WORKFLOW = '.github/workflows/publish-unified.yml'
JOBS = frozenset((
    'Build genuine universal2 macOS app',
    'Build complete Windows x64 package',
    'Test identical app natively on arm64',
    'Test identical app natively on x86_64',
    'Assemble and verify one all-platform ZIP',
))


def github(*arguments, output=None, timeout=120):
    executable = shutil.which('gh')
    if not executable:
        raise RuntimeError('GitHub CLI is required')
    # No shell, credential storage, token logging, or downloaded code execution.
    result = subprocess.run([executable, *arguments], stdout=output or subprocess.PIPE,
                            stderr=subprocess.PIPE, text=output is None,
                            check=False, timeout=timeout)  # nosec B603
    if result.returncode:
        error = result.stderr if isinstance(result.stderr, str) else result.stderr.decode('utf-8', errors='replace')
        raise RuntimeError(f'GitHub CLI failed ({result.returncode}): {error.strip()}')
    return result.stdout


def api(path, *arguments, pages=False):
    options = ('--paginate', '--slurp') if pages else ()
    return json.loads(github('api', path, '-H', f'X-GitHub-Api-Version: {API_VERSION}',
                             *options, *arguments))


def positive_id(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(f'{label} must be a positive integer')
    return value


def verify_bootstrap(commit, run_id):
    """One controlled push can invoke the exact already-approved main source.

    The bootstrap commit may only change this publication workflow and must be
    a direct child of approved main. It cannot introduce new source or switch
    the selected tested run. The normal main workflow has no push trigger.
    """
    bootstrap = os.environ.get('GITHUB_SHA', '')
    if (os.environ.get('GITHUB_REF') != f'refs/heads/{BOOTSTRAP_BRANCH}'
            or not re.fullmatch(r'[0-9a-f]{40}', bootstrap) or bootstrap == commit
            or os.environ.get('APPROVED_RELEASE_COMMIT') != commit
            or os.environ.get('APPROVED_CANDIDATE_RUN_ID') != str(run_id)):
        raise ValueError('Controlled bootstrap requires the exact branch, approved commit and candidate run pins')
    repo = current_repository()
    details = api(f'repos/{repo}/commits/{bootstrap}')
    files = details.get('files', [])
    parents = details.get('parents', [])
    if (details.get('sha') != bootstrap or not isinstance(parents, list) or len(parents) != 1
            or parents[0].get('sha') != commit or not isinstance(files, list) or len(files) != 1
            or files[0].get('filename') != PUBLICATION_WORKFLOW or files[0].get('status') != 'modified'):
        raise ValueError('Bootstrap must directly follow approved main and change only the publication workflow')
    branch = api(f'repos/{repo}/git/ref/heads/{BOOTSTRAP_BRANCH}')
    if branch.get('object', {}).get('type') != 'commit' or branch['object'].get('sha') != bootstrap:
        raise ValueError('Controlled bootstrap branch no longer points to this exact workflow commit')


def require_context(commit, run_id, *, root=ROOT):
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Requires an exact lowercase 40-character commit SHA')
    event = os.environ.get('GITHUB_EVENT_NAME')
    manual = (event == 'workflow_dispatch' and os.environ.get('GITHUB_REF') == 'refs/heads/main'
              and os.environ.get('GITHUB_SHA') == commit)
    bootstrap = event == 'push' and os.environ.get('GITHUB_REF') == f'refs/heads/{BOOTSTRAP_BRANCH}'
    if (not (manual or bootstrap)
            or os.environ.get('GITHUB_REPOSITORY') != current_repository()):
        raise ValueError('Publication must be manually dispatched from main or use the controlled bootstrap')
    executable = shutil.which('git')
    if not executable:
        raise RuntimeError('Git is required')
    actual = subprocess.run([executable, 'rev-parse', 'HEAD'], cwd=root,
                            capture_output=True, text=True, check=True).stdout.strip()  # nosec B603
    if actual != commit:
        raise ValueError('Checked-out source must match the approved commit')
    if source_tag(root) != TAG:
        raise ValueError('Checked-out source version must be 3.1.0')
    release_notes(TAG, root)
    main = api(f'repos/{current_repository()}/git/ref/heads/main')
    if main.get('object', {}).get('type') != 'commit' or main['object'].get('sha') != commit:
        raise ValueError('The approved commit must still be the current main tip')
    if bootstrap:
        verify_bootstrap(commit, run_id)


def collect_pages(path, key):
    pages = api(path, pages=True)
    if (not isinstance(pages, list) or not pages
            or any(not isinstance(page, dict) or not isinstance(page.get(key), list) for page in pages)
            or any(not isinstance(item, dict) for page in pages for item in page[key])):
        raise ValueError(f'Unexpected paginated GitHub {key} response')
    return [item for page in pages for item in page[key]]


def verify_run(run_id, commit, *, root=ROOT):
    positive_id(run_id, 'Candidate run ID')
    require_context(commit, run_id, root=root)
    repo = current_repository()
    workflow = api(f'repos/{repo}/actions/workflows/unified-desktop.yml')
    run = api(f'repos/{repo}/actions/runs/{run_id}')
    repository = run.get('repository', {})
    if (run.get('id') != run_id or run.get('head_sha') != commit
            or run.get('head_branch') not in BUILD_BRANCHES or run.get('status') != 'completed'
            or run.get('conclusion') != 'success' or run.get('event') not in ('push', 'workflow_dispatch')
            or workflow.get('path') != WORKFLOW or type(workflow.get('id')) is not int
            or run.get('workflow_id') != workflow['id']
            or run.get('path', '').split('@', 1)[0] != WORKFLOW
            or repository.get('full_name') != repo or type(repository.get('id')) is not int
            or run.get('head_repository', {}).get('id') != repository['id']):
        raise ValueError('Candidate must be a successful same-repository approved-branch unified build of the approved commit')
    jobs = collect_pages(f'repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100', 'jobs')
    required = [job for job in jobs if job.get('name') in JOBS]
    if (len(required) != len(JOBS) or {job.get('name') for job in required} != JOBS
            or any(job.get('run_id') != run_id or job.get('head_sha') != commit
                   or job.get('status') != 'completed' or job.get('conclusion') != 'success'
                   for job in required)):
        raise ValueError('Same-run Mac build, both native architectures, Windows and assembly must all pass')
    artifacts = collect_pages(f'repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100', 'artifacts')
    matches = [artifact for artifact in artifacts if artifact.get('name') == ARTIFACT]
    if len(matches) != 1:
        raise ValueError('Expected exactly one final all-platform artifact from the selected run')
    artifact = matches[0]
    positive_id(artifact.get('id'), 'Artifact ID')
    origin = artifact.get('workflow_run', {})
    if (artifact.get('expired') is not False
            or not isinstance(artifact.get('digest'), str)
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', artifact['digest'])
            or type(artifact.get('size_in_bytes')) is not int
            or not 0 < artifact['size_in_bytes'] < desktop.MAX_BYTES + 16 * 1024**2
            or origin.get('id') != run_id or origin.get('head_sha') != commit
            or origin.get('head_branch') != run['head_branch']
            or origin.get('repository_id') != repository['id']
            or origin.get('head_repository_id') != repository['id']):
        raise ValueError('Artifact must have a nonexpired immutable ID, digest, and matching run provenance')
    return artifact


def download_artifact(artifact, destination):
    """Use the verified numeric ID, check transport digest, and copy only data."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Artifact destination must be a new directory')
    destination.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='unified-download-') as temp:
        transport = Path(temp) / 'artifact.zip'
        with transport.open('xb') as stream:
            github('api', f'repos/{current_repository()}/actions/artifacts/{artifact["id"]}/zip',
                   '-H', f'X-GitHub-Api-Version: {API_VERSION}', output=stream, timeout=900)
        if (transport.stat().st_size != artifact['size_in_bytes']
                or 'sha256:' + desktop.sha256(transport) != artifact['digest']):
            raise ValueError('GitHub artifact transport size or digest mismatch')
        expected = {f'artifacts/{ARCHIVE}': ARCHIVE,
                    f'artifacts/{ARCHIVE}.sha256': ARCHIVE + '.sha256',
                    f'build/reports/{REPORT}': REPORT}
        # upload-artifact preserves paths relative to the common ancestor.
        # Also accept its equivalent flat layout; never accept mixed/extra paths.
        layouts = (expected, {name: name for name in expected.values()})
        with zipfile.ZipFile(transport) as zipped:
            infos = zipped.infolist()
            names = [info.filename for info in infos]
            layout = next((item for item in layouts if set(names) == set(item)), None)
            if layout is None or len(names) != 3:
                raise ValueError('Artifact transport must contain only the ZIP, checksum and integrity report')
            total = 0
            for info in infos:
                if (info.orig_filename != info.filename or info.is_dir() or info.flag_bits & 1
                        or stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG)
                        or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise ValueError('Unsafe artifact transport entry')
                limit = desktop.MAX_BYTES if info.filename.endswith('.zip') else 1024**2
                if not 0 < info.file_size < limit:
                    raise ValueError('Artifact transport entry exceeds its size limit')
                count = 0
                with zipped.open(info) as source, (destination / layout[info.filename]).open('xb') as output:
                    while block := source.read(1024**2):
                        count += len(block)
                        total += len(block)
                        if count > info.file_size or total >= desktop.MAX_BYTES + 2 * 1024**2:
                            raise ValueError('Artifact transport exceeds its declared size')
                        output.write(block)
                if count != info.file_size:
                    raise ValueError('Artifact transport entry size mismatch')


def verify_evidence(manifest, run_id, commit):
    evidence = manifest.get('validation_evidence', {}).get('report', {})
    expected_url = f'https://github.com/{current_repository()}/actions/runs/{run_id}'
    proof = manifest.get('validation_evidence', {})
    serialized = desktop.json_bytes(evidence)
    if proof.get('sha256') != hashlib.sha256(serialized).hexdigest():
        raise ValueError('Native evidence report hash mismatch')
    native = evidence.get('macos_native', [])
    inputs = manifest.get('inputs', {})
    for platform in ('macos', 'windows'):
        item = inputs.get(platform, {})
        if (not re.fullmatch(r'[0-9a-f]{64}', str(item.get('sha256', '')))
                or type(item.get('bytes')) is not int or item['bytes'] <= 0):
            raise ValueError('Both platform input hashes and sizes are required')
    if (evidence.get('commit') != commit or evidence.get('run') != expected_url
            or evidence.get('windows_job') != 'passed' or not isinstance(native, list)
            or len(native) != 2 or any(not isinstance(item, dict) for item in native)
            or {item.get('machine') for item in native} != {'arm64', 'x86_64'}
            or any(item.get('archive_sha256') != inputs['macos']['sha256']
                   or item.get('version') != VERSION or item.get('frozen_model') != 'passed'
                   or item.get('frozen_ui') != 'passed' or item.get('signature') != 'ad-hoc-verified'
                   or type(item.get('universal_binaries')) is not int or item['universal_binaries'] <= 0
                   for item in native)):
        raise ValueError('Native evidence must bind both architectures to the same Mac ZIP and approved run/commit')


def verify_artifacts(directory, run_id, commit):
    directory = Path(directory)
    paths = list(directory.iterdir())
    if ({path.name for path in paths} != {ARCHIVE, ARCHIVE + '.sha256', REPORT}
            or any(path.is_symlink() or not path.is_file() for path in paths)):
        raise ValueError('Expected exactly the final ZIP, its checksum and the integrity report')
    archive, checksum = directory / ARCHIVE, directory / (ARCHIVE + '.sha256')
    digest = desktop.sha256(archive)
    if not re.fullmatch(digest + r'  ' + re.escape(ARCHIVE) + r'\n?', checksum.read_text(encoding='ascii')):
        raise ValueError('Final ZIP checksum mismatch')
    report = json.loads((directory / REPORT).read_text(encoding='utf-8'))
    if (report.get('status') != 'ok' or report.get('version') != VERSION
            or report.get('candidate') is not False or report.get('published') is not False
            or report.get('archive') != ARCHIVE or report.get('archive_sha256') != digest
            or report.get('archive_bytes') != archive.stat().st_size
            or report.get('validation_scope') != 'archive-integrity'):
        raise ValueError('Final assembly integrity report does not match the exact ZIP')
    with tempfile.TemporaryDirectory(prefix='unified-verify-') as temp:
        root = Path(temp) / 'payload'
        desktop.extract_checked(archive, root, windows=False, max_bytes=desktop.MAX_EXPANDED_BYTES)
        manifest_path = root / desktop.MANIFEST
        if (manifest_path.is_symlink() or not manifest_path.is_file()
                or manifest_path.stat().st_size > 64 * 1024**2
                or {path.name for path in root.iterdir()} != {'Windows', 'macOS', 'README.txt', desktop.MANIFEST}):
            raise ValueError('Unexpected release root or invalid desktop manifest')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if (manifest.get('schema_version') != 1 or manifest.get('version') != VERSION
                or manifest.get('kind') != 'combined-desktop-release'
                or manifest.get('platforms') != ['windows-x64', 'macos-universal2']
                or manifest.get('files') != desktop.desktop_records(root)
                or report.get('manifest_entries') != len(manifest.get('files', []))):
            raise ValueError('Final desktop payload must match its release manifest')
        desktop.verify_windows(root / 'Windows', VERSION)
        desktop.verify_macos(root / 'macOS', VERSION)
        verify_evidence(manifest, run_id, commit)
    return [archive, checksum]


def release_list():
    pages = api(f'repos/{current_repository()}/releases?per_page=100', pages=True)
    if (not isinstance(pages, list) or any(not isinstance(page, list) for page in pages)
            or any(not isinstance(item, dict) for page in pages for item in page)):
        raise ValueError('Unexpected releases response')
    return [item for page in pages for item in page]


def matching_refs():
    refs = api(f'repos/{current_repository()}/git/matching-refs/tags/{TAG}')
    if not isinstance(refs, list) or any(not isinstance(ref, dict) for ref in refs):
        raise ValueError('Unexpected tag response')
    return [ref for ref in refs if ref.get('ref') == f'refs/tags/{TAG}']


def require_absent():
    if matching_refs() or any(item.get('tag_name') == TAG for item in release_list()):
        raise ValueError('v3.1.0 already has a tag or release; refusing to replace it')


def find_release_id():
    matches = [item for item in release_list() if item.get('tag_name') == TAG]
    if len(matches) != 1:
        raise ValueError('Expected exactly one v3.1.0 release, including drafts')
    return positive_id(matches[0].get('id'), 'Release ID')


def verify_remote_tag(commit, *, required):
    refs = matching_refs()
    if not refs and not required:
        return
    if len(refs) != 1:
        raise ValueError('Expected exactly one matching release tag')
    target = refs[0].get('object', {})
    for _ in range(10):
        if target.get('type') == 'commit' and target.get('sha') == commit:
            return
        if target.get('type') != 'tag' or not re.fullmatch(r'[0-9a-f]{40}', str(target.get('sha', ''))):
            break
        target = api(f'repos/{current_repository()}/git/tags/{target["sha"]}').get('object', {})
    raise ValueError('Release tag does not resolve to the verified commit')


def verify_uploaded(release_id, commit, assets, *, draft):
    positive_id(release_id, 'Release ID')
    details = api(f'repos/{current_repository()}/releases/{release_id}')
    if (details.get('id') != release_id or details.get('tag_name') != TAG
            or details.get('target_commitish') != commit or details.get('draft') is not draft
            or details.get('prerelease') is not False):
        raise ValueError('Remote release ID, version, commit or publication state mismatch')
    uploaded = details.get('assets', [])
    if (not isinstance(uploaded, list) or len(uploaded) != 2
            or any(not isinstance(item, dict) for item in uploaded)
            or {item.get('name') for item in uploaded} != {path.name for path in assets}):
        raise ValueError('Remote release must contain only the verified ZIP and checksum')
    for path in assets:
        remote = next(item for item in uploaded if item['name'] == path.name)
        if (remote.get('state') != 'uploaded' or remote.get('size') != path.stat().st_size
                or remote.get('digest') != 'sha256:' + desktop.sha256(path)):
            raise ValueError(f'Remote release asset digest/size mismatch: {path.name}')
    verify_remote_tag(commit, required=not draft)
    return details


def promote(release_id, run_id, commit, artifact, assets, *, root=ROOT):
    verify_uploaded(release_id, commit, assets, draft=True)
    # Recheck main and the selected run immediately before making anything public.
    if verify_run(run_id, commit, root=root) != artifact:
        raise ValueError('Candidate artifact metadata changed during publication')
    api(f'repos/{current_repository()}/releases/{release_id}', '--method', 'PATCH',
        '-F', 'draft=false', '-F', 'prerelease=false', '-f', 'make_latest=true',
        '-f', f'tag_name={TAG}', '-f', f'target_commitish={commit}')
    details = verify_uploaded(release_id, commit, assets, draft=False)
    latest = api(f'repos/{current_repository()}/releases/latest')
    if latest.get('id') != release_id or latest.get('tag_name') != TAG:
        raise ValueError('Published v3.1.0 was not confirmed as latest')
    print(details['html_url'])


def execute(command, run_id, commit, directory, *, release_id=None, root=ROOT):
    if command not in ('verify', 'publish', 'resume'):
        raise ValueError('Unexpected publication command')
    if command == 'resume':
        positive_id(release_id, 'Release ID')
    artifact = verify_run(run_id, commit, root=root)
    if command == 'publish':
        require_absent()
    if command == 'resume' and find_release_id() != release_id:
        raise ValueError('Explicit draft ID does not match the existing v3.1.0 release')
    download_artifact(artifact, directory)
    assets = verify_artifacts(directory, run_id, commit)
    if command == 'verify':
        print(f'Verified {ARCHIVE} from run {run_id}; no release was changed.')
        return
    if verify_run(run_id, commit, root=root) != artifact:
        raise ValueError('Candidate artifact metadata changed during verification')
    if command == 'publish':
        require_absent()
        github('release', 'create', TAG, *(str(path) for path in assets), '--draft',
               '--target', commit, '--title', f'Responsive Classroom {TAG}',
               '--notes-file', str(release_notes(TAG, root)), timeout=900)
        # Drafts are discovered through the authenticated list, then always read
        # and patched by ID. Tag-only release lookup can miss unpublished drafts.
        release_id = find_release_id()
    promote(release_id, run_id, commit, artifact, assets, root=root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'verify', 'publish', 'resume'):
        command = commands.add_parser(name)
        command.add_argument('--candidate-run-id', type=int, required=True)
        command.add_argument('--commit', required=True)
        if name == 'prepare':
            command.add_argument('--output', type=Path, required=True)
        else:
            command.add_argument('--artifacts', type=Path, required=True,
                                 help='New directory for exact-run download, never preexisting files')
        if name == 'resume':
            command.add_argument('--release-id', type=int, required=True)
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        artifact = verify_run(args.candidate_run_id, args.commit)
        with args.output.open('a', encoding='utf-8') as output:
            output.write(f'artifact_id={artifact["id"]}\nartifact_digest={artifact["digest"]}\n')
    else:
        execute(args.command, args.candidate_run_id, args.commit, args.artifacts,
                release_id=getattr(args, 'release_id', None))


if __name__ == '__main__':
    main()
