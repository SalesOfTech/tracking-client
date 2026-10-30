import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publish_accepted as resume


class CompletedAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock(repo='SalesOfTech/tracking-client')
        self.api.api.return_value = {'status': 'ahead'}
        self.run = dict(id=123, run_attempt=1, head_sha='a' * 40, status='completed', conclusion='failure',
                        event='workflow_dispatch', head_branch='main', path='.github/workflows/accept-release.yml',
                        head_repository={'full_name': self.api.repo}, actor={'login': 'owner'},
                        triggering_actor={'login': 'owner'})
        self.keys = {'test': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='}
        self.plan = dict(acceptance_run_id=123, acceptance_run_attempt=1, harness_sha='a' * 40,
                         repository=self.api.repo, version='3.5.2', dispatch_actor='owner',
                         triggering_actor='owner', trusted_keys=self.keys)
        names = ['prepare', 'promote', 'verify-macos-15-arm64', 'verify-macos-26-arm64']
        names += ['accept-' + row['target'] for row in resume.gate.TARGETS]
        self.jobs = [dict(name=name, status='completed', conclusion='failure' if name == 'promote' else 'success')
                     for name in names]
        self.api.jobs.return_value = self.jobs

    def check(self, run=None, plan=None):
        with patch.object(resume.gate, 'VERSION', '3.5.2'), \
                patch.dict(os.environ, TRACKING_TRUSTED_UPDATE_KEYS=json.dumps(self.keys), GITHUB_SHA='b' * 40), \
                patch.object(resume.gate, 'accepted_jobs') as native, \
                patch.object(resume.gate, 'revalidate') as candidate:
            resume.validate_record(self.api, run or self.run, plan or self.plan)
            native.assert_called_once_with(self.api, plan or self.plan)
            candidate.assert_called_once_with(self.api, plan or self.plan)

    def test_all_native_checks_revalidated_after_publication_failure(self):
        self.check()

    def test_failed_skipped_missing_or_duplicate_native_job_is_rejected(self):
        for conclusion in ('failure', 'cancelled', 'skipped', None):
            with self.subTest(conclusion=conclusion):
                jobs = copy.deepcopy(self.jobs)
                jobs[-1]['conclusion'] = conclusion
                self.api.jobs.return_value = jobs
                with self.assertRaises(ValueError):
                    self.check()
        for jobs in (self.jobs[:-1], self.jobs + [self.jobs[-1]]):
            self.api.jobs.return_value = jobs
            with self.assertRaises(ValueError):
                self.check()

    def test_wrong_run_provenance_is_rejected(self):
        for key, value in [('status', 'in_progress'), ('conclusion', 'cancelled'), ('head_branch', 'other'),
                           ('event', 'push'), ('path', '.github/workflows/other.yml'), ('run_attempt', 2),
                           ('head_sha', 'c' * 40), ('id', 456)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.check(run=dict(self.run, **{key: value}))

    def test_changed_version_keyring_or_diverged_harness_is_rejected(self):
        for changes in ({'version': '3.5.1'}, {'trusted_keys': {}}, {'dispatch_actor': 'other'},
                        {'repository': 'Other/Repo'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check(plan=dict(self.plan, **changes))
        self.api.api.return_value = {'status': 'diverged'}
        with self.assertRaises(ValueError):
            self.check()


if __name__ == '__main__':
    unittest.main()
