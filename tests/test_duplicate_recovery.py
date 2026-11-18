import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from auto_upload import UploadBlocked, UploadLedger
from duplicate_recovery import resolve_duplicate, resolution_listing, commit_resolution
from torrent_metainfo import bencode, create_variant, _extract_torrent_info_hash
from torrent_store import MappingStore, infohash
from upload_recovery import verified_source


class RecoveryFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = MappingStore(self.root / 'mapping.json'); self.store.initialize()
        self.ledger = UploadLedger(self.store, self.root / 'artifacts')
        self.source = bencode({b'announce':b'https://bearbit.org/announce.php?passkey=private', b'info':{
            b'name':b'track.mp3', b'length':1024, b'piece length':1024, b'pieces':b'a'*20, b'private':1}})
        digest = _extract_torrent_info_hash(self.source)
        self.live = {'node':'test','hash':digest,'name':'track.mp3','progress':1,'amount_left':0,
                     'size_bytes':1024,'state':'uploading','save_path':'/data/test'}
        self.files = [{'path':'track.mp3','size_bytes':1024,'progress':1}]
        self.schema = {'fields':{'c41type':{'options':['รวม','Full Album']},
                                'c41sub':{'options':['MP3','Lossless']}},'acknowledgements':[]}
        metadata = {'site':'BEARBIT','infohash':digest,'zone':'normal','zone_verified':True,
            'category':'เพลง','title':'Albums Singles Vol 1974','description':'รวมเพลง',
            'details_url':'https://bearbit.org/details.php?id=1'}
        rule = {'enable':True,'source_zone':'normal','source_category':'เพลง',
                'target_site':'DEDBIT','target_category':'41','fields':{}}
        self.job = self.ledger.prepare(self.source,self.live,self.files,metadata,rule,
            'https://tracker.cab/announce.php',readiness={'ready':False},form_schema=self.schema)
        self.key = self.job['id']
        self.canonical = create_variant(self.source,'DEDBIT','normal','41','https://tracker.cab/announce.php')
        self.digest = _extract_torrent_info_hash(self.canonical)
        self.target = {**self.live,'hash':self.digest,'state':'stalledUP'}
        self.listing = {'torrent_id':'123','details_url':'https://www.dedbit.com/details.php?id=123'}
        self.store.update(lambda d:d['upload_jobs'][self.key].update(duplicate_check={
            'status':'duplicate','complete':True,'listing':self.listing}))
        self.job = self.ledger.get(self.key)
        self.client = SimpleNamespace(name='test',inventory=Mock(side_effect=lambda h,**k:
            copy.deepcopy(self.target if h==self.digest else self.live)), files=Mock(return_value=self.files),
            export_torrent=Mock(return_value=self.source), add_paused=Mock(), recheck=Mock(),resume=Mock())
        self.adapter = SimpleNamespace(site='DEDBIT', enabled_paths={'/browse.php'},
            download_listing=AsyncMock(return_value=self.canonical), submit=AsyncMock())


class DuplicateRecoveryTests(RecoveryFixture, unittest.IsolatedAsyncioTestCase):
    async def resolve(self):
        return await resolve_duplicate(self.ledger,self.key,self.adapter,self.client)

    async def test_attaches_existing_seeding_target_without_publication_or_client_mutation(self):
        result = await self.resolve()
        self.assertEqual(result['state'],'seeding')
        self.assertEqual(result['target_hash'],self.digest)
        self.assertEqual(result['duplicate_resolution']['method'],'existing_destination_torrent')
        self.assertNotIn('submission_started_at',result)
        self.adapter.submit.assert_not_awaited()
        for method in ('add_paused','recheck','resume'):
            getattr(self.client,method).assert_not_called()
        data = self.store.read()
        self.assertEqual(data['site_records']['DEDBIT:123']['infohash'],self.digest)
        row = next(r for r in data['torrents'] if r['client_info']['hash']==self.digest)
        self.assertEqual(row['variant_info']['role'],'existing_destination_variant')
        self.assertTrue(row['node_locations'][0]['inventory_verified'])

    async def test_idempotent_and_preserves_existing_obligations_holds_and_other_locations(self):
        await self.resolve()
        def preserve(d):
            d['site_records']['DEDBIT:123'].update(manual_hold=True)
            d['site_records']['DEDBIT:123']['obligations']['hr']={'status':'required'}
            d['torrents'][0]['node_locations'].append({'seedbox_host':'other','save_path':'/elsewhere'})
        self.store.update(preserve)
        before = self.store.read()
        await self.resolve()
        self.assertEqual(self.store.read(),before)

    async def test_recovers_missing_source_pointer_from_verified_export(self):
        self.store.update(lambda d:d['upload_jobs'][self.key].update(source_artifact='/missing/old/source.torrent'))
        result = await self.resolve()
        self.client.export_torrent.assert_called_once_with(self.job['source_hash'])
        self.assertTrue(Path(result['source_artifact']).is_relative_to(self.root))
        self.assertEqual(Path(result['source_artifact']).read_bytes(),self.source)

    async def test_existing_uppercase_hash_is_reused_without_duplicating_records(self):
        def existing(d):
            d['torrents'].append({'client_info':{'hash':self.digest.upper(),'name':'existing'},
                'site_listings':[{'site_key':'DEDBIT','torrent_id':'123','verified':False}],
                'node_locations':[{'seedbox_host':'other','save_path':'/other'}]})
            d['site_records']['DEDBIT:123']={'site':'DEDBIT','torrent_id':'123','infohash':None,
                'legacy_state':{},'manual_hold':True,'obligations':{'hr':{'status':'required'}}}
        self.store.update(existing)
        await self.resolve()
        before=self.store.read()
        await self.resolve()
        after=self.store.read()
        self.assertEqual(after,before)
        rows=[r for r in after['torrents'] if infohash(r['client_info']['hash'])==self.digest]
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['client_info']['hash'],self.digest.upper())
        self.assertTrue(rows[0]['site_listings'][0]['verified'])
        self.assertEqual(len(rows[0]['node_locations']),2)
        self.assertTrue(after['site_records']['DEDBIT:123']['manual_hold'])
        self.assertEqual(after['site_records']['DEDBIT:123']['obligations']['hr']['status'],'required')

    async def test_no_write_when_target_missing_paused_incomplete_wrong_path_or_node(self):
        for target in [None,{**self.target,'state':'pausedUP'}, {**self.target,'progress':0.5},
                       {**self.target,'save_path':'/different'}, {**self.target,'node':'other'}]:
            with self.subTest(target=target):
                self.client.inventory.side_effect=lambda h,**k: target if h==self.digest else self.live
                before=self.store.path.read_bytes()
                with self.assertRaises((UploadBlocked,ValueError)):await self.resolve()
                self.assertEqual(self.store.path.read_bytes(),before)

    async def test_renamed_layout_and_wrong_piece_stream_are_rejected(self):
        for content in [self.canonical.replace(b'track.mp3',b'other.mp3'),
                        self.canonical.replace(b'a'*20,b'b'*20)]:
            self.adapter.download_listing.return_value=content
            before=self.store.path.read_bytes()
            with self.assertRaises(UploadBlocked):await self.resolve()
            self.assertEqual(self.store.path.read_bytes(),before)

    async def test_stale_job_during_download_is_rejected(self):
        async def changed(url):
            self.store.update(lambda d:d['upload_jobs'][self.key]['metadata'].update(title='Changed'))
            return self.canonical
        self.adapter.download_listing.side_effect=changed
        with self.assertRaisesRegex(UploadBlocked,'changed'):await self.resolve()
        self.assertNotIn('target_hash',self.ledger.get(self.key))

    async def test_existing_conflicting_site_hash_is_not_overwritten(self):
        def conflict(d):
            d['site_records']['DEDBIT:123']={'site':'DEDBIT','torrent_id':'123','infohash':'e'*40,
                'legacy_state':{},'obligations':{'hr':{'status':'required'}}}
        self.store.update(conflict)
        before=self.store.path.read_bytes()
        with self.assertRaisesRegex(UploadBlocked,'another hash'):await self.resolve()
        self.assertEqual(self.store.path.read_bytes(),before)

    async def test_possible_duplicate_and_uncertain_submission_are_not_implicitly_resolved(self):
        for changes in [{'duplicate_check':{'status':'possible_duplicate','complete':False}},
                        {'state':'publication_uncertain'}]:
            candidate={**self.job,**changes}
            with self.assertRaises(UploadBlocked):resolution_listing(candidate)
        self.assertEqual(resolution_listing(self.job,self.listing['details_url']),self.listing)
        with self.assertRaises(UploadBlocked):resolution_listing(self.job,'https://attacker.example/details.php?id=123')

    async def test_source_artifact_tampering_never_falls_back_to_export(self):
        Path(self.job['source_artifact']).write_bytes(self.source.replace(b'a'*20,b'b'*20))
        with self.assertRaises(UploadBlocked):verified_source(self.job,self.client)
        self.client.export_torrent.assert_not_called()

    async def test_non_private_or_wrong_tracker_canonical_is_rejected(self):
        for content in [self.canonical.replace(b'7:privatei1e',b'7:privatei0e'),
                        create_variant(self.source,'DEDBIT','normal','41','https://tracker.cab/announce.php').replace(b'tracker.cab',b'unknown.cab')]:
            with self.assertRaises(UploadBlocked):
                commit_resolution(self.ledger,self.job,self.listing,self.source,content,self.target,self.files)


if __name__=='__main__':unittest.main()
