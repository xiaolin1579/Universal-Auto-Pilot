import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import main as app
from torrent_store import MappingStore

A = 'a' * 40
B = 'b' * 40
PROOF = ['exact_source_candidate_file_manifest', 'live_qbittorrent_complete_inventory',
         'candidate_tracker_api_all_present']


class ZoneFixture:
    def setUp(self):
        self.normal = {'site_group': 'BEARBIT', 'site_key': 'BEARBIT', 'zone_id': 'normal',
            'zone_name': 'Normal', 'host': 'bearbit.org', 'url_path': '/viewno18sbx.php', 'enabled': True}
        self.special = {**self.normal, 'zone_id': 'special', 'url_path': '/viewbrsb.php'}
        self.dedbit = {'site_group': 'DEDBIT', 'site_key': 'DEDBIT', 'zone_id': 'normal',
            'zone_name': 'Normal', 'host': 'www.dedbit.com', 'url_path': '/browse.php', 'enabled': True}
        self.registry = [self.normal, self.special, self.dedbit]
        self.config = {'SITE': [{'name': 'BEARBIT', 'enable': True}, {'name': 'DEDBIT', 'enable': True}]}
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(app, 'CFG', self.config))
        self.stack.enter_context(patch.object(app, '_build_mapping_site_zone_registry', side_effect=lambda: copy.deepcopy(self.registry)))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def listing(self, digest=A, meta=None, tid='12'):
        meta = self.normal if meta is None else meta
        return {**meta, 'torrent_id': tid, 'infohash': digest,
            'details_url': 'https://' + meta['host'] + '/details.php?id=' + tid,
            'verified': True, 'verification_status': 'live_inventory_exact_manifest_tracker_verified',
            'verified_by': list(PROOF), 'verified_at': '2026-11-18T01:00:00+00:00'}

    def record(self, digest=A, listings=None):
        return {'client_info': {'hash': digest}, 'source_site': 'Unknown', 'source_site_verified': False,
                'source_zone_id': 'unknown', 'source_zone_verified': False,
                'site_listings': copy.deepcopy(listings if listings is not None else [self.listing(digest)])}


class CrossSeedZoneTests(ZoneFixture, unittest.TestCase):
    def test_unique_listing_reconciles_unknown_site_zone_and_identity(self):
        row = self.record(); data = {'torrents': [row]}
        self.assertEqual(app._reconcile_cross_seed_source_zones(data), 1)
        self.assertEqual((row['source_site'], row['source_zone_id'], row['torrent_id']), ('BEARBIT', 'normal', '12'))
        self.assertTrue(row['source_site_verified']); self.assertTrue(row['source_zone_verified'])
        proof = row['source_zone_evidence']
        self.assertEqual(proof['verification_method'], 'cross_seed_verified_listing')
        self.assertIsNotNone(app._validate_mapping_source_zone_evidence(proof, 'BEARBIT', '12', row['details_url'], source_hash=A))
        snapshot = copy.deepcopy(data)
        self.assertEqual(app._reconcile_cross_seed_source_zones(data), 0); self.assertEqual(data, snapshot)

    def test_foreign_hash_never_changes_the_source_but_can_reconcile_its_own_record(self):
        source = self.record(A, [self.listing(B)]); candidate = self.record(B, [])
        data = {'torrents': [source, candidate]}
        self.assertEqual(app._reconcile_cross_seed_source_zones(data), 1)
        self.assertFalse(source['source_site_verified']); self.assertFalse(source['source_zone_verified'])
        self.assertTrue(candidate['source_zone_verified']); self.assertEqual(candidate['source_zone_evidence']['infohash'], B.upper())

    def test_multiple_verified_zones_or_sites_remain_unknown(self):
        for other in (self.special, self.dedbit):
            with self.subTest(meta=other):
                row = self.record(listings=[self.listing(), self.listing(meta=other)])
                self.assertEqual(app._reconcile_cross_seed_source_zones({'torrents': [row]}), 0)
                self.assertFalse(row['source_zone_verified'])

    def test_multiple_listing_ids_do_not_guess_the_original_listing(self):
        row = self.record(listings=[self.listing(), self.listing(tid='13')])
        self.assertEqual(app._reconcile_cross_seed_source_zones({'torrents': [row]}), 0)
        self.assertFalse(row['source_site_verified'])

    def test_verified_primary_source_is_preserved_when_another_site_matches(self):
        row = self.record(listings=[self.listing()]); row.update(source_site='DEDBIT', source_site_verified=True)
        before = copy.deepcopy(row)
        self.assertEqual(app._reconcile_cross_seed_source_zones({'torrents': [row]}), 0)
        self.assertEqual(row, before)

    def test_verified_source_selects_only_its_own_site_from_multiple_associations(self):
        row = self.record(listings=[self.listing(), self.listing(meta=self.dedbit)])
        row.update(source_site='DEDBIT', source_site_verified=True)
        self.assertEqual(app._reconcile_cross_seed_source_zones({'torrents': [row]}), 1)
        self.assertEqual(row['source_site'], 'DEDBIT'); self.assertEqual(row['source_zone_id'], 'normal')

    def test_existing_confirmed_add_proof_is_preserved_when_cross_seed_conflicts(self):
        listing = self.listing()
        old = {**listing, 'verification_method': 'successful_add_from_verified_zone',
               'verification_status': 'successful_add_from_verified_zone',
               'verified_by': ['configured_enabled_zone_registry', 'matching_search_result_details_url', 'successful_add_response']}
        row = self.record(listings=[self.listing(meta=self.special)])
        row.update(source_site='BEARBIT', source_site_verified=True, source_zone_id='normal',
            source_zone_verified=True, source_zone_evidence=old, torrent_id='12', details_url=listing['details_url'])
        before = copy.deepcopy(row)
        self.assertIsNotNone(app._validate_mapping_source_zone_evidence(old, 'BEARBIT', '12', listing['details_url']))
        self.assertEqual(app._reconcile_cross_seed_source_zones({'torrents': [row]}), 0)
        self.assertEqual(row, before)

    def test_incomplete_or_unsupported_proof_is_not_promoted(self):
        mutations = [{'verified': False}, {'verification_status': 'filelist_manifest_verified_recheck_required'},
            {'verified_by': PROOF[:-1]}, {'verified_by': [dict()]}, {'infohash': 'bad'},
            {'verified_at': 'invalid'}, {'verified_at': '2026-11-18T01:00:00'}, {'host': 'elsewhere.example'},
            {'torrent_id': '13'}, {'zone_id': 'special'}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                row = self.record(listings=[{**self.listing(), **mutation}])
                self.assertEqual(app._reconcile_cross_seed_source_zones({'torrents': [row]}), 0)
                self.assertFalse(row['source_zone_verified'])

    def test_disabled_site_or_zone_is_not_reenabled_by_old_proof(self):
        row = self.record(); self.config['SITE'][0]['enable'] = False
        self.assertEqual(app._reconcile_cross_seed_source_zones({'torrents': [row]}), 0)
        self.config['SITE'][0]['enable'] = True; self.registry[0]['enabled'] = False
        self.assertEqual(app._reconcile_cross_seed_source_zones({'torrents': [row]}), 0)

    def test_proof_cannot_be_reused_for_another_hash_or_without_hash_binding(self):
        row = self.record(); app._reconcile_cross_seed_source_zones({'torrents': [row]})
        for digest in (None, B):
            self.assertIsNone(app._validate_mapping_source_zone_evidence(row['source_zone_evidence'],
                'BEARBIT', '12', row['details_url'], source_hash=digest))

    def test_signed_ambiguous_or_invalid_listing_urls_are_not_persisted_as_proof(self):
        for url in ('https://bearbit.org/details.php?id=12&passkey=private',
            'https://bearbit.org/details.php?id=12&token=', 'https://bearbit.org/details.php?id=12&id=13',
            'https://private@bearbit.org/details.php?id=12', 'https://bearbit.org:bad/details.php?id=12',
            'http://bearbit.org/details.php?id=12', 'https://bearbit.org/details.php?id=12#private'):
            with self.subTest(url=url):
                row = self.record(listings=[{**self.listing(), 'details_url': url}])
                self.assertEqual(app._reconcile_cross_seed_source_zones({'torrents': [row]}), 0)


class CrossSeedZonePersistenceTests(ZoneFixture, unittest.IsolatedAsyncioTestCase):
    async def test_already_stored_listings_can_backfill_zone_without_new_listing(self):
        data = {'torrents': [self.record()]}
        async def load(): return copy.deepcopy(data)
        async def save(incoming): data.clear(); data.update(copy.deepcopy(incoming)); return True
        raw = {'target_zone_meta': self.normal, 'infohash': A, 'torrent_id': '12',
               'details_url': self.listing()['details_url']}
        with patch.object(app, 'load_mapping', side_effect=load), patch.object(app, 'save_mapping', new=AsyncMock(side_effect=save)) as writer:
            self.assertTrue(await app.record_cross_seed_site_listings.__wrapped__({A: [raw]}))
        writer.assert_awaited_once(); self.assertTrue(data['torrents'][0]['source_zone_verified'])

    async def test_regular_cross_seed_linking_pass_backfills_persisted_evidence(self):
        data = {'torrents': [self.record()]}
        with patch.object(app, 'load_mapping', new=AsyncMock(return_value=data)), \
             patch.object(app, 'process_cross_seed_linking', side_effect=lambda value: value), \
             patch.object(app, 'save_mapping', new=AsyncMock(return_value=True)) as writer:
            await app._persist_cross_seed_linking.__wrapped__()
        self.assertTrue(writer.call_args.args[0]['torrents'][0]['source_zone_verified'])


class CrossSeedListingBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = MappingStore(Path(self.tmp.name) / 'mapping.json'); self.store.initialize()

    def add(self, listing):
        self.store.update(lambda d: d['torrents'].append({'client_info': {'hash': A}, 'site_listings': [listing]}))

    def test_cross_seed_listing_binds_its_own_hash_and_keeps_obligations_unknown(self):
        self.add({'site_key': 'BEARBIT', 'torrent_id': '12', 'details_url': 'https://bearbit.org/details.php?id=12',
                  'verified': True, 'infohash': B})
        self.assertEqual(self.store.reconcile_verified_listings()['created'], 1)
        record = self.store.read()['site_records']['BEARBIT:12']
        self.assertEqual(record['infohash'], B)
        self.assertTrue(all(o['status'] == 'unknown' for o in record['obligations'].values()))

    def test_legacy_listing_without_hash_still_uses_its_own_torrent_record(self):
        self.add({'site_key': 'BEARBIT', 'torrent_id': '12', 'details_url': 'https://bearbit.org/details.php?id=12', 'verified': True})
        self.store.reconcile_verified_listings()
        self.assertEqual(self.store.read()['site_records']['BEARBIT:12']['infohash'], A)

    def test_explicit_bad_listing_hash_never_falls_back_to_the_source_hash(self):
        self.add({'site_key': 'BEARBIT', 'torrent_id': '12', 'details_url': 'https://bearbit.org/details.php?id=12',
                  'verified': True, 'infohash': 'invalid'})
        self.assertEqual(self.store.reconcile_verified_listings()['created'], 0)
        self.assertEqual(self.store.read()['site_records'], {})
