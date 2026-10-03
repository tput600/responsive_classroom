"""Offline release safety checks: no test sends a GitHub request or publishes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import publish_release as release


@mock.patch.dict(os.environ, {'GH_REPO': 'example/classroom'})
class PublishReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='release guard 測試 ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source/classroom_resources.py'
        self.source.parent.mkdir()
        self.source.write_text('VERSION = "3.0.3"\n', encoding='utf-8')
        self.notes = self.root / 'docs/releases/v3.0.3.md'
        self.notes.parent.mkdir(parents=True)
        self.notes.write_text('# Release v3.0.3\n', encoding='utf-8')
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir()
        for target in release.TARGETS:
            archive = self.artifacts / f'ResponsiveClassroom-Portable-{target}-v3.0.3.zip'
            with zipfile.ZipFile(archive, 'w') as zipped:
                zipped.writestr('README.md', f'Package for {target}')
            self.write_checksum(archive)
        self.commit = 'a' * 40

    @staticmethod
    def write_checksum(archive):
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        archive.with_suffix('.zip.sha256').write_text(
            f'{digest}  {archive.name}\n', encoding='ascii')

    def test_exact_three_pairs_are_verified(self):
        assets = release.verify_artifacts(self.artifacts, 'v3.0.3')
        self.assertEqual(len(assets), 6)
        self.assertEqual({path.name for path in assets},
                         {path.name for path in self.artifacts.iterdir()})

    def test_missing_pair_is_rejected(self):
        next(self.artifacts.glob('*.sha256')).unlink()
        with self.assertRaisesRegex(ValueError, 'exactly three'):
            release.verify_artifacts(self.artifacts, 'v3.0.3')

    def test_extra_file_is_rejected(self):
        (self.artifacts / 'unverified.zip').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'exactly three'):
            release.verify_artifacts(self.artifacts, 'v3.0.3')

    def test_incorrect_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exactly three'):
            release.verify_artifacts(self.artifacts, 'v3.0.4')

    def test_changed_archive_is_rejected(self):
        archive = next(self.artifacts.glob('*.zip'))
        archive.write_bytes(archive.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
            release.verify_artifacts(self.artifacts, 'v3.0.3')

    def test_checksum_must_name_the_exact_archive(self):
        checksum = next(self.artifacts.glob('*.sha256'))
        checksum.write_text('a' * 64 + '  ../other.zip\n', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'Malformed checksum'):
            release.verify_artifacts(self.artifacts, 'v3.0.3')

    def test_non_zip_with_valid_checksum_is_rejected(self):
        archive = next(self.artifacts.glob('*.zip'))
        archive.write_bytes(b'not a ZIP')
        self.write_checksum(archive)
        with self.assertRaisesRegex(ValueError, 'invalid ZIP'):
            release.verify_artifacts(self.artifacts, 'v3.0.3')

    @unittest.skipIf(os.name == 'nt', 'Creating symlinks may require Windows privileges')
    def test_symlink_is_rejected(self):
        archive = next(self.artifacts.glob('*.zip'))
        real = self.root / 'real.zip'
        archive.rename(real)
        archive.symlink_to(real)
        with self.assertRaisesRegex(ValueError, 'exactly three'):
            release.verify_artifacts(self.artifacts, 'v3.0.3')

    @mock.patch.object(release, 'release_exists', return_value=False)
    def test_approved_version_is_eligible_on_push(self, exists):
        self.assertTrue(release.prepare('v3.0.3', 'push', root=self.root))
        exists.assert_called_once_with('v3.0.3')

    @mock.patch.object(release, 'release_exists')
    def test_later_versions_never_auto_publish(self, exists):
        self.source.write_text('VERSION = "3.0.4"\n', encoding='utf-8')
        self.assertFalse(release.prepare('v3.0.3', 'push', root=self.root))
        self.assertFalse(release.prepare('v3.0.4', 'push', root=self.root))
        exists.assert_not_called()

    @mock.patch.object(release, 'release_exists', return_value=False)
    def test_explicit_later_matching_version_is_eligible(self, exists):
        self.source.write_text('VERSION = "3.0.4"\n', encoding='utf-8')
        self.notes.with_name('v3.0.4.md').write_text('Approved later release', encoding='utf-8')
        self.assertTrue(release.prepare('v3.0.4', 'workflow_dispatch', root=self.root))
        exists.assert_called_once_with('v3.0.4')

    @mock.patch.object(release, 'release_exists', return_value=True)
    def test_existing_push_release_is_skipped(self, exists):
        self.assertFalse(release.prepare('v3.0.3', 'push', root=self.root))

    @mock.patch.object(release, 'release_exists', return_value=True)
    def test_existing_manual_release_is_rejected(self, exists):
        with self.assertRaisesRegex(ValueError, 'refusing to replace'):
            release.prepare('v3.0.3', 'workflow_dispatch', root=self.root)

    @mock.patch.object(release, 'release_exists')
    def test_mismatch_and_missing_notes_stop_before_api_calls(self, exists):
        with self.assertRaisesRegex(ValueError, 'does not match'):
            release.prepare('v3.0.4', 'workflow_dispatch', root=self.root)
        self.notes.unlink()
        with self.assertRaisesRegex(ValueError, 'release notes'):
            release.prepare('v3.0.3', 'workflow_dispatch', root=self.root)
        exists.assert_not_called()

    def test_only_plain_stable_version_tags_are_allowed(self):
        for tag in ('--help', 'v3.0.3\neligible=true', 'v3.0.3-beta', 'v03.0.3', '../v3.0.3'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.validate_tag(tag)

    @mock.patch.dict(os.environ, {'GH_REPO': 'example/classroom'})
    @mock.patch.object(release, 'github')
    def test_prefix_tag_does_not_count_as_an_existing_exact_tag(self, github):
        github.side_effect = ['[{"ref":"refs/tags/v3.0.30"}]',
                              '[[{"tag_name":"v3.0.2"}],[{"tag_name":"v3.0.30"}]]']
        self.assertFalse(release.release_exists('v3.0.3'))
        self.assertIn('--paginate', github.call_args.args)
        self.assertIn('--slurp', github.call_args.args)

    @mock.patch.dict(os.environ, {'GH_REPO': 'example/classroom'})
    @mock.patch.object(release, 'github')
    def test_either_existing_tag_or_release_prevents_publication(self, github):
        for refs, tags in (('[{"ref":"refs/tags/v3.0.3"}]', '[[]]'),
                           ('[]', '[[{"tag_name":"v3.0.3","draft":true}]]')):
            with self.subTest(refs=refs, tags=tags):
                github.side_effect = [refs, tags]
                self.assertTrue(release.release_exists('v3.0.3'))

    @mock.patch.dict(os.environ, {'GH_REPO': 'example/classroom'})
    @mock.patch.object(release, 'github', side_effect=RuntimeError('HTTP 403: forbidden'))
    def test_authentication_failure_is_never_treated_as_absence(self, github):
        with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
            release.release_exists('v3.0.3')

    @mock.patch.object(release.shutil, 'which', return_value='/usr/bin/gh')
    @mock.patch.object(release.subprocess, 'run')
    def test_cli_errors_are_preserved(self, run, which):
        run.return_value = subprocess.CompletedProcess([], 1, '', 'HTTP 401: Bad credentials')
        with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
            release.github('api', 'repos/example/classroom/releases')
        self.assertNotIn('shell', run.call_args.kwargs)

    def release_payload(self, draft=True):
        return {
            'id': 123, 'tag_name': 'v3.0.3', 'target_commitish': self.commit,
            'draft': draft, 'prerelease': False,
            'html_url': 'https://github.com/example/classroom/releases/tag/v3.0.3',
            'assets': [
                {'name': path.name, 'state': 'uploaded', 'size': path.stat().st_size,
                 'digest': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in self.artifacts.iterdir()],
        }

    @mock.patch.object(release, 'release_exists', return_value=False)
    @mock.patch.object(release, 'github')
    def test_publication_verifies_draft_then_promotes_exact_commit(self, github, exists):
        reference = {'ref': 'refs/tags/v3.0.3', 'object': {'type': 'commit', 'sha': self.commit}}
        github.side_effect = [
            '', json.dumps([[self.release_payload()]]),
            json.dumps(self.release_payload()), '[]', '',
            json.dumps(self.release_payload(draft=False)), json.dumps([reference]),
            json.dumps({'id': 123, 'tag_name': 'v3.0.3'}),
        ]
        release.publish('v3.0.3', self.commit, self.artifacts, root=self.root)
        exists.assert_called_once_with('v3.0.3')
        arguments = github.call_args_list[0].args
        self.assertEqual(arguments[:3], ('release', 'create', 'v3.0.3'))
        self.assertEqual(set(arguments[3:9]), {str(path) for path in self.artifacts.iterdir()})
        self.assertEqual(arguments[arguments.index('--target') + 1], self.commit)
        self.assertEqual(arguments[arguments.index('--notes-file') + 1], str(self.notes))
        self.assertIn('--draft', arguments)
        self.assertNotIn('--latest', arguments)
        self.assertNotIn('--clobber', arguments)
        self.assertEqual(github.call_args_list[0].kwargs, {'timeout': 900})
        self.assertEqual(github.call_args_list[1].args,
                         ('api', '--paginate', '--slurp',
                          'repos/example/classroom/releases?per_page=100'))
        self.assertEqual(github.call_args_list[2].args,
                         ('api', 'repos/example/classroom/releases/123'))
        self.assertEqual(github.call_args_list[4].args,
                         ('api', '--method', 'PATCH', 'repos/example/classroom/releases/123',
                          '-F', 'draft=false', '-f', 'make_latest=true',
                          '-f', 'tag_name=v3.0.3', '-f', f'target_commitish={self.commit}'))
        self.assertEqual(github.call_args_list[5].args,
                         ('api', 'repos/example/classroom/releases/123'))

    @mock.patch.object(release, 'release_exists', return_value=False)
    @mock.patch.object(release, 'github')
    def test_partial_upload_remains_unpublished_draft(self, github, exists):
        details = self.release_payload()
        details['assets'].pop()
        github.side_effect = ['', json.dumps([[details]]), json.dumps(details)]
        with self.assertRaisesRegex(ValueError, 'exactly the six'):
            release.publish('v3.0.3', self.commit, self.artifacts, root=self.root)
        self.assertEqual(github.call_count, 3)
        self.assertEqual(github.call_args_list[0].args[:2], ('release', 'create'))
        self.assertEqual(github.call_args_list[1].args[0], 'api')

    @mock.patch.object(release, 'github')
    def test_incorrect_upload_state_size_or_digest_blocks_promotion(self, github):
        for field, value in (('state', 'starter'), ('size', 0), ('digest', None),
                             ('digest', 'sha256:' + 'b' * 64)):
            with self.subTest(field=field, value=value):
                details = self.release_payload()
                details['assets'][0][field] = value
                github.return_value = json.dumps(details)
                with self.assertRaisesRegex(ValueError, 'upload or digest'):
                    release.verify_uploaded_release('v3.0.3', self.commit,
                                                    list(self.artifacts.iterdir()), draft=True,
                                                    release_id=123)

    @mock.patch.object(release, 'github')
    def test_incorrect_release_commit_or_state_blocks_promotion(self, github):
        for field, value in (('id', 456), ('target_commitish', 'main'), ('tag_name', 'v3.0.4'),
                             ('draft', False), ('prerelease', True)):
            with self.subTest(field=field, value=value):
                details = self.release_payload()
                details[field] = value
                github.return_value = json.dumps(details)
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    release.verify_uploaded_release('v3.0.3', self.commit,
                                                    list(self.artifacts.iterdir()), draft=True,
                                                    release_id=123)

    @mock.patch.object(release, 'github')
    def test_resume_verifies_explicit_draft_without_reuploading(self, github):
        reference = {'ref': 'refs/tags/v3.0.3', 'object': {'type': 'commit', 'sha': self.commit}}
        # A second page proves recovery does not assume the first 100 releases.
        github.side_effect = [
            json.dumps([[{'id': 122, 'tag_name': 'v3.0.2'}], [self.release_payload()]]),
            json.dumps(self.release_payload()), json.dumps([reference]), '',
            json.dumps(self.release_payload(draft=False)), json.dumps([reference]),
            json.dumps({'id': 123, 'tag_name': 'v3.0.3'}),
        ]
        release.resume('v3.0.3', self.commit, self.artifacts, release_id=123, root=self.root)
        self.assertEqual(github.call_args_list[0].args,
                         ('api', '--paginate', '--slurp',
                          'repos/example/classroom/releases?per_page=100'))
        self.assertEqual(github.call_args_list[1].args,
                         ('api', 'repos/example/classroom/releases/123'))
        mutations = [call.args for call in github.call_args_list
                     if call.args[0] != 'api' or '--method' in call.args]
        self.assertEqual(mutations, [
            ('api', '--method', 'PATCH', 'repos/example/classroom/releases/123',
             '-F', 'draft=false', '-f', 'make_latest=true',
             '-f', 'tag_name=v3.0.3', '-f', f'target_commitish={self.commit}')])
        self.assertFalse(any('/releases/tags/' in str(call) for call in github.call_args_list))

    @mock.patch.object(release, 'github')
    def test_resume_refuses_wrong_explicit_id(self, github):
        github.return_value = json.dumps([[self.release_payload()]])
        with self.assertRaisesRegex(ValueError, 'Explicit release ID does not match'):
            release.resume('v3.0.3', self.commit, self.artifacts, release_id=456, root=self.root)
        github.assert_called_once()

    @mock.patch.object(release, 'github')
    def test_resume_refuses_changed_id_commit_or_published_release(self, github):
        for field, value in (('id', 456), ('target_commitish', 'b' * 40), ('draft', False)):
            with self.subTest(field=field):
                github.reset_mock()
                details = self.release_payload()
                details[field] = value
                github.side_effect = [json.dumps([[self.release_payload()]]), json.dumps(details)]
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    release.resume('v3.0.3', self.commit, self.artifacts,
                                   release_id=123, root=self.root)
                self.assertEqual(github.call_count, 2)
                self.assertTrue(all(call.args[0] == 'api' for call in github.call_args_list))

    @mock.patch.object(release, 'github')
    def test_resume_checks_every_remote_asset_digest_size_and_state(self, github):
        for index in range(6):
            for field, value in (('digest', 'sha256:' + 'b' * 64), ('size', 0),
                                 ('state', 'starter')):
                with self.subTest(asset=index, field=field):
                    github.reset_mock()
                    details = self.release_payload()
                    details['assets'][index][field] = value
                    github.side_effect = [json.dumps([[details]]), json.dumps(details)]
                    with self.assertRaisesRegex(ValueError, 'upload or digest'):
                        release.resume('v3.0.3', self.commit, self.artifacts,
                                       release_id=123, root=self.root)
                    self.assertTrue(all(call.args[0] == 'api' for call in github.call_args_list))

    @mock.patch.object(release, 'github')
    def test_resume_refuses_existing_tag_on_wrong_commit(self, github):
        reference = {'ref': 'refs/tags/v3.0.3', 'object': {'type': 'commit', 'sha': 'b' * 40}}
        github.side_effect = [json.dumps([[self.release_payload()]]),
                              json.dumps(self.release_payload()), json.dumps([reference])]
        with self.assertRaisesRegex(ValueError, 'different commit'):
            release.resume('v3.0.3', self.commit, self.artifacts, release_id=123, root=self.root)
        self.assertTrue(all(call.args[0] == 'api' for call in github.call_args_list))

    @mock.patch.object(release, 'github')
    def test_resume_requires_explicit_positive_id_and_verified_local_inputs(self, github):
        for release_id in (None, 0, -1, True, '123'):
            with self.subTest(release_id=release_id), self.assertRaisesRegex(ValueError, 'release ID'):
                release.resume('v3.0.3', self.commit, self.artifacts,
                               release_id=release_id, root=self.root)
        with self.assertRaisesRegex(ValueError, 'exact 40-character'):
            release.resume('v3.0.3', 'main', self.artifacts, release_id=123, root=self.root)
        next(self.artifacts.glob('*.zip')).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
            release.resume('v3.0.3', self.commit, self.artifacts, release_id=123, root=self.root)
        github.assert_not_called()

    @mock.patch.object(release, 'resume')
    def test_resume_cli_requires_release_id(self, resume):
        with mock.patch.object(sys, 'argv', ['publish_release.py', 'resume', '--tag', 'v3.0.3',
                                            '--commit', self.commit, '--artifacts', str(self.artifacts)]):
            with mock.patch('sys.stderr'), self.assertRaises(SystemExit) as error:
                release.main()
        self.assertEqual(error.exception.code, 2)
        resume.assert_not_called()

    @mock.patch.object(release, 'github')
    def test_release_lookup_refuses_missing_duplicate_or_malformed_list(self, github):
        for response in ('[[]]', json.dumps([[self.release_payload(), self.release_payload()]]),
                         '[[{"tag_name":"v3.0.3","id":null}]]'):
            with self.subTest(response=response):
                github.return_value = response
                with self.assertRaisesRegex(ValueError, 'exactly one remote release'):
                    release.find_release_id('v3.0.3')
        for response in ('{}', '[{}]', '[[null]]'):
            with self.subTest(response=response):
                github.return_value = response
                with self.assertRaisesRegex(RuntimeError, 'Unexpected GitHub releases'):
                    release.find_release_id('v3.0.3')

    @mock.patch.object(release, 'github')
    def test_absent_draft_tag_is_allowed_but_published_tag_is_required(self, github):
        github.return_value = '[]'
        release.verify_remote_tag('v3.0.3', self.commit, required=False)
        with self.assertRaisesRegex(ValueError, 'no matching Git tag'):
            release.verify_remote_tag('v3.0.3', self.commit, required=True)

    @mock.patch.object(release, 'github')
    def test_annotated_tag_must_resolve_to_exact_verified_commit(self, github):
        annotated = {'ref': 'refs/tags/v3.0.3', 'object': {'type': 'tag', 'sha': 'c' * 40}}
        for commit in (self.commit, 'b' * 40):
            with self.subTest(commit=commit):
                github.side_effect = [json.dumps([annotated]),
                                      json.dumps({'object': {'type': 'commit', 'sha': commit}})]
                if commit == self.commit:
                    release.verify_remote_tag('v3.0.3', self.commit, required=True)
                else:
                    with self.assertRaisesRegex(ValueError, 'different commit'):
                        release.verify_remote_tag('v3.0.3', self.commit, required=False)

    @mock.patch.object(release, 'release_exists', return_value=False)
    @mock.patch.object(release, 'github')
    def test_timeout_never_promotes_draft(self, github, exists):
        github.side_effect = subprocess.TimeoutExpired('gh', 900)
        with self.assertRaises(subprocess.TimeoutExpired):
            release.publish('v3.0.3', self.commit, self.artifacts, root=self.root)
        github.assert_called_once()
        self.assertEqual(github.call_args.args[:2], ('release', 'create'))

    @mock.patch.object(release, 'release_exists', return_value=True)
    @mock.patch.object(release, 'github')
    def test_tag_created_during_build_blocks_publication(self, github, exists):
        with self.assertRaisesRegex(ValueError, 'refusing to replace'):
            release.publish('v3.0.3', self.commit, self.artifacts, root=self.root)
        github.assert_not_called()

    @mock.patch.object(release, 'release_exists')
    @mock.patch.object(release, 'github')
    def test_unverified_artifacts_and_non_sha_target_never_publish(self, github, exists):
        with self.assertRaisesRegex(ValueError, 'exact 40-character'):
            release.publish('v3.0.3', 'main', self.artifacts, root=self.root)
        next(self.artifacts.glob('*.zip')).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
            release.publish('v3.0.3', self.commit, self.artifacts, root=self.root)
        exists.assert_not_called()
        github.assert_not_called()


if __name__ == '__main__':
    unittest.main()
