import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from auto_upload import UploadBlocked, UploadLedger
from auto_upload_cli import execute, parser
from torrent_metainfo import bencode, _extract_torrent_info_hash
from torrent_store import MappingStore
from upload_classifier import classify_job


class ClassificationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = MappingStore(self.root / 'mapping.json')
        self.store.initialize()
        self.ledger = UploadLedger(self.store, self.root / 'artifacts')
        self.source = bencode({b'announce': b'https://bearbit.org/announce.php?passkey=private',
            b'info': {b'name': b'track.mp3', b'length': 1024, b'piece length': 1024,
                      b'pieces': b'a' * 20, b'private': 1}})
        digest = _extract_torrent_info_hash(self.source)
        self.inventory = {'node': 'test', 'hash': digest, 'name': 'track.mp3', 'progress': 1,
            'amount_left': 0, 'size_bytes': 1024, 'state': 'pausedUP', 'save_path': '/data/test'}
        self.files = [{'path': 'track.mp3', 'size_bytes': 1024, 'progress': 1}]
        metadata = {'site': 'BEARBIT', 'infohash': digest, 'zone': 'normal', 'zone_verified': True,
            'category': 'เพลง', 'title': 'Albums Singles Vol 1974', 'description': 'รวมเพลง',
            'details_url': 'https://bearbit.org/details.php?id=1'}
        rule = {'enable': True, 'source_zone': 'normal', 'source_category': 'เพลง',
                'target_site': 'DEDBIT', 'target_category': '41', 'fields': {}}
        schema = {'fields': {'c41sub': {'options': ['MP3', 'Lossless']},
                            'c41type': {'options': ['รวม', 'Full Album']}},
                  'acknowledgements': ['ack2']}
        self.job = self.ledger.prepare(self.source, self.inventory, self.files, metadata,
            rule, 'https://tracker.cab/announce.php',
            readiness={'ready': False, 'missing': ['c41sub', 'c41type', 'ack2']}, form_schema=schema)
        self.key = self.job['id']
        self.app = SimpleNamespace(BASE_DIR=self.root, CFG={'NODES': [{'name': 'test', 'enable': True}]},
            _mapping_store=lambda: self.store, initialize_mapping=AsyncMock(), CLIENTS=Mock())

    async def run_cli(self, *options):
        args = parser().parse_args(['classify', *options])
        return await execute(args, self.app)

    async def test_default_is_byte_for_byte_read_only_and_does_not_connect(self):
        before = self.store.path.read_bytes()
        with patch('metadata_providers.lookup_metadata') as lookup:
            report = await self.run_cli(self.key)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.app.initialize_mapping.assert_not_called()
        self.app.CLIENTS.create.assert_not_called()
        lookup.assert_not_called()
        self.assertEqual(report['manifest_source'], 'source_metainfo')
        self.assertFalse(report['applied'])
        self.assertFalse(report['saved'])
        self.assertNotIn('private', str(report))
        self.assertEqual({s['field']: s['value'] for s in report['suggestions']},
                         {'c41sub': 'MP3', 'c41type': 'รวม'})

    async def test_save_adds_only_report_and_preserves_readiness_and_fields(self):
        before = self.ledger.get(self.key)
        result = await self.run_cli(self.key, '--save')
        after = self.ledger.get(self.key)
        saved = after.pop('classification')
        self.assertEqual(after, before)
        self.assertTrue(result['saved'])
        self.assertFalse(saved['applied'])
        self.assertFalse(self.ledger.review(self.key)['classification']['stale'])
        self.app.CLIENTS.create.assert_not_called()

    async def test_stale_report_is_rejected_after_metadata_change(self):
        report = classify_job(self.job)
        self.store.update(lambda d: d['upload_jobs'][self.key]['metadata'].update(title='Changed'))
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(UploadBlocked, 'changed'):
            self.ledger.save_classification(self.key, report)
        self.assertEqual(self.store.path.read_bytes(), before)

    async def test_review_marks_old_suggestions_stale(self):
        await self.run_cli(self.key, '--save')
        self.store.update(lambda d: d['upload_jobs'][self.key]['rule']['fields'].update(c41sub='Lossless'))
        self.assertTrue(self.ledger.review(self.key)['classification']['stale'])

    async def test_submitted_job_cannot_save_suggestions(self):
        self.ledger.transition(self.key, {'draft'}, 'publication_uncertain')
        before = self.store.path.read_bytes()
        with self.assertRaises(UploadBlocked):
            await self.run_cli(self.key, '--save')
        self.assertEqual(self.store.path.read_bytes(), before)

    async def test_tampered_manifest_cannot_be_used_as_evidence(self):
        content = self.source.replace(b'track.mp3', b'other.mp3')
        Path(self.job['source_artifact']).write_bytes(content)
        report = await self.run_cli(self.key)
        self.assertEqual(report['manifest_source'], 'unavailable')
        self.assertIn('source_artifact_unavailable', report['review_flags'])
        self.assertNotIn('c41sub', {s['field'] for s in report['suggestions']})

    async def test_explicit_client_uses_verified_read_only_export_without_artifact_writes(self):
        client = Mock()
        client.inventory.return_value = self.inventory
        client.files.return_value = self.files
        client.request.return_value.content = self.source
        self.app.CLIENTS.create.return_value.login.return_value = True
        before = self.store.path.read_bytes()
        Path(self.job['source_artifact']).unlink()
        with patch('classification_runner.client_for', return_value=client):
            report = await self.run_cli(self.key, '--client')
        self.assertEqual(report['manifest_source'], 'verified_client_export')
        client.request.assert_called_once_with('GET', 'torrents/export', params={'hash': self.job['source_hash']})
        self.assertEqual([call[0] for call in client.method_calls], ['inventory', 'request', 'files'])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertFalse(Path(self.job['source_artifact']).exists())
        self.app.CLIENTS.close.assert_called_once()

    async def test_origin_label_in_description_skips_manifest_and_external_lookup(self):
        job = copy.deepcopy(self.job)
        job['source_zone'] = 'special'
        job['metadata']['description'] = 'Uncensored Leaked'
        with patch('classification_runner.ledger_for') as factory, \
             patch('classification_runner._manifest') as manifest, \
             patch('metadata_providers.lookup_metadata') as lookup:
            factory.return_value.store.read.return_value = {'upload_jobs': {self.key: job}}
            report = await self.run_cli(self.key, '--client', '--musicbrainz', '--web')
        self.assertIn('origin_requires_review', report['review_flags'])
        self.assertEqual(report['suggestions'], [])
        manifest.assert_not_called()
        lookup.assert_not_called()
        self.app.CLIENTS.create.assert_not_called()

    async def test_choose_either_job_or_all_drafts(self):
        for options in [[], [self.key, '--all-drafts']]:
            with self.assertRaises(UploadBlocked):
                await self.run_cli(*options)
        reports = await self.run_cli('--all-drafts')
        self.assertEqual([r['job_id'] for r in reports], [self.key])


if __name__ == '__main__':
    unittest.main()
