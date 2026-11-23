import contextlib
import io
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import test_onlyone_repack as fixtures
import main as app
from auto_upload_retention import (CROSS_SITE_METHOD, RetentionUnverified, listing_stats,
                                   other_seeders, observation, inspect_upload,
                                   refresh_upload_retention, verification_target)
from torrent_metainfo import create_variant, _extract_torrent_info_hash
from torrent_store import stamp, utcnow

AUTH = '<a href="/logout.php">Logout</a>'
ORIGIN = 'https://www.dedbit.com'
LISTING = {'torrent_id':'1','details_url':ORIGIN+'/details.php?id=1'}


def details(completed='1', seeds=2, leeches=0, advertised=True):
    return (AUTH+'<dl><dt>Snatched</dt><dd>'+str(completed)+' time(s)<br><a>last 20 snatched</a></dd>'
        +'<dt>Peers</dt><dd>'+str(seeds)+' seed, '+str(leeches)+' leech ('+str(seeds+leeches)+' peers total)</dd></dl>'
        +(f'<script>$.get("ajaxpeers.php?id=1&type=seed", function(data){{}});</script>' if advertised else ''))


def peer_table(ids, complete='100.00%'):
    return ('success\n<table><tr><th>User/IP</th><th>Complete</th></tr>'
        +''.join('<tr><td><a href="userdetails.php?id='+uid+'">member</a></td><td>'+complete+'</td></tr>' for uid in ids)
        +'</table>').encode()


class PeerParserTests(unittest.TestCase):
    def test_snatch_counter_is_distinct_from_last_20_navigation(self):
        stats=listing_stats('DEDBIT',details(completed=0),LISTING['details_url'],LISTING)
        self.assertEqual(stats['completed'],0)
        self.assertEqual(stats['peer_url'],ORIGIN+'/ajaxpeers.php?id=1&type=seed')

    def test_comma_count_and_aggregate_counts_are_checked(self):
        self.assertEqual(listing_stats('DEDBIT',details(completed='1,234'),LISTING['details_url'],LISTING)['completed'],1234)
        with self.assertRaises(RetentionUnverified):
            listing_stats('DEDBIT',details().replace('(2 peers total)','(7 peers total)'),LISTING['details_url'],LISTING)

    def test_unknown_markup_missing_stat_and_login_page_do_not_mean_zero(self):
        for html in ['<h1>Login</h1>',AUTH,details().replace('<dt>Snatched</dt>','<dt>Hits</dt>')]:
            with self.assertRaises(Exception): listing_stats('DEDBIT',html,LISTING['details_url'],LISTING)

    def test_wrong_listing_identity_is_rejected(self):
        with self.assertRaises(Exception): listing_stats('DEDBIT',details(),ORIGIN+'/details.php?id=2',LISTING)

    def test_peer_endpoint_must_be_advertised_for_the_same_listing(self):
        stats=listing_stats('DEDBIT',details().replace('ajaxpeers.php?id=1','ajaxpeers.php?id=2'),LISTING['details_url'],LISTING)
        self.assertIsNone(stats['peer_url'])

    def test_all_our_connections_are_excluded_and_other_accounts_are_unique(self):
        self.assertEqual(other_seeders(peer_table(['10','10','20','20']),'DEDBIT',LISTING['details_url'],'10',4),1)
        self.assertEqual(other_seeders(peer_table(['10','10']),'DEDBIT',LISTING['details_url'],'10',2),0)

    def test_own_connection_may_be_absent_from_advertised_seed_count(self):
        self.assertEqual(other_seeders(peer_table(['10','20','30']),'DEDBIT',
                                       LISTING['details_url'],'10',2),2)

    def test_duplicate_connections_and_one_row_drift_keep_verified_other_accounts(self):
        self.assertEqual(other_seeders(peer_table(['20','20','30','40']),'DEDBIT',
                                       LISTING['details_url'],'10',3),3)
        self.assertEqual(other_seeders(peer_table([str(n) for n in range(20,29)]),'DEDBIT',
                                       LISTING['details_url'],'10',10),9)

    def test_anonymous_peer_or_unknown_current_account_never_counts_as_other(self):
        anonymous=b'success\n<table><tr><th>User/IP</th><th>Complete</th></tr><tr><td>Anonymous</td><td>100%</td></tr></table>'
        for fragment,own in [(anonymous,'10'),(peer_table(['20']),None),(peer_table(['20']),'0')]:
            with self.assertRaises(RetentionUnverified): other_seeders(fragment,'DEDBIT',LISTING['details_url'],own,1)

    def test_partial_peer_list_incomplete_peer_and_unrecognized_response_are_held(self):
        for fragment,total in [(peer_table(['20']),2),(peer_table(['20'],'99%'),1),(b'<h1>Login</h1>',1)]:
            with self.assertRaises(RetentionUnverified): other_seeders(fragment,'DEDBIT',LISTING['details_url'],'10',total)

    def test_untrusted_profile_link_is_rejected(self):
        raw=peer_table(['20']).replace(b'userdetails.php?id=20',b'https://other.example/userdetails.php?id=20')
        with self.assertRaises(Exception): other_seeders(raw,'DEDBIT',LISTING['details_url'],'10',1)


class RetentionStoreTests(unittest.TestCase):
    def setUp(self):
        fixtures.RepackTests.setUp(self)
        self.ledger.claim_submission(self.job['id'],self.job,{})
        listing={'torrent_id':'1','details_url':'https://www.bitsuse.com/bs_details.php?id=1'}
        self.ledger.record_publication(self.job['id'],listing)
        canonical=create_variant(self.source,'BITSUSE','special','180',self.spec['announce'])
        self.ledger.accept_canonical(self.job['id'],canonical)
        self.job=self.ledger.transition(self.job['id'],{'verified'},'seeding')
        self.target=self.job['target_hash']

    def ready(self):
        proof=observation(self.job,'ready','ready')
        proof.update(completed=1,other_seeders=1,own_peers_excluded=True,canonical_hash_verified=True)
        return proof

    def save(self,proof): self.store.record_upload_retention(self.job['id'],self.job,proof)

    def test_no_evidence_holds_uploaded_target_but_does_not_hold_ordinary_torrents(self):
        self.assertFalse(self.store.upload_retention_status(self.target)[0])
        self.assertTrue(self.store.upload_retention_status(self.job['source_hash'])[0])

    def test_ready_evidence_allows_guard_without_changing_seed_state_or_manual_hold(self):
        self.store.set_manual_hold('BITSUSE','1',True)
        self.save(self.ready())
        self.assertTrue(self.store.upload_retention_status(self.target)[0])
        self.assertEqual(self.ledger.get(self.job['id'])['state'],'seeding')
        self.assertEqual(self.store.protection_status(self.target,['BITSUSE'],policy_sites=['BITSUSE']),'PROTECTED')

    def test_expired_or_failed_recheck_never_reuses_old_ready_evidence(self):
        proof=self.ready(); proof['checked_at']=stamp(utcnow()-timedelta(seconds=400));proof['expires_at']=stamp(utcnow()-timedelta(seconds=100))
        self.save(proof);self.assertEqual(self.store.upload_retention_status(self.target)[1],'AUTO_UPLOAD_PEERS_STALE')
        self.save(self.ready());self.save(observation(self.job,'failed','fetch_failed'))
        self.assertFalse(self.store.upload_retention_status(self.target)[0])

    def test_ready_needs_both_thresholds_and_own_identity_and_hash_verification(self):
        for field,value in [('completed',0),('other_seeders',0),('own_peers_excluded',False),('canonical_hash_verified',False),('other_seeders',True)]:
            proof=self.ready();proof[field]=value
            with self.assertRaises(ValueError): self.save(proof)

    def test_proof_is_bound_to_exact_target_listing_and_job(self):
        for field,value in [('job_id','f'*64),('target_hash','a'*40),('site','DEDBIT'),('torrent_id','2')]:
            proof=self.ready();proof[field]=value
            with self.assertRaises(ValueError): self.save(proof)

    def test_direct_evidence_cannot_claim_cross_site_verification(self):
        proof=self.ready()
        proof['verification_site']='DEDBIT'
        with self.assertRaises(ValueError): self.save(proof)

    def test_job_changed_during_inspection_does_not_accept_result(self):
        self.ledger.transition(self.job['id'],{'seeding'},'rechecking')
        with self.assertRaises(ValueError): self.save(self.ready())

    def test_excessive_or_invalid_lifetime_is_rejected(self):
        for expires in [stamp(utcnow()+timedelta(days=1)),'invalid']:
            proof=self.ready();proof['expires_at']=expires
            with self.assertRaises(ValueError): self.save(proof)

    def test_cleaner_gate_checks_retention_before_remote_client_mutations(self):
        cleaner=app.NodeCleaner(SimpleNamespace(name='fake'),{'enable':True},{'enable':True})
        cleaner._purge_qbit_get_json=Mock()
        with patch.object(app,'_mapping_store',lambda:self.store):
            self.assertEqual(cleaner._check_purge_authorization(self.target)[1],'AUTO_UPLOAD_PEERS_UNVERIFIED')
        cleaner._purge_qbit_get_json.assert_not_called()


class InspectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.content=fixtures.torrent()
        self.job={'id':'a'*64,'target_site':'DEDBIT','target_hash':_extract_torrent_info_hash(self.content),'listing':LISTING}
        self.adapter=SimpleNamespace(site='DEDBIT',origin=ORIGIN,open=AsyncMock(return_value=(details(),LISTING['details_url'])),
            tab=SimpleNamespace(send=AsyncMock(return_value=[SimpleNamespace(name='uid',value='10')])),
            _download_bytes=AsyncMock(return_value=peer_table(['10','20'])),download_listing=AsyncMock(return_value=self.content))

    async def test_verified_other_seeder_completed_counter_and_canonical_hash_pass(self):
        proof=await inspect_upload(self.adapter,self.job)
        self.assertEqual((proof['status'],proof['other_seeders'],proof['completed']),('ready',1,1))
        self.assertTrue(proof['canonical_hash_verified'])
        self.assertNotIn('uid',proof);self.assertNotIn('ip',proof)

    async def test_bitsuse_job_can_be_verified_through_matching_dedbit_listing(self):
        tid='686663'
        listing={'torrent_id':tid,'details_url':'https://www.bitsuse.com/bs_details.php?id='+tid}
        job={'id':'b'*64,'target_site':'BITSUSE','target_hash':_extract_torrent_info_hash(self.content),
             'listing':listing}
        verify_site,dedbit_listing=verification_target(job)
        self.assertEqual(verify_site,'DEDBIT')
        self.assertEqual(dedbit_listing['details_url'],'https://www.dedbit.com/details.php?id='+tid)
        html=details(seeds=2).replace('ajaxpeers.php?id=1','ajaxpeers.php?id='+tid)
        adapter=SimpleNamespace(site='DEDBIT',origin=ORIGIN,
            open=AsyncMock(return_value=(html,dedbit_listing['details_url'])),
            tab=SimpleNamespace(send=AsyncMock(return_value=[SimpleNamespace(name='uid',value='10')])),
            _download_bytes=AsyncMock(return_value=peer_table(['10','20'])),
            download_listing=AsyncMock(return_value=self.content))
        proof=await inspect_upload(adapter,job,dedbit_listing,
                                   method=CROSS_SITE_METHOD,verification_site='DEDBIT')
        self.assertEqual(proof['site'],'BITSUSE')
        self.assertEqual(proof['verification_site'],'DEDBIT')
        self.assertEqual(proof['method'],CROSS_SITE_METHOD)
        self.assertEqual((proof['status'],proof['other_seeders'],proof['completed']),('ready',1,1))
        self.assertTrue(proof['canonical_hash_verified'])
        adapter.open.assert_awaited_once_with(dedbit_listing['details_url'])

    async def test_own_seeder_only_is_held(self):
        self.adapter.open.return_value=(details(seeds=1),LISTING['details_url'])
        self.adapter._download_bytes.return_value=peer_table(['10'])
        proof=await inspect_upload(self.adapter,self.job)
        self.assertEqual(proof['reason'],'other_seeders_required')
        self.adapter.download_listing.assert_not_called()

    async def test_zero_completed_never_passes_and_does_not_download_torrent(self):
        self.adapter.open.return_value=(details(completed=0),LISTING['details_url'])
        proof=await inspect_upload(self.adapter,self.job)
        self.assertEqual(proof['reason'],'completed_required')
        self.adapter.download_listing.assert_not_called()

    async def test_aggregate_seed_count_without_identities_is_not_enough(self):
        self.adapter.open.return_value=(details(seeds=20,advertised=False),LISTING['details_url'])
        proof=await inspect_upload(self.adapter,self.job)
        self.assertEqual(proof['reason'],'peer_identity_unverified')
        self.assertIsNone(proof['other_seeders'])

    async def test_network_error_and_hash_mismatch_are_held(self):
        self.adapter.open.side_effect=TimeoutError()
        self.assertEqual((await inspect_upload(self.adapter,self.job))['status'],'failed')
        self.adapter.open.side_effect=None
        self.adapter.download_listing.return_value=fixtures.torrent(seed=b'z')
        self.assertEqual((await inspect_upload(self.adapter,self.job))['reason'],'canonical_hash_mismatch')


class RefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self): RetentionStoreTests.setUp(self)

    def application(self, stopped=False):
        async def call(method,*args): return getattr(self.store,method)(*args)
        return SimpleNamespace(CFG={'GLOBAL_CLEAN':{'enable':True},'NODES':[{'enable':True}]},
            NodeCleaner=lambda *a:SimpleNamespace(enabled=lambda:True),
            stop_event=SimpleNamespace(is_set=lambda:stopped),
            _mapping_store=lambda:self.store,_store_call=call)

    async def test_fresh_ready_result_does_not_open_browser_again(self):
        proof=RetentionStoreTests.ready(self)
        self.store.record_upload_retention(self.job['id'],self.job,proof)
        with patch('upload_runner.UploadSession') as session:
            self.assertEqual(await refresh_upload_retention(self.application()),[])
            session.assert_not_called()

    async def test_recent_held_or_failed_results_stay_blocked_without_repeated_requests(self):
        for status,reason in [('held','completed_required'),('failed','fetch_failed')]:
            self.store.record_upload_retention(self.job['id'],self.job,observation(self.job,status,reason))
            with patch('upload_runner.UploadSession') as session:
                self.assertEqual(await refresh_upload_retention(self.application()),[])
                session.assert_not_called()
            self.assertFalse(self.store.upload_retention_status(self.target)[0])

    async def test_login_failure_replaces_expired_ready_result_and_preserves_state(self):
        proof=RetentionStoreTests.ready(self)
        proof.update(checked_at=stamp(utcnow()-timedelta(seconds=400)),expires_at=stamp(utcnow()-timedelta(seconds=100)))
        self.store.record_upload_retention(self.job['id'],self.job,proof)
        session=SimpleNamespace(adapter=AsyncMock(side_effect=RuntimeError()),close=AsyncMock())
        with patch('upload_runner.UploadSession',return_value=session),contextlib.redirect_stdout(io.StringIO()):
            result=await refresh_upload_retention(self.application())
        self.assertEqual(result[0]['status'],'failed')
        self.assertEqual(self.ledger.get(self.job['id'])['state'],'seeding')
        self.assertFalse(self.store.upload_retention_status(self.target)[0])
        session.close.assert_awaited_once()

    async def test_bitsuse_job_uses_dedbit_adapter_for_cross_site_evidence(self):
        job=dict(self.job)
        job['id']='c'*64
        job['target_site']='BITSUSE'
        job['listing']={'torrent_id':'686663',
                        'details_url':'https://www.bitsuse.com/bs_details.php?id=686663'}
        store=SimpleNamespace(read=lambda:{'upload_jobs':{job['id']:job}})
        app=SimpleNamespace(CFG={'GLOBAL_CLEAN':{'enable':True},'NODES':[{'enable':True}]},
            NodeCleaner=lambda *a:SimpleNamespace(enabled=lambda:True),
            stop_event=SimpleNamespace(is_set=lambda:False),
            _mapping_store=lambda:store,_store_call=AsyncMock())
        adapter=SimpleNamespace(site='DEDBIT',open=AsyncMock(side_effect=RuntimeError('offline')))
        session=SimpleNamespace(adapter=AsyncMock(return_value=adapter),close=AsyncMock())
        with patch('upload_runner.UploadSession',return_value=session),contextlib.redirect_stdout(io.StringIO()):
            result=await refresh_upload_retention(app)
        session.adapter.assert_awaited_once_with('DEDBIT')
        self.assertEqual(result[0]['method'],CROSS_SITE_METHOD)
        self.assertEqual(result[0]['verification_site'],'DEDBIT')
        self.assertEqual(result[0]['site'],'BITSUSE')
        session.close.assert_awaited_once()

    async def test_shutdown_does_not_start_web_work(self):
        with patch('upload_runner.UploadSession') as session:
            self.assertEqual(await refresh_upload_retention(self.application(stopped=True)),[])
            session.assert_not_called()


if __name__=='__main__':unittest.main()
