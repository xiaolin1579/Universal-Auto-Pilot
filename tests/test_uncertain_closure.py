from copy import deepcopy
import tempfile
import unittest
from pathlib import Path

from auto_upload import UploadBlocked, UploadLedger
from torrent_store import MappingStore


class UncertainClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = MappingStore(root / 'mapping.json')
        self.store.initialize()
        self.ledger = UploadLedger(self.store, root / 'artifacts')
        self.key = 'a' * 64
        self.group = 'f' * 64
        self.store.update(lambda data: data['content_groups'].update({
            self.group: {'id': self.group, 'hashes': ['1' * 40], 'primary_site': 'BEARBIT'}}))
        self.job = {
            'id': self.key, 'state': 'publication_uncertain', 'updated_at': '2026-11-21T00:00:00+00:00',
            'source_hash': '1' * 40, 'source_node': 'ReadyIDC', 'save_path': '/downloads',
            'target_site': 'DEDBIT', 'source_zone': 'normal', 'target_zone': 'normal',
            'target_category': '35', 'content_group_id': self.group,
            'prepared_hash': '2' * 40, 'metadata': {'title': 'Test', 'category': 'test'},
            'rule': {}, 'field_overrides': {},
            'repack': None, 'repack_request': None,
            'listing': None, 'target_hash': None, 'submission_signature': 'c' * 64,
            'submission_started_at': '2026-11-19T00:00:00+00:00',
            'last_publication_failure': {'response_code': 'upload_failed_unclassified'},
            'publication_check': {'attempts': 6, 'found': False},
        }
        self.store.update(lambda data: data['upload_jobs'].update({self.key: deepcopy(self.job)}))
        self.evidence = {'method': 'artifact_and_read_only_site_check',
                         'publication_retry_attempted': False,
                         'artifact_checks': {'source_hash': True}}

    def test_closes_stale_uncertain_job_with_audit_evidence(self):
        result = self.ledger.close_uncertain_offline(self.key, self.job, evidence=self.evidence)
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(result['publication_resolution']['evidence'], self.evidence)
        self.assertEqual(result['publication_check'], {'attempts': 6, 'found': False})
        entry = result['publication_history'][-1]
        self.assertEqual(entry['resolution'], 'operator_confirmed_not_published_offline')
        self.assertEqual(entry['state'], 'publication_uncertain')
        self.assertEqual(self.ledger.review(self.key)['publication_resolution'],
                         result['publication_resolution'])
        data = self.store.read()
        self.assertEqual(data['upload_jobs'][self.key]['state'], 'cancelled')

    def test_rejects_changed_job(self):
        def mutate(data):
            data['upload_jobs'][self.key]['updated_at'] = '2026-11-21T01:00:00+00:00'
        self.store.update(mutate)
        with self.assertRaisesRegex(UploadBlocked, 'changed'):
            self.ledger.close_uncertain_offline(self.key, self.job, evidence=self.evidence)
        self.assertEqual(self.ledger.get(self.key)['state'], 'publication_uncertain')

    def test_rejects_evidence_that_repeats_publication(self):
        evidence = {**self.evidence, 'publication_retry_attempted': True}
        with self.assertRaisesRegex(UploadBlocked, 'must not repeat'):
            self.ledger.close_uncertain_offline(self.key, self.job, evidence=evidence)
        self.assertEqual(self.ledger.get(self.key)['state'], 'publication_uncertain')


if __name__ == '__main__':
    unittest.main()
