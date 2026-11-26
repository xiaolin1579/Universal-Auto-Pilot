import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from auto_upload import (UploadBlocked, UploadLedger, create_job_variant, repacked_source,
                         publication_signature, UploadWorker)
from onlyone_repack import OnlyOneMaker, rebuild_onlyone
from torrent_metainfo import bencode, create_variant, payload_signature, _extract_torrent_info_hash
from torrent_store import MappingStore


def torrent(piece=1024**2, name=b'payload.bin', length=4*1024**2, seed=b'a'):
    pieces = ((length + piece - 1) // piece) * 20
    return bencode({b'announce': b'https://bearbit.org/announce.php', b'info': {
        b'name': name, b'length': length, b'piece length': piece,
        b'pieces': seed * pieces, b'private': 1}})


class RepackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = MappingStore(root / 'mapping.json'); self.store.initialize()
        self.ledger = UploadLedger(self.store, root / 'artifacts')
        self.source = torrent(); self.candidate = torrent(piece=2*1024**2, seed=b'b')
        source_hash = _extract_torrent_info_hash(self.source)
        self.inventory = {'node':'OnlyOne', 'hash':source_hash,'name':'payload.bin',
            'progress':1,'amount_left':0,'size_bytes':4*1024**2,'state':'pausedUP','save_path':'/data/users/test/downloads'}
        files = [{'path':'payload.bin','size_bytes':4*1024**2,'progress':1}]
        metadata = {'site':'BEARBIT','infohash':source_hash,'zone':'special','zone_verified':True,
            'category':'test','title':'test','description':'test','details_url':'https://bearbit.org/details.php?id=1'}
        rule = {'enable':True,'source_zone':'special','source_category':'test','target_site':'BITSUSE','target_category':'180'}
        announce = 'https://www.bitsuse.com/announce.php'
        limit = len(create_variant(self.source, 'BITSUSE','special','180',announce)) - 10
        self.spec = {'announce':announce,'max_file_size':limit,'fields':{},'acknowledgements':[]}
        self.job = self.ledger.prepare(self.source, self.inventory, files, metadata, rule, announce,
                                       readiness={'ready':True},form_schema=self.spec)
        inventory = self.inventory
        class Client:
            name = 'OnlyOne'
            node = object()
            def inventory(self, target, **kwargs): return dict(inventory)
            def files(self, target): return files
        self.client = Client()
        candidate = self.candidate
        self.calls=[]
        calls=self.calls
        class Maker:
            base = "https://onlyoneseedbox.com/account"
            def __init__(self, client): pass
            def location(self, job, source): return {'root':'files','path':'payload.bin'}
            def request(self, route, body=None, binary=False):
                calls.append((route,body))
                if route=='/api/tenant/maker':
                    return {'root':'files','src':'payload.bin','out':'payload.bin.torrent','state':'done'}
                if binary: return candidate
                return {'root':'files','path':'','entries':[]}
        self.maker=Maker

    def rebuild(self):
        with patch('onlyone_repack.OnlyOneMaker',self.maker):
            return rebuild_onlyone(self.ledger,self.job['id'],self.client,self.spec)

    def test_oversized_job_reuses_matching_completed_result_without_post(self):
        result=self.rebuild()
        self.assertEqual(result['state'],'prepared')
        self.assertEqual(result['content_group_id'],payload_signature(self.source))
        self.assertNotEqual(result['prepared_hash'],self.job['prepared_hash'])
        self.assertLess(result['metainfo_preflight']['variant_bytes'],self.spec['max_file_size'])
        self.assertEqual(repacked_source(result,self.source),self.candidate)
        self.assertTrue(all(body is None for _,body in self.calls))
        self.assertEqual(self.rebuild()['prepared_hash'],result['prepared_hash'])

    def test_layout_mismatch_is_refused_without_releasing_job(self):
        bad=torrent(piece=2*1024**2,name=b'other.bin')
        original=self.maker.request
        def request(obj,route,body=None,binary=False):
            return bad if binary else original(obj,route,body,binary)
        with patch.object(self.maker,'request',request),self.assertRaisesRegex(UploadBlocked,'paths or sizes'):
            self.rebuild()
        self.assertNotIn('repack',self.ledger.get(self.job['id']))

    def test_candidate_artifact_tampering_is_refused(self):
        result=self.rebuild()
        Path(result['repack']['artifact']).write_bytes(torrent(piece=2*1024**2,seed=b'c'))
        with self.assertRaisesRegex(UploadBlocked,'artifact changed'):
            create_job_variant(result,self.source,self.spec['announce'])

    def test_original_source_stays_bound(self):
        result=self.rebuild()
        with self.assertRaisesRegex(UploadBlocked,'original source'):
            repacked_source(result,torrent(seed=b'z'))

    def test_canonical_signature_changes_only_when_new_post_is_claimed(self):
        key=self.job['id']
        self.ledger.transition(key,{'prepared'},'publication_uncertain')
        result=self.rebuild()
        self.assertEqual(result['state'],'publication_uncertain')
        self.assertEqual(publication_signature(result),self.job['content_group_id'])
        # The explicit retry gate remains outside rebuilding.
        with self.assertRaises(UploadBlocked): self.ledger.claim_submission(key,result,{})
        self.ledger.transition(key,{'publication_uncertain'},'prepared')
        result=self.ledger.get(key)
        self.ledger.claim_submission(key,result,{'status':'clear','complete':True})
        current=self.ledger.get(key)
        self.assertEqual(publication_signature(current),payload_signature(self.candidate))
        canonical=create_job_variant(current,self.source,self.spec['announce'])
        self.ledger.record_publication(key,{'torrent_id':'1','details_url':'https://www.bitsuse.com/bs_details.php?id=1'})
        verified=self.ledger.accept_canonical(key,canonical)
        self.assertEqual(verified['state'],'verified')
        self.assertEqual(verified['content_group_id'],self.job['content_group_id'])

    def test_same_layout_but_wrong_hashes_not_accepted_as_canonical(self):
        result=self.rebuild(); key=result['id']
        self.ledger.claim_submission(key,result,{})
        self.ledger.record_publication(key,{'torrent_id':'1','details_url':'https://www.bitsuse.com/bs_details.php?id=1'})
        wrong=create_variant(torrent(piece=2*1024**2,seed=b'c'),'BITSUSE','special','180',self.spec['announce'])
        with self.assertRaises(UploadBlocked): self.ledger.accept_canonical(key,wrong)

    def test_uncertain_provider_post_is_never_repeated(self):
        calls=self.calls
        def request(obj,route,body=None,binary=False):
            calls.append((route,body))
            if body is not None: raise TimeoutError('unknown POST result')
            if route=='/api/tenant/maker': return {'state':'idle'}
            return {'entries':[]}
        with patch.object(self.maker,'request',request):
            with self.assertRaises(TimeoutError): self.rebuild()
            self.assertIn('repack_request',self.ledger.get(self.job['id']))
            with self.assertRaisesRegex(UploadBlocked,'unresolved'): self.rebuild()
        self.assertEqual(sum(body is not None for _,body in calls),1)

    def test_busy_account_does_not_start_another_job(self):
        def request(obj,route,body=None,binary=False):
            self.calls.append((route,body));return {'state':'running','root':'files','src':'another.bin'}
        with patch.object(self.maker,'request',request),self.assertRaisesRegex(UploadBlocked,'running'):
            self.rebuild()
        self.assertTrue(all(body is None for _,body in self.calls))

    def test_small_source_never_calls_provider(self):
        with self.assertRaisesRegex(UploadBlocked,'only allowed'):
            with patch('onlyone_repack.OnlyOneMaker',side_effect=AssertionError('must not connect')):
                rebuild_onlyone(self.ledger,self.job['id'],self.client,{**self.spec,'max_file_size':1000000})

    def test_same_piece_size_is_not_a_rebuild(self):
        same=torrent(seed=b'b')
        evidence={'provider':'onlyone','source_hash':self.job['source_hash'],
          'source_signature':self.job['content_group_id'],'candidate_hash':_extract_torrent_info_hash(same),
          'candidate_signature':payload_signature(same)}
        with self.assertRaisesRegex(UploadBlocked,'increase'):
            repacked_source({**self.job,'repack':evidence},self.source,candidate=same)

    def test_rebuilt_target_must_pass_recheck_before_resume(self):
        result=self.rebuild();key=result['id']
        self.ledger.claim_submission(key,result,{})
        self.ledger.record_publication(key,{'torrent_id':'1','details_url':'https://www.bitsuse.com/bs_details.php?id=1'})
        canonical=create_job_variant(result,self.source,self.spec['announce'])
        self.ledger.accept_canonical(key,canonical)
        self.ledger.transition(key,{'verified'},'rechecking',recheck_observed=False)
        from unittest.mock import Mock
        client=Mock(name='client');client.name='OnlyOne'
        client.inventory.return_value={**self.inventory,'hash':_extract_torrent_info_hash(canonical)}
        client.recheck_complete.return_value=False
        state=UploadWorker(self.ledger).seed(key,client,resume=True)
        self.assertEqual(state['state'],'rechecking');client.resume.assert_not_called()

    def test_rebuilt_result_still_too_large_does_not_change_job(self):
        with patch('onlyone_repack.OnlyOneMaker',self.maker),self.assertRaises(UploadBlocked):
            rebuild_onlyone(self.ledger,self.job['id'],self.client,{**self.spec,'max_file_size':1})
        self.assertNotIn('repack',self.ledger.get(self.job['id']))

    def test_uncertain_maker_holds_source_against_cancel(self):
        def apply(data):
            data['upload_jobs'][self.job['id']]['repack_request']={
                'root':'files','path':'payload.bin','name':'uap-'+self.job['id'][:24]+'.torrent'}
        self.store.update(apply)
        with self.assertRaisesRegex(UploadBlocked,'source protection'):
            self.ledger.transition(self.job['id'],{'prepared'},'cancelled')

    def test_other_provider_cannot_use_onlyone_maker(self):
        from onlyone_repack import OnlyOneMaker
        from types import SimpleNamespace
        self.client.node=SimpleNamespace(url='https://example.com/qbittorrent',provider_sso=False)
        with self.assertRaisesRegex(UploadBlocked,'OnlyOne SSO'):
            OnlyOneMaker(self.client)

    def test_incomplete_source_does_not_connect_to_maker(self):
        with patch.object(self.client,'inventory',return_value={**self.inventory,'progress':0.5}), \
                patch('onlyone_repack.OnlyOneMaker',side_effect=AssertionError('must not connect')):
            with self.assertRaises(ValueError):
                rebuild_onlyone(self.ledger,self.job['id'],self.client,self.spec)

    def test_worker_automatically_rebuilds_only_when_oversized(self):
        import asyncio
        with patch('onlyone_repack.OnlyOneMaker',self.maker):
            job,variant=asyncio.run(UploadWorker(self.ledger).variant(self.job,self.source,self.spec,self.client))
        self.assertIn('repack',job)
        self.assertLessEqual(len(variant),self.spec['max_file_size'])

    def test_worker_keeps_small_torrent_on_original_path(self):
        import asyncio
        with patch('onlyone_repack.rebuild_onlyone',side_effect=AssertionError('must not rebuild')):
            job,variant=asyncio.run(UploadWorker(self.ledger).variant(self.job,self.source,
                {**self.spec,'max_file_size':1000000},self.client))
        self.assertNotIn('repack',job)
        self.assertEqual(_extract_torrent_info_hash(variant),self.job['prepared_hash'])

    def test_duplicate_search_and_browse_recovery_recognize_rebuilt_signature(self):
        import asyncio
        from unittest.mock import AsyncMock
        from upload_sites import BrowserSite
        result=self.rebuild()
        self.ledger.claim_submission(result['id'],result,{})
        result=self.ledger.get(result['id'])
        canonical=create_job_variant(result,self.source,self.spec['announce'])
        tab=AsyncMock();adapter=BrowserSite('BITSUSE',tab)
        details='https://www.bitsuse.com/bs_details.php?id=123'
        browse='https://www.bitsuse.com/bs_browse.php?search=payload&incldead=1'
        html='<input name="search"><table class="torrenttable"><tr><td><a href="'+details+'">payload</a></td></tr></table>'
        adapter.open=AsyncMock(side_effect=[(html,browse),('<a href="/bs_edit.php?id=123">edit</a>',details)])
        adapter.download_listing=AsyncMock(return_value=canonical)
        with patch('upload_sites.duplicate_search_keywords',return_value=['payload']), \
             patch('upload_sites.candidate_relevant',return_value=True):
            listing=asyncio.run(adapter.resolve_publication(result))
        self.assertEqual(listing['torrent_id'],'123')

    def test_check_job_is_read_only_even_when_rebuild_needed(self):
        import asyncio
        from unittest.mock import AsyncMock
        adapter=AsyncMock();adapter.load_form.return_value=self.spec
        with patch('onlyone_repack.rebuild_onlyone',side_effect=AssertionError('must not rebuild')):
            with self.assertRaisesRegex(UploadBlocked,'exceeds destination limit'):
                asyncio.run(UploadWorker(self.ledger).check_job(self.job['id'],adapter,self.client))
        adapter.check_duplicates.assert_not_awaited()

    def test_reprepare_does_not_erase_rebuild_evidence(self):
        result=self.rebuild()
        metadata={**result['metadata'],'description':'updated description'}
        again=self.ledger.prepare(self.source,self.inventory,self.client.files(''),metadata,
            result['rule'],self.spec['announce'],readiness={'ready':True},form_schema=self.spec)
        self.assertEqual(again['repack'],result['repack'])
        self.assertEqual(again['prepared_hash'],result['prepared_hash'])


class OnlyOneMakerHostTests(unittest.TestCase):
    def client(self, url):
        node = SimpleNamespace(url=url, provider_sso=True)
        return SimpleNamespace(node=node)

    def test_new_hub_and_node_domains_are_accepted(self):
        for url in ('https://onlyoneseedbox.com/account/qbittorrent',
                    'https://s2.onlyoneseedbox.com/account/qbittorrent'):
            with self.subTest(url=url):
                maker = OnlyOneMaker(self.client(url))
                self.assertTrue(urlsplit(maker.base).hostname.endswith('onlyoneseedbox.com'))

    def test_unrelated_domain_is_rejected(self):
        with self.assertRaises(UploadBlocked):
            OnlyOneMaker(self.client('https://onlyoneseedbox.example/qbittorrent'))


if __name__=='__main__': unittest.main()
