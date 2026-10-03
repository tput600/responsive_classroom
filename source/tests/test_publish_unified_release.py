"""Offline unified publication checks: fake gh only, no tokens or network writes."""
import copy
import hashlib
import json
import os
import plistlib
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tools import assemble_desktop_archive as desktop
from tools import publish_unified_release as release
from tools.macos_package import tree_records, write_archive


@mock.patch.dict(os.environ, {'GH_REPO': 'example/classroom', 'GITHUB_REPOSITORY': 'example/classroom',
                              'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                              'GITHUB_SHA': 'a' * 40})
class UnifiedPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='unified release 測試 ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'source').mkdir()
        (self.root / 'source/classroom_resources.py').write_text('VERSION = "3.1.0"\n')
        (self.root / 'docs/releases').mkdir(parents=True)
        (self.root / 'docs/releases/v3.1.0.md').write_text('# Release 3.1.0\n')
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
        self.bootstrap_sha = 'd' * 40
        self.bootstrap = {'sha': self.bootstrap_sha, 'parents': [{'sha': self.commit}],
                          'files': [{'filename': release.PUBLICATION_WORKFLOW, 'status': 'modified'}]}
        self.bootstrap_tip = self.bootstrap_sha
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
        if route == f'commits/{self.bootstrap_sha}':
            return self.bootstrap
        if route == f'git/ref/heads/{release.BOOTSTRAP_BRANCH}':
            return {'object': {'type': 'commit', 'sha': self.bootstrap_tip}}
        if route == 'actions/workflows/unified-desktop.yml':
            return {'id': 44, 'path': release.WORKFLOW}
        if route == 'actions/runs/123':
            return self.run
        if route == 'actions/runs/123/jobs?filter=latest&per_page=100':
            return [{'jobs': self.jobs[:2]}, {'jobs': self.jobs[2:]}]
        if route == 'actions/runs/123/artifacts?per_page=100':
            return [{'artifacts': [self.artifact]}]
        self.fail(f'Unexpected API request: {route} {arguments}')

    def verify_run(self):
        return release.verify_run(self.run_id, self.commit, root=self.root)

    def test_valid_exact_main_run_and_paginated_jobs(self):
        self.assertEqual(self.verify_run(), self.artifact)
        self.assertIn('filter=latest', self.api_calls[3][0])

    def test_exact_approved_main_commit_can_come_from_isolated_build_branch(self):
        self.run['head_branch'] = 'codex/unified-desktop-package'
        self.artifact['workflow_run']['head_branch'] = self.run['head_branch']
        self.assertEqual(self.verify_run(), self.artifact)
        self.artifact['workflow_run']['head_branch'] = 'main'
        with self.assertRaisesRegex(ValueError, 'matching run provenance'):
            self.verify_run()

    def bootstrap_env(self):
        return {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': f'refs/heads/{release.BOOTSTRAP_BRANCH}',
                'GITHUB_SHA': self.bootstrap_sha, 'APPROVED_RELEASE_COMMIT': self.commit,
                'APPROVED_CANDIDATE_RUN_ID': str(self.run_id)}

    def test_controlled_push_bootstrap_uses_exact_approved_checkout_and_run(self):
        with mock.patch.dict(os.environ, self.bootstrap_env()):
            self.assertEqual(self.verify_run(), self.artifact)
        routes = [call[0] for call in self.api_calls]
        self.assertIn(f'repos/example/classroom/commits/{self.bootstrap_sha}', routes)
        self.assertIn(f'repos/example/classroom/git/ref/heads/{release.BOOTSTRAP_BRANCH}', routes)

    def test_bootstrap_rejects_missing_or_wrong_approval_pins(self):
        for key, value in [('APPROVED_RELEASE_COMMIT', ''), ('APPROVED_RELEASE_COMMIT', 'c' * 40),
                           ('APPROVED_CANDIDATE_RUN_ID', ''), ('APPROVED_CANDIDATE_RUN_ID', '999'),
                           ('GITHUB_SHA', self.commit), ('GITHUB_SHA', 'not-a-sha')]:
            env = dict(self.bootstrap_env(), **{key: value})
            with (
                self.subTest(key=key, value=value),
                mock.patch.dict(os.environ, env),
                self.assertRaisesRegex(ValueError, 'exact branch, approved commit and candidate run pins'),
            ):
                self.verify_run()

    def test_bootstrap_rejects_main_push_or_other_branch_push(self):
        for branch in ('main', 'codex/unified-desktop-package', 'unrelated', 'refs/tags/v3.1.0'):
            env = dict(self.bootstrap_env(), GITHUB_REF=f'refs/heads/{branch}')
            with (
                self.subTest(branch=branch),
                mock.patch.dict(os.environ, env),
                self.assertRaisesRegex(ValueError, 'manually dispatched'),
            ):
                self.verify_run()
        self.api.assert_not_called()

    def test_bootstrap_must_have_only_approved_main_as_direct_parent(self):
        for parents in ([], [{'sha': 'c' * 40}], [{'sha': self.commit}, {'sha': 'c' * 40}]):
            self.bootstrap['parents'] = parents
            with (
                self.subTest(parents=parents),
                mock.patch.dict(os.environ, self.bootstrap_env()),
                self.assertRaisesRegex(ValueError, 'directly follow approved main'),
            ):
                self.verify_run()

    def test_bootstrap_cannot_change_source_or_rename_or_add_workflow(self):
        for files in ([], [{'filename': 'source/tools/publish_unified_release.py', 'status': 'modified'}],
                      [{'filename': release.PUBLICATION_WORKFLOW, 'status': 'renamed'}],
                      [{'filename': release.PUBLICATION_WORKFLOW, 'status': 'added'}],
                      self.bootstrap['files'] + [{'filename': 'README.md', 'status': 'modified'}]):
            self.bootstrap['files'] = files
            with (
                self.subTest(files=files),
                mock.patch.dict(os.environ, self.bootstrap_env()),
                self.assertRaisesRegex(ValueError, 'change only the publication workflow'),
            ):
                self.verify_run()

    def test_bootstrap_commit_identity_and_current_branch_tip_must_match(self):
        with mock.patch.dict(os.environ, self.bootstrap_env()):
            self.bootstrap['sha'] = 'c' * 40
            with self.assertRaisesRegex(ValueError, 'directly follow approved main'):
                self.verify_run()
            self.bootstrap['sha'] = self.bootstrap_sha
            self.bootstrap_tip = 'c' * 40
            with self.assertRaisesRegex(ValueError, 'no longer points'):
                self.verify_run()

    def test_bootstrap_must_checkout_approved_source_not_bootstrap_source(self):
        self.git.return_value.stdout = self.bootstrap_sha
        with (
            mock.patch.dict(os.environ, self.bootstrap_env()),
            self.assertRaisesRegex(ValueError, 'Checked-out source'),
        ):
            self.verify_run()
        self.api.assert_not_called()

    def test_dispatch_must_be_main_same_repository_and_commit(self):
        for field, value in [('GITHUB_REF', 'refs/heads/topic'), ('GITHUB_EVENT_NAME', 'push'),
                             ('GITHUB_SHA', 'b' * 40), ('GITHUB_REPOSITORY', 'other/repo')]:
            with (
                self.subTest(field=field),
                mock.patch.dict(os.environ, {field: value}),
                self.assertRaisesRegex(ValueError, 'manually dispatched'),
            ):
                self.verify_run()
        self.api.assert_not_called()

    def test_source_checkout_and_version_must_match(self):
        self.git.return_value.stdout = 'c' * 40
        with self.assertRaisesRegex(ValueError, 'Checked-out source'):
            self.verify_run()
        self.git.return_value.stdout = self.commit
        (self.root / 'source/classroom_resources.py').write_text('VERSION = "3.0.3"\n')
        with self.assertRaisesRegex(ValueError, 'version must be 3.1.0'):
            self.verify_run()
        self.api.assert_not_called()

    def test_old_main_tip_is_rejected(self):
        with (
            mock.patch.object(release, 'api', return_value={'object': {'type': 'commit', 'sha': 'c' * 40}}),
            self.assertRaisesRegex(ValueError, 'current main tip'),
        ):
            self.verify_run()

    def test_bad_run_identity_and_state_are_rejected(self):
        for key, value in [('id', 999), ('head_sha', 'c' * 40), ('head_branch', 'topic'),
                           ('status', 'in_progress'), ('conclusion', 'failure'),
                           ('event', 'pull_request'), ('workflow_id', 999),
                           ('path', '.github/workflows/untrusted.yml'),
                           ('head_repository', {'id': 99})]:
            original = copy.deepcopy(self.run)
            self.run[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'successful same-repository'):
                self.verify_run()
            self.run = original

    def test_missing_skipped_failed_wrong_sha_and_duplicate_jobs_are_rejected(self):
        original = copy.deepcopy(self.jobs)
        variants = [original[:-1], original + [original[0]]]
        for field, value in [('conclusion', 'skipped'), ('conclusion', 'failure'),
                             ('head_sha', 'c' * 40), ('run_id', 999), ('status', 'queued')]:
            jobs = copy.deepcopy(original)
            jobs[0][field] = value
            variants.append(jobs)
        for jobs in variants:
            self.jobs = jobs
            with self.subTest(jobs=jobs), self.assertRaisesRegex(ValueError, 'must all pass'):
                self.verify_run()

    def test_expired_digestless_wrong_run_and_fork_artifacts_are_rejected(self):
        original = copy.deepcopy(self.artifact)
        variants = [{'expired': True}, {'digest': None}, {'digest': 'sha256:' + 'x' * 64},
                    {'id': True}, {'size_in_bytes': 0}, {'name': release.ARTIFACT + '-candidate'}]
        for key, value in [('id', 999), ('head_sha', 'c' * 40), ('head_branch', 'topic'),
                           ('head_repository_id', 99), ('repository_id', 99)]:
            origin = copy.deepcopy(original['workflow_run'])
            origin[key] = value
            variants.append({'workflow_run': origin})
        for changes in variants:
            self.artifact = dict(original, **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.verify_run()

    def test_api_failure_never_means_candidate_success(self):
        self.api.side_effect = RuntimeError('HTTP 403')
        with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
            self.verify_run()

    def build_assets(self):
        """Portable byte fixtures for remote digest and publication-order guards."""
        self.directory = self.root / 'artifacts'
        self.directory.mkdir()
        archive = self.directory / release.ARCHIVE
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('README.txt', 'Synthetic upload fixture; no native payload')
        checksum = self.directory / (release.ARCHIVE + '.sha256')
        checksum.write_text(f'{desktop.sha256(archive)}  {archive.name}\n', encoding='ascii')
        return [archive, checksum]

    def build_files(self):
        # The real assembler deliberately requires POSIX modes and symlinks.
        # Windows still runs the independent run/provenance/publication guards.
        if os.name != 'posix':
            self.skipTest('Payload assembly requires a POSIX filesystem; safety guards run independently')
        windows = self.root / 'windows'
        windows.mkdir()
        (windows / '_internal').mkdir()
        (windows / 'ResponsiveClassroom.exe').write_bytes(b'fake Windows EXE')
        (windows / '_internal/python312.dll').write_bytes(b'fake Windows runtime')
        records = [{'path': path.relative_to(windows).as_posix(), 'bytes': path.stat().st_size,
                    'sha256': desktop.sha256(path)} for path in sorted(windows.rglob('*')) if path.is_file()]
        (windows / 'resource_manifest.json').write_bytes(desktop.json_bytes(
            {'version': '3.1.0', 'platform': 'windows-x64', 'files': records}))
        winzip = self.root / 'windows.zip'
        with zipfile.ZipFile(winzip, 'w') as zipped:
            for path in windows.rglob('*'):
                if path.is_file():
                    zipped.write(path, path.relative_to(windows).as_posix())
        macos = self.root / 'macos'
        executable = macos / 'ResponsiveClassroom.app/Contents/MacOS/ResponsiveClassroom'
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b'fake Mac executable')
        executable.chmod(0o755)
        (executable.parent.parent / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleVersion': '3.1.0', 'CFBundleShortVersionString': '3.1.0',
            'LSMinimumSystemVersion': '14.0'}))
        (macos / 'resource_manifest.json').write_bytes(desktop.json_bytes(
            {'version': '3.1.0', 'platform': 'macos-universal2', 'files': tree_records(macos)}))
        maczip = self.root / 'macos.zip'
        write_archive(macos, maczip)
        self.evidence = {'commit': self.commit, 'run': 'https://github.com/example/classroom/actions/runs/123',
                         'windows_job': 'passed', 'macos_native': [
                             {'machine': machine, 'version': '3.1.0', 'archive_sha256': desktop.sha256(maczip),
                              'frozen_model': 'passed', 'frozen_ui': 'passed', 'signature': 'ad-hoc-verified',
                              'universal_binaries': 8} for machine in ('arm64', 'x86_64')]}
        evidence = self.root / 'evidence.json'
        # Match the real workflow: insertion-ordered, indented JSON, not
        # pre-sorted fixture JSON that could hide canonicalization bugs.
        evidence.write_text(json.dumps(self.evidence, indent=2) + '\n', encoding='utf-8')
        self.directory = self.root / 'artifacts'
        report = desktop.assemble(winzip, maczip, self.directory / release.ARCHIVE,
                                  version='3.1.0', validation_evidence=evidence)
        (self.directory / release.REPORT).write_bytes(desktop.json_bytes(report))
        return [self.directory / release.ARCHIVE, self.directory / (release.ARCHIVE + '.sha256')]

    def test_real_assembled_payload_and_embedded_native_evidence_pass(self):
        assets = self.build_files()
        self.assertEqual(release.verify_artifacts(self.directory, 123, self.commit), assets)
        self.api.assert_not_called()

    def test_native_evidence_canonical_hash_survives_key_order_and_unicode(self):
        # Semantic evidence validation is portable; no POSIX payload fixture.
        evidence = {'commit': self.commit,
                    'run': 'https://github.com/example/classroom/actions/runs/123',
                    'windows_job': 'passed', 'note': '雙架構原生驗證',
                    'macos_native': [{'machine': machine, 'version': '3.1.0',
                        'archive_sha256': 'f' * 64, 'frozen_model': 'passed',
                        'frozen_ui': 'passed', 'signature': 'ad-hoc-verified',
                        'universal_binaries': 8} for machine in ('arm64', 'x86_64')]}
        proof = {'report': evidence,
                 'sha256': hashlib.sha256(desktop.json_bytes(evidence)).hexdigest()}
        manifest = {'validation_evidence': proof, 'inputs': {
            platform: {'sha256': 'f' * 64, 'bytes': 1} for platform in ('macos', 'windows')}}
        proof['report'] = dict(reversed(list(proof['report'].items())))
        release.verify_evidence(manifest, 123, self.commit)
        proof['report']['note'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'report hash'):
            release.verify_evidence(manifest, 123, self.commit)

    def test_checksum_extra_files_and_candidate_report_fail(self):
        self.build_files()
        report_path = self.directory / release.REPORT
        report = json.loads(report_path.read_text())
        report['candidate'] = True
        report_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'integrity report'):
            release.verify_artifacts(self.directory, 123, self.commit)
        (self.directory / 'extra.txt').write_text('extra')
        with self.assertRaisesRegex(ValueError, 'Expected exactly'):
            release.verify_artifacts(self.directory, 123, self.commit)
        (self.directory / 'extra.txt').unlink()
        (self.directory / (release.ARCHIVE + '.sha256')).write_text('0' * 64 + '  ' + release.ARCHIVE)
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            release.verify_artifacts(self.directory, 123, self.commit)

    def test_native_proof_wrong_run_commit_machine_and_archive_fail(self):
        self.build_files()
        with zipfile.ZipFile(self.directory / release.ARCHIVE) as zipped:
            manifest = json.loads(zipped.read(desktop.MANIFEST))
        original = copy.deepcopy(manifest)
        for field, value in [('commit', 'c' * 40), ('run', 'https://github.com/example/classroom/actions/runs/999'),
                             ('windows_job', 'failed')]:
            manifest = copy.deepcopy(original)
            manifest['validation_evidence']['report'][field] = value
            manifest['validation_evidence']['sha256'] = hashlib.sha256(
                (json.dumps(manifest['validation_evidence']['report'], indent=2) + '\n').encode()).hexdigest()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Native evidence'):
                release.verify_evidence(manifest, 123, self.commit)
        for field, value in [('machine', 'arm64'), ('archive_sha256', 'c' * 64),
                             ('frozen_model', 'failed'), ('frozen_ui', 'skipped'),
                             ('version', '3.0.3'), ('universal_binaries', 0)]:
            manifest = copy.deepcopy(original)
            manifest['validation_evidence']['report']['macos_native'][1][field] = value
            manifest['validation_evidence']['sha256'] = hashlib.sha256(
                (json.dumps(manifest['validation_evidence']['report'], indent=2) + '\n').encode()).hexdigest()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Native evidence'):
                release.verify_evidence(manifest, 123, self.commit)

    def test_native_evidence_hash_is_verified(self):
        self.build_files()
        with zipfile.ZipFile(self.directory / release.ARCHIVE) as zipped:
            manifest = json.loads(zipped.read(desktop.MANIFEST))
        manifest['validation_evidence']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'report hash mismatch'):
            release.verify_evidence(manifest, 123, self.commit)

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

    def test_exact_id_download_checks_transport_and_copies_only_expected_files(self):
        transport = self.make_transport()
        def fake_download(*args, output=None, **kwargs):
            self.assertEqual(args[1], 'repos/example/classroom/actions/artifacts/456/zip')
            self.assertIn(f'X-GitHub-Api-Version: {release.API_VERSION}', args)
            output.write(transport.read_bytes())
        with mock.patch.object(release, 'github', side_effect=fake_download):
            destination = self.root / 'download'
            release.download_artifact(self.artifact, destination)
            self.assertEqual(release.verify_artifacts(destination, 123, self.commit),
                             [destination / release.ARCHIVE, destination / (release.ARCHIVE + '.sha256')])

    def test_download_digest_failure_cannot_extract(self):
        transport = self.make_transport()
        self.artifact['digest'] = 'sha256:' + '0' * 64
        with (
            mock.patch.object(release, 'github', side_effect=lambda *args, output=None, **kwargs: output.write(transport.read_bytes())),
            self.assertRaisesRegex(ValueError, 'transport size or digest'),
        ):
            release.download_artifact(self.artifact, self.root / 'download')
        self.assertEqual(list((self.root / 'download').iterdir()), [])

    def test_download_extra_or_traversal_path_cannot_extract(self):
        transport = self.make_transport(extra=True)
        with (
            mock.patch.object(release, 'github', side_effect=lambda *args, output=None, **kwargs: output.write(transport.read_bytes())),
            self.assertRaisesRegex(ValueError, 'must contain only'),
        ):
            release.download_artifact(self.artifact, self.root / 'download')
        self.assertFalse((self.root / 'escape.py').exists())

    def remote(self, assets, draft=True):
        return {'id': 888, 'tag_name': 'v3.1.0', 'target_commitish': self.commit,
                'draft': draft, 'prerelease': False,
                'html_url': 'https://github.com/example/classroom/releases/tag/v3.1.0',
                'assets': [{'name': path.name, 'state': 'uploaded', 'size': path.stat().st_size,
                            'digest': 'sha256:' + desktop.sha256(path)} for path in assets]}

    def test_remote_verification_uses_numeric_draft_id_and_exact_two_assets(self):
        assets = self.build_assets()
        payload = self.remote(assets)
        with mock.patch.object(release, 'api', return_value=payload) as api, \
                mock.patch.object(release, 'verify_remote_tag'):
            release.verify_uploaded(888, self.commit, assets, draft=True)
            self.assertEqual(api.call_args.args[0], 'repos/example/classroom/releases/888')
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
                api.return_value = bad
                with self.subTest(change=change), self.assertRaises(ValueError):
                    release.verify_uploaded(888, self.commit, assets, draft=True)

    def test_existing_tag_or_draft_never_gets_overwritten(self):
        for tags, releases in [([{'ref': 'refs/tags/v3.1.0'}], []),
                               ([], [{'tag_name': 'v3.1.0', 'draft': True}])]:
            with (
                mock.patch.object(release, 'matching_refs', return_value=tags),
                mock.patch.object(release, 'release_list', return_value=releases),
                self.assertRaisesRegex(ValueError, 'refusing to replace'),
            ):
                release.require_absent()

    def test_older_release_is_ignored_and_never_modified(self):
        with mock.patch.object(release, 'matching_refs', return_value=[]), \
                mock.patch.object(release, 'release_list', return_value=[{'id': 111, 'tag_name': 'v3.0.3'}]):
            release.require_absent()
        self.api.assert_not_called()

    def test_invalid_draft_id_is_rejected_before_any_api(self):
        for value in (None, 0, -1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.execute('resume', 123, self.commit, self.root / 'out', release_id=value, root=self.root)
        self.api.assert_not_called()

    def test_publish_order_creates_draft_then_promotes_and_never_clobbers(self):
        assets = self.build_assets()
        operations = []
        with mock.patch.object(release, 'verify_run', side_effect=lambda *a, **kw:
                               operations.append('run') or self.artifact), \
                mock.patch.object(release, 'require_absent', side_effect=lambda: operations.append('absent')), \
                mock.patch.object(release, 'download_artifact', side_effect=lambda *a: operations.append('download')), \
                mock.patch.object(release, 'verify_artifacts', side_effect=lambda *a: operations.append('verify') or assets), \
                mock.patch.object(release, 'github', side_effect=lambda *a, **kw: operations.append(a)) as gh, \
                mock.patch.object(release, 'find_release_id', side_effect=lambda: operations.append('find-id') or 888), \
                mock.patch.object(release, 'promote', side_effect=lambda *a, **kw: operations.append('promote')):
            release.execute('publish', 123, self.commit, self.directory, root=self.root)
        self.assertEqual(operations[:6], ['run', 'absent', 'download', 'verify', 'run', 'absent'])
        command = gh.call_args.args
        self.assertEqual(command[:3], ('release', 'create', 'v3.1.0'))
        self.assertEqual(command[3:5], tuple(str(path) for path in assets))
        self.assertIn('--draft', command)
        self.assertNotIn('--clobber', command)
        self.assertNotIn('--latest', command)
        self.assertEqual(operations[-2:], ['find-id', 'promote'])

    def test_verify_and_resume_never_create_or_upload(self):
        assets = self.build_assets()
        with mock.patch.object(release, 'verify_run', return_value=self.artifact), \
                mock.patch.object(release, 'download_artifact'), \
                mock.patch.object(release, 'verify_artifacts', return_value=assets), \
                mock.patch.object(release, 'find_release_id', return_value=888), \
                mock.patch.object(release, 'promote') as promote, \
                mock.patch.object(release, 'github') as gh:
            release.execute('verify', 123, self.commit, self.directory, root=self.root)
            promote.assert_not_called()
            release.execute('resume', 123, self.commit, self.directory, release_id=888, root=self.root)
            promote.assert_called_once()
            gh.assert_not_called()

    def test_remote_digest_failure_prevents_promotion(self):
        with (
            mock.patch.object(release, 'verify_uploaded', side_effect=ValueError('digest mismatch')),
            self.assertRaisesRegex(ValueError, 'digest mismatch'),
        ):
            release.promote(888, 123, self.commit, self.artifact, [])
        self.api.assert_not_called()

    def test_promote_rechecks_then_patches_only_exact_id_before_latest_check(self):
        with mock.patch.object(release, 'verify_uploaded', return_value={'html_url': 'verified release'}) as verify, \
                mock.patch.object(release, 'verify_run', return_value=self.artifact), \
                mock.patch.object(release, 'api', side_effect=[{}, {'id': 888, 'tag_name': 'v3.1.0'}]) as api:
            release.promote(888, 123, self.commit, self.artifact, [])
            self.assertEqual(verify.call_args_list[0].kwargs, {'draft': True})
            self.assertEqual(verify.call_args_list[1].kwargs, {'draft': False})
            args = api.call_args_list[0].args
            self.assertEqual(args[0], 'repos/example/classroom/releases/888')
            self.assertIn('PATCH', args)
            self.assertIn('make_latest=true', args)
            self.assertEqual(api.call_args_list[1].args[0], 'repos/example/classroom/releases/latest')

    def test_changed_main_or_artifact_prevents_promotion(self):
        with (
            mock.patch.object(release, 'verify_uploaded'),
            mock.patch.object(release, 'verify_run', return_value=dict(self.artifact, id=999)),
            self.assertRaisesRegex(ValueError, 'metadata changed'),
        ):
            release.promote(888, 123, self.commit, self.artifact, [])
        self.api.assert_not_called()

    def test_remote_tag_wrong_commit_is_rejected(self):
        with (
            mock.patch.object(release, 'matching_refs', return_value=[{'object': {'type': 'commit', 'sha': 'c' * 40}}]),
            self.assertRaisesRegex(ValueError, 'verified commit'),
        ):
            release.verify_remote_tag(self.commit, required=True)

    def test_numeric_draft_lookup_does_not_use_tag_endpoint(self):
        with mock.patch.object(release, 'release_list', return_value=[
                {'id': 111, 'tag_name': 'v3.0.3'}, {'id': 888, 'tag_name': 'v3.1.0', 'draft': True}]):
            self.assertEqual(release.find_release_id(), 888)

    def test_workflow_is_manual_main_only_and_actions_are_immutable(self):
        text = (release.ROOT / '.github/workflows/publish-unified.yml').read_text()
        self.assertIn('workflow_dispatch:', text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn('ref: ${{ github.sha }}', text)
        self.assertIn('persist-credentials: false', text)
        self.assertNotIn('\n  push:', text)
        self.assertNotIn('\n  workflow_run:', text)
        self.assertNotIn('gh release', text)
        import re
        for action in re.findall(r'uses: (\S+)', text):
            self.assertRegex(action, r'^[\w/-]+@[0-9a-f]{40}$')


if __name__ == '__main__':
    unittest.main()
