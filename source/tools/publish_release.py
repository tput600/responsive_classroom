"""Guard cross-platform releases and publish only complete, verified artifact sets."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
AUTOMATIC_TAG = 'v3.0.3'
TARGETS = ('macos-arm64', 'macos-x86_64', 'windows-x64')


def validate_tag(tag):
    if not re.fullmatch(r'v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)', tag):
        raise ValueError('Release tag must be a stable semantic version such as v3.0.3')
    return tag


def source_tag(root=ROOT):
    tree = ast.parse((root / 'source/classroom_resources.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets):
            return validate_tag('v' + ast.literal_eval(node.value))
    raise ValueError('classroom_resources.VERSION was not found')


def release_notes(tag, root=ROOT):
    validate_tag(tag)
    notes = root / 'docs/releases' / f'{tag}.md'
    if not notes.is_file() or not notes.read_text(encoding='utf-8').strip():
        raise ValueError(f'Nonempty release notes are required: {notes}')
    return notes


def github(*arguments, timeout=120):
    executable = shutil.which('gh')
    if not executable:
        raise RuntimeError('GitHub CLI is required')
    # No shell or persistent credentials. The workflow supplies its scoped GH_TOKEN.
    result = subprocess.run([executable, *arguments], capture_output=True,
                            text=True, check=False, timeout=timeout)  # nosec B603
    if result.returncode:
        raise RuntimeError(f'GitHub CLI failed ({result.returncode}): {result.stderr.strip()}')
    return result.stdout


def current_repository():
    repository = os.environ.get('GH_REPO', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('GH_REPO must identify the current owner/repository')
    return repository


def list_releases():
    """Authenticated listing includes drafts, which tag lookup does not resolve."""
    pages = json.loads(github('api', '--paginate', '--slurp',
                              f'repos/{current_repository()}/releases?per_page=100'))
    if (not isinstance(pages, list) or any(not isinstance(page, list) for page in pages)
            or any(not isinstance(item, dict) for page in pages for item in page)):
        raise RuntimeError('Unexpected GitHub releases response')
    return [item for page in pages for item in page]


def find_release_id(tag):
    matches = [item for item in list_releases() if item.get('tag_name') == tag]
    if len(matches) != 1 or type(matches[0].get('id')) is not int or matches[0]['id'] <= 0:
        raise ValueError('Expected exactly one remote release with a valid ID for this tag')
    return matches[0]['id']


def release_exists(tag):
    """Use successful list queries, so authentication/API errors never mean absent."""
    validate_tag(tag)
    repository = current_repository()
    refs = json.loads(github('api', f'repos/{repository}/git/matching-refs/tags/{tag}'))
    releases = list_releases()
    if not isinstance(refs, list):
        raise RuntimeError('Unexpected GitHub tag response')
    return (any(ref['ref'] == f'refs/tags/{tag}' for ref in refs)
            or any(item.get('tag_name') == tag for item in releases))


def prepare(tag, event, *, root=ROOT):
    validate_tag(tag)
    version_tag = source_tag(root)
    if event == 'push' and (tag != AUTOMATIC_TAG or version_tag != AUTOMATIC_TAG):
        print('Automatic publication is only enabled for v3.0.3; no release requested.')
        return False
    if version_tag != tag:
        raise ValueError(f'Requested {tag} does not match source version {version_tag}')
    release_notes(tag, root)
    if release_exists(tag):
        if event == 'push':
            print(f'{tag} already has a tag or release; leaving it unchanged.')
            return False
        raise ValueError(f'{tag} already has a tag or release; refusing to replace it')
    return True


def verify_artifacts(directory, tag):
    """Require exactly one correctly named ZIP/checksum pair per native platform."""
    validate_tag(tag)
    expected = [f'ResponsiveClassroom-Portable-{target}-{tag}.zip' for target in TARGETS]
    expected_names = set(expected + [f'{name}.sha256' for name in expected])
    paths = list(directory.iterdir())
    if {path.name for path in paths} != expected_names or any(
            path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError('Expected exactly three native ZIPs and their three SHA-256 files')
    verified = []
    for name in expected:
        archive = directory / name
        checksum = directory / f'{name}.sha256'
        text = checksum.read_text(encoding='ascii')
        match = re.fullmatch(r'([0-9a-f]{64})  ' + re.escape(name) + r'\n?', text)
        if not match:
            raise ValueError(f'Malformed checksum file: {checksum.name}')
        with archive.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != match.group(1) or not zipfile.is_zipfile(archive):
            raise ValueError(f'Checksum mismatch or invalid ZIP archive: {name}')
        verified.extend((archive, checksum))
    return verified


def verify_remote_tag(tag, commit, *, required):
    """Never replace a tag another actor created while this run was building."""
    repository = current_repository()
    refs = json.loads(github('api', f'repos/{repository}/git/matching-refs/tags/{tag}'))
    matches = [ref for ref in refs if ref['ref'] == f'refs/tags/{tag}']
    if not matches:
        if required:
            raise ValueError('Published release has no matching Git tag')
        return
    if len(matches) != 1:
        raise ValueError('Unexpected duplicate matching Git tags')
    target = matches[0]['object']
    for _ in range(10):
        if target['type'] == 'commit':
            if target['sha'] != commit:
                raise ValueError('Release tag points to a different commit; refusing to publish')
            return
        if target['type'] != 'tag' or not re.fullmatch(r'[0-9a-fA-F]{40}', target['sha']):
            break
        target = json.loads(github('api', f"repos/{repository}/git/tags/{target['sha']}"))['object']
    raise ValueError('Unable to resolve release tag to the verified commit')


def verify_uploaded_release(tag, commit, assets, *, draft, release_id):
    repository = current_repository()
    details = json.loads(github('api', f'repos/{repository}/releases/{release_id}'))
    if (details.get('id') != release_id or details.get('tag_name') != tag
            or details.get('target_commitish') != commit
            or details.get('draft') is not draft or details.get('prerelease') is not False):
        raise ValueError('Remote release version, commit, or publication state does not match')
    uploaded = details.get('assets', [])
    if (len(uploaded) != len(assets)
            or {asset['name'] for asset in uploaded} != {path.name for path in assets}):
        raise ValueError('Remote release must contain exactly the six verified assets')
    by_name = {asset['name']: asset for asset in uploaded}
    for path in assets:
        asset = by_name[path.name]
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if (asset.get('state') != 'uploaded' or asset.get('size') != path.stat().st_size
                or asset.get('digest') != f'sha256:{digest}'):
            raise ValueError(f'Remote asset upload or digest verification failed: {path.name}')
    verify_remote_tag(tag, commit, required=not draft)
    return details


def promote_release(tag, commit, assets, release_id):
    verify_uploaded_release(tag, commit, assets, draft=True, release_id=release_id)
    github('api', '--method', 'PATCH',
           f'repos/{current_repository()}/releases/{release_id}',
           '-F', 'draft=false', '-f', 'make_latest=true',
           '-f', f'tag_name={tag}', '-f', f'target_commitish={commit}')
    published = verify_uploaded_release(tag, commit, assets, draft=False,
                                        release_id=release_id)
    latest = json.loads(github('api', f'repos/{current_repository()}/releases/latest'))
    if latest.get('id') != published.get('id') or latest.get('tag_name') != tag:
        raise ValueError('Published release was not confirmed as latest')
    print(published['html_url'])


def publish(tag, commit, artifacts, *, root=ROOT):
    if not re.fullmatch(r'[0-9a-fA-F]{40}', commit):
        raise ValueError('Publication requires the exact 40-character workflow commit SHA')
    if source_tag(root) != validate_tag(tag):
        raise ValueError('Requested release tag does not match the checked-out source')
    notes = release_notes(tag, root)
    assets = verify_artifacts(artifacts, tag)
    # Recheck after the builds, immediately before the first mutating command.
    if release_exists(tag):
        raise ValueError(f'{tag} already has a tag or release; refusing to replace it')
    # A failed/partial upload remains a draft. Never promote before verifying all
    # six remote assets; reruns refuse existing drafts instead of overwriting them.
    github('release', 'create', tag, *(str(path) for path in assets),
           '--draft', '--target', commit,
           '--title', f'Responsive Classroom {tag}', '--notes-file', str(notes), timeout=900)
    promote_release(tag, commit, assets, find_release_id(tag))


def resume(tag, commit, artifacts, *, release_id, root=ROOT):
    """Promote only an explicitly selected, fully verified existing draft."""
    if type(release_id) is not int or release_id <= 0:
        raise ValueError('Resume requires an explicit positive release ID')
    if not re.fullmatch(r'[0-9a-fA-F]{40}', commit):
        raise ValueError('Publication requires the exact 40-character workflow commit SHA')
    if source_tag(root) != validate_tag(tag):
        raise ValueError('Requested release tag does not match the checked-out source')
    assets = verify_artifacts(artifacts, tag)
    if find_release_id(tag) != release_id:
        raise ValueError('Explicit release ID does not match the existing release for this tag')
    # No create/upload/delete commands: recovery must preserve the original assets.
    promote_release(tag, commit, assets, release_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    preflight = commands.add_parser('prepare')
    preflight.add_argument('--tag', required=True)
    preflight.add_argument('--event', choices=('push', 'workflow_dispatch'), required=True)
    preflight.add_argument('--output', required=True, type=Path)
    for command in ('publish', 'resume'):
        publication = commands.add_parser(command)
        publication.add_argument('--tag', required=True)
        publication.add_argument('--commit', required=True)
        publication.add_argument('--artifacts', required=True, type=Path)
        if command == 'resume':
            publication.add_argument('--release-id', required=True, type=int)
    args = parser.parse_args()
    if args.command == 'prepare':
        eligible = prepare(args.tag, args.event)
        with args.output.open('a', encoding='utf-8') as output:
            output.write(f'eligible={str(eligible).lower()}\ntag={args.tag}\n')
    elif args.command == 'publish':
        publish(args.tag, args.commit, args.artifacts)
    else:
        resume(args.tag, args.commit, args.artifacts, release_id=args.release_id)


if __name__ == '__main__':
    main()
