"""Offline Windows publication checks: fake gh only, no tokens or network writes."""
import copy
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tools import assemble_desktop_archive as desktop
from tools import publish_unified_release as release


@mock.patch.dict(os.environ, {'GH_REPO': 'example/classroom', 'GITHUB_REPOSITORY': 'example/classroom',
                              'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                              'GITHUB_SHA': 'a' * 40})
class WindowsPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='windows release ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'source').mkdir()
        (self.root / 'source/classroom_resources.py').write_text('VERSION = "3.1.1"\n')
        (self.root / 'docs/releases').mkdir(parents=True)
        (self.root / 'docs/releases/v3.1.1.md').write_text('# Release 3.1.1\n')
        self.commit = 'a' * 40
        self.run_id = 123
        self.artifact = {
            'id': 456, 'name': release.ARTIFACT, 'expired': False,
            'size_in_bytes': 900, 'digest': 'sha256:' + 'b' * 64,
            'workflow_run': {'id': 123, 'head_sha': self.commit, 'head_branch': 'main',
                             'repository_id': 78, 'head_repository_id': 78},
        }
        self.run = {
            'id': 123, 'head_sha': self.commit, 'head_branch': 'main',
            'status': 'completed', 'conclusion': 'success', 'event': 'workflow_dispatch',
            'workflow_id': 44, 'path': release.WORKFLOW,
            'repository': {'full_name': 'example/classroom', 'id': 78},
            'head_repository': {'id': 78},
        }
        self.jobs = [{'name': name, 'run_id': 123, 'head_sha': self.commit,
                      'status': 'completed', 'conclusion': 'success'} for name in release.JOBS]
        self.api_calls = []
        self.api = mock.patch.object(release, 'api', side_effect=self.fake_api).start()
        self.addCleanup(mock.patch.stopall)
        self.git = mock.patch.object(release.subprocess, 'run', return_value=
                                     subprocess.CompletedProcess([], 0, self.commit + '\n', '')).start()
        mock.patch.object(release.shutil, 'which', return_value='/usr/bin/git').start()

    def fake_api(self, path, *arguments, **kwargs):
        self.api_calls.append((path, arguments, kwargs))
        prefix = 'repos/example/classroom/'
        self.assertTrue(path.startswith(prefix))
        route = path[len(prefix):]
        if route == 'git/ref/heads/main':
            return {'object': {'type': 'commit', 'sha': self.commit}}
        if route == 'actions/workflows/unified-desktop.yml':
            return {'id': 44, 'path': release.WORKFLOW}
        if route == 'actions/runs/123':
            return self.run
        if route == 'actions/runs/123/jobs?filter=latest&per_page=100':
            return [{'jobs': self.jobs}]
        if route == 'actions/runs/123/artifacts?per_page=100':
            return [{'artifacts': [self.artifact]}]
        self.fail(f'Unexpected API request: {route} {arguments}')

    def verify_run(self):
        return release.verify_run(self.run_id, self.commit, root=self.root)

    def test_valid_exact_main_run_and_windows_job(self):
        self.assertEqual(self.verify_run(), self.artifact)
        self.assertIn('filter=latest', self.api_calls[3][0])

    def test_wrong_run_commit_branch_job_or_artifact_is_rejected(self):
        for field, value in [('id', 999), ('head_sha', 'c' * 40), ('head_branch', 'topic'),
                             ('status', 'in_progress'), ('conclusion', 'failure'),
                             ('event', 'pull_request'), ('workflow_id', 999),
                             ('path', '.github/workflows/untrusted.yml'),
                             ('head_repository', {'id': 99})]:
            original = copy.deepcopy(self.run)
            self.run[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'successful same-repository'):
                self.verify_run()
            self.run = original
        for field, value in [('conclusion', 'skipped'), ('head_sha', 'c' * 40),
                             ('run_id', 999), ('status', 'queued')]:
            original = copy.deepcopy(self.jobs)
            self.jobs[0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Windows build'):
                self.verify_run()
            self.jobs = original

    def test_artifact_provenance_and_checkout_must_match_exact_main(self):
        self.artifact['workflow_run']['head_repository_id'] = 99
        with self.assertRaisesRegex(ValueError, 'matching run provenance'):
            self.verify_run()
        self.artifact['workflow_run']['head_repository_id'] = 78
        self.git.return_value.stdout = 'c' * 40
        with self.assertRaisesRegex(ValueError, 'Checked-out source'):
            self.verify_run()
        self.git.return_value.stdout = self.commit
        with mock.patch.dict(os.environ, {'GITHUB_SHA': 'c' * 40}):
            with self.assertRaisesRegex(ValueError, 'manually dispatched'):
                self.verify_run()

    def build_files(self):
        self.directory = self.root / 'artifacts'
        self.directory.mkdir(exist_ok=True)
        payload = self.root / 'payload'
        (payload / '_internal').mkdir(parents=True, exist_ok=True)
        (payload / 'ResponsiveClassroom.exe').write_bytes(b'fake Windows EXE')
        (payload / '_internal/python312.dll').write_bytes(b'fake Windows runtime')
        records = [{'path': path.relative_to(payload).as_posix(), 'bytes': path.stat().st_size,
                    'sha256': desktop.sha256(path)} for path in sorted(payload.rglob('*')) if path.is_file()]
        (payload / 'resource_manifest.json').write_text(json.dumps(
            {'version': '3.1.1', 'platform': 'windows-x64', 'files': records}, indent=2) + '\n')
        archive = self.directory / release.ARCHIVE
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
            for path in sorted(payload.rglob('*')):
                if path.is_file():
                    zipped.write(path, path.relative_to(payload).as_posix())
        digest = desktop.sha256(archive)
        (self.directory / (release.ARCHIVE + '.sha256')).write_text(
            f'{digest}  {archive.name}\n', encoding='ascii')
        (self.directory / release.REPORT).write_text(json.dumps({
            'status': 'ok', 'version': '3.1.1', 'archive': f'C:\\build\\artifacts\\{archive.name}',
            'archive_bytes': archive.stat().st_size, 'archive_sha256': digest,
            'manifest_files': len(records), 'zip_entries': len(records) + 1,
            'manifest_mismatches': 0, 'zip_crc_failures': 0, 'zip_path_mismatches': 0,
        }))
        return [archive, self.directory / (release.ARCHIVE + '.sha256')]

    def test_windows_manifest_checksum_and_integrity_report_are_verified(self):
        assets = self.build_files()
        self.assertEqual(release.verify_artifacts(self.directory, self.run_id, self.commit), assets)
        report = self.directory / release.REPORT
        value = json.loads(report.read_text())
        value['zip_crc_failures'] = 1
        report.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'integrity report'):
            release.verify_artifacts(self.directory, self.run_id, self.commit)

    def make_transport(self, *, extra=False):
        self.build_files()
        transport = self.root / 'transport.zip'
        with zipfile.ZipFile(transport, 'w') as zipped:
            for path in self.directory.iterdir():
                target = f'build/reports/{path.name}' if path.name == release.REPORT else f'artifacts/{path.name}'
                zipped.write(path, target)
            if extra:
                zipped.writestr('../escape.py', 'not executed')
        self.artifact['digest'] = 'sha256:' + desktop.sha256(transport)
        self.artifact['size_in_bytes'] = transport.stat().st_size
        return transport

    def test_download_checks_immutable_transport_and_extracts_only_expected_files(self):
        transport = self.make_transport()
        def fake_download(*args, output=None, **kwargs):
            self.assertEqual(args[1], 'repos/example/classroom/actions/artifacts/456/zip')
            output.write(transport.read_bytes())
        with mock.patch.object(release, 'github', side_effect=fake_download):
            destination = self.root / 'download'
            release.download_artifact(self.artifact, destination)
            self.assertEqual(release.verify_artifacts(destination, 123, self.commit),
                             [destination / release.ARCHIVE, destination / (release.ARCHIVE + '.sha256')])

    def test_download_rejects_bad_digest_or_extra_paths(self):
        transport = self.make_transport()
        self.artifact['digest'] = 'sha256:' + '0' * 64
        with (mock.patch.object(release, 'github', side_effect=lambda *a, output=None, **k:
                                output.write(transport.read_bytes())),
              self.assertRaisesRegex(ValueError, 'transport size or digest')):
            release.download_artifact(self.artifact, self.root / 'bad-digest')
        transport = self.make_transport(extra=True)
        self.artifact['digest'] = 'sha256:' + desktop.sha256(transport)
        self.artifact['size_in_bytes'] = transport.stat().st_size
        with (mock.patch.object(release, 'github', side_effect=lambda *a, output=None, **k:
                                output.write(transport.read_bytes())),
              self.assertRaisesRegex(ValueError, 'must contain only')):
            release.download_artifact(self.artifact, self.root / 'extra-path')
        self.assertFalse((self.root / 'escape.py').exists())

    def remote(self, assets, draft=True):
        return {'id': 888, 'tag_name': 'v3.1.1', 'target_commitish': self.commit,
                'draft': draft, 'prerelease': False,
                'html_url': 'https://github.com/example/classroom/releases/tag/v3.1.1',
                'assets': [{'name': path.name, 'state': 'uploaded', 'size': path.stat().st_size,
                            'digest': 'sha256:' + desktop.sha256(path)} for path in assets]}

    def test_remote_release_requires_exact_assets_and_hashes(self):
        assets = self.build_files()
        payload = self.remote(assets)
        with mock.patch.object(release, 'api', return_value=payload), \
                mock.patch.object(release, 'verify_remote_tag'):
            release.verify_uploaded(888, self.commit, assets, draft=True)
            for change in ('digest', 'size', 'extra', 'draft'):
                bad = copy.deepcopy(payload)
                if change == 'digest':
                    bad['assets'][0]['digest'] = None
                elif change == 'size':
                    bad['assets'][0]['size'] += 1
                elif change == 'extra':
                    bad['assets'].append({'name': 'another.zip'})
                else:
                    bad['draft'] = False
                with self.subTest(change=change), \
                        mock.patch.object(release, 'api', return_value=bad), \
                        self.assertRaises(ValueError):
                    release.verify_uploaded(888, self.commit, assets, draft=True)

    def test_existing_tag_or_draft_is_never_overwritten(self):
        for tags, releases in [([{'ref': 'refs/tags/v3.1.1'}], []),
                               ([], [{'tag_name': 'v3.1.1', 'draft': True}])]:
            with (mock.patch.object(release, 'matching_refs', return_value=tags),
                  mock.patch.object(release, 'release_list', return_value=releases),
                  self.assertRaisesRegex(ValueError, 'refusing to replace')):
                release.require_absent()

    def test_workflow_is_windows_only_manual_main_publisher(self):
        build = (release.ROOT / '.github/workflows/unified-desktop.yml').read_text()
        publish = (release.ROOT / '.github/workflows/publish-unified.yml').read_text()
        self.assertIn('workflow_dispatch:', build)
        self.assertIn('runs-on: windows-latest', build)
        self.assertNotIn('macos-', build)
        self.assertIn('workflow_dispatch:', publish)
        self.assertIn("github.ref == 'refs/heads/main'", publish)
        self.assertIn('runs-on: ubuntu-latest', publish)
        self.assertIn('ref: ${{ github.sha }}', publish)
        self.assertNotIn('\n  push:', publish)
        import re
        for action in re.findall(r'uses: (\S+)', build + publish):
            self.assertRegex(action, r'^[\w/-]+@[0-9a-f]{40}$')


if __name__ == '__main__':
    unittest.main()
