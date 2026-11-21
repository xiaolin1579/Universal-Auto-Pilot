from copy import deepcopy
import tempfile
import unittest
from pathlib import Path

from auto_upload import UploadBlocked, UploadLedger, UploadWorker
from torrent_metainfo import bencode, create_variant, _extract_torrent_info_hash
from torrent_store import MappingStore


class ActiveClient:
    name = 'seedbox'

    def __init__(self, source_hash, target_hash, save_path, files, target_state='uploading'):
        self.source_hash = source_hash
        self.target_hash = target_hash
        self.save_path = save_path
        self.files_payload = files
        self.target_state = target_state
        self.calls = []

    def inventory(self, value, missing_ok=False):
        row = {'hash': value.upper(), 'progress': 1, 'amount_left': 0, 'size_bytes': 4,
               'save_path': self.save_path, 'state': 'uploading'}
        if value == self.source_hash:
            row['state'] = 'stalledUP'
            return row
        if value == self.target_hash:
            row['state'] = self.target_state
            return row
        if missing_ok:
            return None
        raise KeyError(value)

    def files(self, value):
        if value in (self.source_hash, self.target_hash):
            return deepcopy(self.files_payload)
        return []

    def add_paused(self, *args, **kwargs):
        self.calls.append('add_paused')
        return True

    def resume(self, *args, **kwargs):
        self.calls.append('resume')
        return True

    def recheck(self, *args, **kwargs):
        self.calls.append('recheck')
        return True


class CanonicalAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = MappingStore(root / 'mapping.json')
        self.store.initialize()
        self.ledger = UploadLedger(self.store, root / 'artifacts')
        self.source = bencode({b'announce': b'https://bearbit.org/announce.php', b'info': {
            b'name': b'payload.bin', b'length': 4, b'piece length': 4,
            b'pieces': b'a' * 20, b'private': 1}})
        self.source_hash = _extract_torrent_info_hash(self.source)
        self.inventory = {'node': 'seedbox', 'hash': self.source_hash, 'name': 'payload.bin',
            'progress': 1, 'amount_left': 0, 'size_bytes': 4, 'state': 'pausedUP', 'save_path': '/downloads'}
        self.files = [{'path': 'payload.bin', 'size_bytes': 4, 'progress': 1}]
        metadata = {'site': 'BEARBIT', 'infohash': self.source_hash, 'zone': 'special',
            'zone_verified': True, 'category': 'test', 'title': 'Test payload',
            'description': 'Test description', 'details_url': 'https://bearbit.org/details.php?id=1'}
        rule = {'enable': True, 'source_zone': 'special', 'source_category': 'test',
                'target_site': 'BITSUSE', 'target_category': '180'}
        self.announce = 'https://www.bitsuse.com/announce.php'
        self.job = self.ledger.prepare(self.source, self.inventory, self.files, metadata, rule,
                                       self.announce, readiness={'ready': True},
                                       form_schema={'fields': {}, 'acknowledgements': []})
        self.key = self.job['id']
        self.canonical = create_variant(self.source, 'BITSUSE', 'special', '180', self.announce)
        self.target = _extract_torrent_info_hash(self.canonical)
        self.listing = {'torrent_id': '687125',
                        'details_url': 'https://www.bitsuse.com/bs_details.php?id=687125'}
        self.ledger.claim_submission(self.key, self.job, {'status': 'clear', 'complete': True})
        self.ledger.record_publication(self.key, self.listing)
        self.store.update(lambda data: data['torrents'].append({
            'client_info': {'hash': self.target.upper(), 'name': 'Test payload'},
            'content_group_id': None,
            'source_site': 'BITSUSE', 'source_site_verified': True,
            'source_zone_id': 'special', 'source_zone_verified': True,
            'torrent_id': '687125', 'details_url': self.listing['details_url'],
            'variant_info': {'role': 'unclassified', 'original_infohash': None,
                             'target_site': None, 'target_zone_id': None, 'verified': False},
            'site_listings': [{'site_key': 'BITSUSE', 'site_group': 'DEDBIT', 'torrent_id': '687125',
                               'infohash': self.target.upper(),
                               'details_url': self.listing['details_url'], 'verified': True}],
            'node_locations': []}))
        self.store.update(lambda data: data['site_records'].update({
            'BITSUSE:687125': {'site': 'BITSUSE', 'torrent_id': '687125', 'infohash': self.target.upper(),
                'legacy_state': {'hash': self.target.upper(), 'url': self.listing['details_url']},
                'obligations': {'hr': {'status': 'required'}, 'quest_seed': {'status': 'clear'}},
                'binding_evidence': {'method': 'verified_mapping_listing'},
                'manual_hold': True}}))

    def test_adopts_exact_verified_mapping_without_duplicate_or_evidence_loss(self):
        result = self.ledger.accept_canonical(self.key, self.canonical)
        self.assertEqual(result['state'], 'verified')
        self.assertEqual(result['target_hash'], self.target)
        self.assertEqual(result['canonical_verification']['method'], 'adopted_existing_verified_mapping')
        data = self.store.read()
        group = data['content_groups'][self.job['content_group_id']]
        self.assertEqual(group['hashes'], [self.source_hash, self.target])
        rows = [r for r in data['torrents']
                if str(r['client_info']['hash']).lower() == self.target]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['content_group_id'], self.job['content_group_id'])
        self.assertEqual(rows[0]['variant_info']['role'], 'uploaded_variant')
        self.assertEqual(rows[0]['variant_info']['original_infohash'], self.source_hash)
        record = data['site_records']['BITSUSE:687125']
        self.assertEqual(record['manual_hold'], True)
        self.assertEqual(record['obligations'], {'hr': {'status': 'required'},
                                                  'quest_seed': {'status': 'clear'}})
        self.assertEqual(Path(result['canonical_artifact']).read_bytes(), self.canonical)

    def test_rejects_same_hash_with_unverified_listing(self):
        def unverify(data):
            data['torrents'][0]['site_listings'][0]['verified'] = False
        self.store.update(unverify)
        with self.assertRaisesRegex(UploadBlocked, 'already belongs'):
            self.ledger.accept_canonical(self.key, self.canonical)
        self.assertEqual(self.ledger.get(self.key)['state'], 'published')

    def test_rejects_same_hash_in_a_different_content_group(self):
        def foreign_group(data):
            data['torrents'][0]['content_group_id'] = '0' * 64
        self.store.update(foreign_group)
        with self.assertRaisesRegex(UploadBlocked, 'already belongs'):
            self.ledger.accept_canonical(self.key, self.canonical)

    def test_rejects_listing_bound_to_another_active_job(self):
        other = deepcopy(self.job)
        other['id'] = 'b' * 64
        other['state'] = 'published'
        other['listing'] = deepcopy(self.listing)
        self.assertNotEqual(other['id'], self.key)
        self.store.update(lambda data: data['upload_jobs'].update({other['id']: other}))
        with self.assertRaisesRegex(UploadBlocked, 'another upload job'):
            self.ledger.accept_canonical(self.key, self.canonical)

    def test_rejects_exact_hash_without_provenance(self):
        def drop_evidence(data):
            del data['site_records']['BITSUSE:687125']['binding_evidence']
        self.store.update(drop_evidence)
        with self.assertRaisesRegex(UploadBlocked, 'already belongs'):
            self.ledger.accept_canonical(self.key, self.canonical)

    def test_adopts_active_target_without_recheck_or_client_mutation(self):
        self.ledger.accept_canonical(self.key, self.canonical)
        client = ActiveClient(self.source_hash, self.target, '/downloads', self.files)
        result = UploadWorker(self.ledger).adopt_active_target(self.key, client)
        self.assertEqual(result['state'], 'seeding')
        self.assertEqual(result['seed_recovery']['method'], 'existing_active_target')
        self.assertEqual(client.calls, [])
        data = self.store.read()
        row = next(r for r in data['torrents'] if str(r['client_info']['hash']).lower() == self.target)
        self.assertTrue(any(loc.get('active_seed_verified') for loc in row['node_locations']))
        self.assertEqual(data['site_records']['BITSUSE:687125']['obligations'],
                         {'hr': {'status': 'required'}, 'quest_seed': {'status': 'clear'}})

    def test_rejects_active_adoption_when_target_is_not_seeding(self):
        self.ledger.accept_canonical(self.key, self.canonical)
        client = ActiveClient(self.source_hash, self.target, '/downloads', self.files, target_state='pausedUP')
        with self.assertRaisesRegex(UploadBlocked, 'not actively seeding'):
            UploadWorker(self.ledger).adopt_active_target(self.key, client)
        self.assertEqual(self.ledger.get(self.key)['state'], 'verified')
        self.assertEqual(client.calls, [])


if __name__ == '__main__':
    unittest.main()
