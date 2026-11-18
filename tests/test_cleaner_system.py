import asyncio
import contextlib
import io
import json
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

import main as app
from auto_upload import UploadBlocked
from cleaner_obligations import parse_snapshot, sync_site
from torrent_store import MappingStore, stamp, utcnow
from torrent_metainfo import bencode, _extract_torrent_info_hash

A = 'a' * 40
B = 'b' * 40
ORIGIN = 'https://bearbit.org'
AUTH = '<a href="/logout.php">Logout</a>'
HR_EMPTY = AUTH + '<table class="t"><tr><td class="empty">Empty</td></tr></table>'
QUEST_EMPTY = AUTH + '<ul class="bbx-qlist"></ul>'


class CleanerSwitchTests(unittest.TestCase):
    def setUp(self):
        now = time.time()
        self.row = {'hash': A, 'progress': 1, 'amount_left': 0, 'size_bytes': 2*1024**3,
                    'ratio': 1, 'added_on': now-7200, 'ts_finished': now-7200, 'up_speed': 0, 'leechers': 0}
        self.node = SimpleNamespace(name='fake', allow_space_reclaim=True, free_gb=0,
            refresh_status=Mock(return_value=True), get_all_torrents_info=Mock(return_value=[self.row]))
        self.output = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.output); self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)
        self.stop = patch.object(app, 'stop_event', SimpleNamespace(is_set=lambda: False)); self.stop.start()
        self.addCleanup(self.stop.stop)

    def cleaner(self, node=True, master=True):
        c = app.NodeCleaner(self.node, {'enable': node}, {'enable': master})
        c.check_torrent_permission = Mock(return_value='NORMAL')
        c._hard_purge_sequence = Mock(return_value=False)
        return c

    def test_every_disabled_switch_blocks_normal_and_emergency_reclaim(self):
        for node, master in [(False, True), (True, False), (False, False)]:
            for emergency in [False, True]:
                c = self.cleaner(node, master)
                self.assertFalse(c.smart_reclaim_process(10, emergency))
                c._hard_purge_sequence.assert_not_called()
        self.node.refresh_status.assert_not_called()
        self.node.get_all_torrents_info.assert_not_called()

    def test_allow_reclaim_is_separate_explicit_opt_in(self):
        self.node.allow_space_reclaim = False
        c = self.cleaner(); self.assertFalse(c.smart_reclaim_process(10, True))
        self.node.refresh_status.assert_not_called()

    def test_emergency_already_has_enough_space_does_not_scan(self):
        self.node.free_gb = 30
        self.assertTrue(self.cleaner().smart_reclaim_process(10, True))
        self.node.get_all_torrents_info.assert_not_called()

    def test_reclaim_stops_after_confirmed_target_reached(self):
        self.node.get_all_torrents_info.return_value = [self.row, {**self.row, 'hash': B}]
        def refresh():
            if self.node.refresh_status.call_count > 1: self.node.free_gb = 20
            return True
        self.node.refresh_status.side_effect = refresh
        c = self.cleaner(); c._hard_purge_sequence.return_value = True
        self.assertTrue(c.smart_reclaim_process(10, True))
        c._hard_purge_sequence.assert_called_once()

    def test_shutdown_stops_before_any_remote_action(self):
        with patch.object(app, 'stop_event', SimpleNamespace(is_set=lambda: True)):
            self.assertFalse(self.cleaner().smart_reclaim_process(10, True))
        self.node.refresh_status.assert_not_called()

    def test_final_purge_gate_also_honors_disabled_switch(self):
        c = app.NodeCleaner(self.node, {'enable': False}, {'enable': True})
        c._check_purge_authorization = Mock()
        self.assertFalse(c._hard_purge_sequence(A))
        c._check_purge_authorization.assert_not_called()

    def test_expired_evidence_during_file_scan_prevents_any_mutation(self):
        c=app.NodeCleaner(self.node,{'enable':True},{'enable':True})
        c._check_purge_authorization=Mock(side_effect=[(True,'ok',{},['BEARBIT']),(False,'stale',{},[])])
        c._can_delete_data=Mock(return_value=True)
        self.node.reannounce_torrent=Mock(); self.node.stop_torrent=Mock()
        self.assertFalse(c._hard_purge_sequence(A))
        self.node.reannounce_torrent.assert_not_called(); self.node.stop_torrent.assert_not_called()

    def test_new_obligation_after_stop_prevents_delete(self):
        c=app.NodeCleaner(self.node,{'enable':True},{'enable':True})
        c._check_purge_authorization=Mock(side_effect=[(True,'ok',{},['BEARBIT']),(True,'ok',{},['BEARBIT']),(False,'required',{},[])])
        c._can_delete_data=Mock(return_value=True)
        self.node.reannounce_torrent=Mock(); self.node.stop_torrent=Mock(return_value=True); self.node.delete_torrent=Mock()
        self.assertFalse(c._hard_purge_sequence(A))
        self.node.delete_torrent.assert_not_called()

    def test_active_old_torrent_is_not_removed_by_legacy_expiry(self):
        self.assertFalse(self.cleaner()._should_remove(2,721,100000,1))


class QbitStopTests(unittest.TestCase):
    def run_purge(self, state):
        node=object.__new__(app.QbitNode)
        node.name='fake'
        node.reannounce_torrent=Mock(return_value=True)
        node.stop_torrent=Mock(return_value=True)
        node.delete_torrent=Mock(return_value=True)
        cleaner=app.NodeCleaner(node,{'enable':True},{'enable':True})
        cleaner._check_purge_authorization=Mock(return_value=(True,'verified',{'state':state},['BEARBIT']))
        cleaner._can_delete_data=Mock(return_value=True)
        cleaner._update_db_status=Mock()
        with patch.object(app,'stop_event',SimpleNamespace(is_set=lambda:False)),contextlib.redirect_stdout(io.StringIO()):
            result=cleaner._hard_purge_sequence(A)
        return result,node

    def test_stop_ack_without_observed_stopped_state_never_deletes(self):
        result,node=self.run_purge('uploading')
        self.assertFalse(result)
        node.delete_torrent.assert_not_called()

    def test_confirmed_stopped_state_allows_final_deletion(self):
        for state in ('pausedUP','stoppedUP'):
            result,node=self.run_purge(state)
            self.assertTrue(result)
            node.delete_torrent.assert_called_once_with(A,delete_files=True)

    def test_current_endpoint_and_no_content_success(self):
        node=SimpleNamespace(name='fake',url='https://client.example',_execute_request=Mock(return_value=SimpleNamespace(status_code=204)))
        self.assertTrue(app.QbitNode.stop_torrent(node,A))
        self.assertTrue(node._execute_request.call_args.args[1].endswith('/stop'))
        self.assertTrue(node._execute_request.call_args.kwargs['verify'])

    def test_legacy_fallback_only_after_explicit_404(self):
        node=SimpleNamespace(name='fake',url='https://client.example',_execute_request=Mock(side_effect=[SimpleNamespace(status_code=404),SimpleNamespace(status_code=200)]))
        self.assertTrue(app.QbitNode.stop_torrent(node,A))
        self.assertTrue(node._execute_request.call_args.args[1].endswith('/pause'))
        for status in [None,401,403,500]:
            node._execute_request=Mock(return_value=None if status is None else SimpleNamespace(status_code=status))
            self.assertFalse(app.QbitNode.stop_torrent(node,A))
            self.assertEqual(node._execute_request.call_count,1)


class StoreFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = MappingStore(Path(tmp.name) / 'mapping.json'); self.store.initialize()

    def observe(self, hr=(), quest=()):
        for kind, ids in [('hr', hr), ('quest_seed', quest)]:
            self.store.record_required_snapshot('BEARBIT', kind, ids)
            self.store.finalize_required_snapshot('BEARBIT', kind)


class ObligationProofTests(StoreFixture):
    def test_complete_empty_pages_prove_absence_without_listing_guess(self):
        self.observe()
        self.assertEqual(self.store.protection_status(A, ['BEARBIT']), 'NORMAL')
        self.assertEqual(self.store.read()['site_records'], {})

    def test_unresolved_required_listing_blocks_the_entire_site(self):
        self.observe(hr=['1'])
        self.assertEqual(self.store.protection_status(A, ['BEARBIT']), 'UNKNOWN')
        self.store.bind_obligation_listing('BEARBIT', '1', B)
        self.assertTrue(self.store.finalize_required_snapshot('BEARBIT', 'hr'))
        self.assertEqual(self.store.protection_status(A, ['BEARBIT']), 'NORMAL')
        self.assertEqual(self.store.protection_status(B, ['BEARBIT']), 'PROTECTED')

    def test_stale_or_failed_snapshot_never_clears(self):
        self.observe()
        self.store.record_failure('BEARBIT', 'hr')
        self.assertEqual(self.store.protection_status(A, ['BEARBIT']), 'UNKNOWN')
        self.observe()
        def expire(data): data['site_sync']['BEARBIT']['hr']['expires_at'] = stamp(utcnow()-timedelta(seconds=1))
        self.store.update(expire)
        self.assertEqual(self.store.protection_status(A, ['BEARBIT']), 'UNKNOWN')

    def test_other_sites_still_require_their_own_proof(self):
        self.observe()
        self.assertEqual(self.store.protection_status(A, ['BEARBIT', 'DEDBIT']), 'UNKNOWN')

    def test_manual_hold_survives_complete_empty_snapshots(self):
        self.observe(hr=['1']); self.store.bind_obligation_listing('BEARBIT', '1', A)
        self.store.set_manual_hold('BEARBIT', '1', True)
        self.observe()
        self.assertEqual(self.store.protection_status(A, ['BEARBIT']), 'PROTECTED')

    def test_conflicting_bound_hash_never_changes(self):
        self.observe(hr=['1']); self.store.bind_obligation_listing('BEARBIT', '1', A)
        with self.assertRaises(ValueError): self.store.bind_obligation_listing('BEARBIT', '1', B)
        self.assertEqual(self.store.read()['site_records']['BEARBIT:1']['infohash'], A)

    def test_legacy_verified_full_parser_also_produces_hash_proof(self):
        self.store.record_observation('BEARBIT', 'hr', [])
        self.store.record_observation('BEARBIT', 'quest_seed', [])
        self.assertEqual(self.store.protection_status(A, ['BEARBIT']), 'NORMAL')

    def test_hash_snapshot_schema_rejects_invalid_hash(self):
        self.observe()
        def mutate(data): data['site_sync']['BEARBIT']['hr']['required_hashes'] = ['invalid']
        with self.assertRaises(ValueError): self.store.update(mutate)

    def test_explicit_site_policy_does_not_forge_obligation_evidence(self):
        self.assertEqual(self.store.protection_status(A,['DEDBIT'],policy_sites=['DEDBIT']),'NORMAL')
        self.assertEqual(self.store.read()['site_sync'],{})
        self.assertEqual(self.store.protection_status(A,['DEDBIT']),'UNKNOWN')

    def test_site_policy_does_not_override_required_or_manual_hold(self):
        self.store.record_required_snapshot('DEDBIT','hr',['1'])
        self.store.bind_obligation_listing('DEDBIT','1',A)
        self.assertEqual(self.store.protection_status(A,['DEDBIT'],policy_sites=['DEDBIT']),'PROTECTED')
        self.store.record_required_snapshot('DEDBIT','hr',[])
        self.store.set_manual_hold('DEDBIT','1',True)
        self.assertEqual(self.store.protection_status(A,['DEDBIT'],policy_sites=['DEDBIT']),'PROTECTED')

    def test_policy_never_waives_a_second_sites_obligations(self):
        self.assertEqual(self.store.protection_status(A,['DEDBIT','BEARBIT'],policy_sites=['DEDBIT','BEARBIT']),'UNKNOWN')


class ListingReconciliationTests(StoreFixture):
    def add(self, digest=A, verified=True, site='DEDBIT', tid='1', url=None):
        listing = {'site_key':site, 'torrent_id':tid, 'verified':verified,
                   'details_url':url or 'https://www.dedbit.com/details.php?id='+tid}
        self.store.update(lambda d:d['torrents'].append({'client_info':{'hash':digest},'site_listings':[listing]}))

    def test_verified_listing_repairs_binding_but_does_not_clear_obligations(self):
        self.add(); result = self.store.reconcile_verified_listings()
        self.assertEqual(result['created'], 1)
        self.assertEqual(self.store.read()['site_records']['DEDBIT:1']['infohash'], A)
        self.assertEqual(self.store.protection_status(A, ['DEDBIT']), 'UNKNOWN')
        self.assertEqual(self.store.reconcile_verified_listings()['created'], 0)

    def test_unverified_or_wrong_origin_listing_is_not_adopted(self):
        self.add(verified=False); self.add(B, url='https://other.example/details.php?id=1')
        self.assertEqual(self.store.reconcile_verified_listings()['created'], 0)

    def test_ambiguous_identity_is_not_bound_to_first_seen_hash(self):
        self.add(); self.add(B)
        self.assertEqual(self.store.reconcile_verified_listings()['conflicts'], 1)
        self.assertEqual(self.store.read()['site_records'], {})

    def test_existing_protection_is_preserved_when_hash_is_bound(self):
        self.add(site='BEARBIT',url=ORIGIN+'/details.php?id=1')
        self.store.record_required_snapshot('BEARBIT', 'hr', ['1'])
        self.store.set_manual_hold('BEARBIT', '1', True)
        self.assertEqual(self.store.reconcile_verified_listings()['bound'], 1)
        self.assertEqual(self.store.protection_status(A, ['BEARBIT']), 'PROTECTED')


class ObligationParserTests(unittest.TestCase):
    def test_empty_and_vip_pages(self):
        self.assertEqual(parse_snapshot('BEARBIT','hr',HR_EMPTY,ORIGIN+'/myhr.php')[0],set())
        self.assertEqual(parse_snapshot('BEARBIT','hr',AUTH+'<div class="vipbox">VIP</div>',ORIGIN+'/myhr.php')[0],set())
        self.assertEqual(parse_snapshot('BEARBIT','quest_seed',QUEST_EMPTY,ORIGIN+'/mybonus.php')[0],set())

    def test_unrecognized_or_logged_out_pages_are_not_empty_proof(self):
        for html in ['<h1>404</h1>', AUTH+'<h1>H&R</h1>', '<table class="t"><td class="empty">Empty</td></table>']:
            with self.assertRaises(UploadBlocked): parse_snapshot('BEARBIT','hr',html,ORIGIN+'/myhr.php')

    def test_seed_quest_finished_chip_is_clear_but_unknown_chip_is_required(self):
        html = AUTH+'<ul class="bbx-qlist"><li><a href="details.php?id=1">one</a><span class="bbx-chip">ครบแล้ว</span></li><li><a href="details.php?id=2">two</a></li></ul>'
        self.assertEqual(parse_snapshot('BEARBIT','quest_seed',html,ORIGIN+'/mybonus.php')[0],{'2'})

    def test_pagination_is_collected_and_external_origins_rejected(self):
        html = HR_EMPTY+'<a href="myhr.php?page=2">2</a>'
        self.assertEqual(parse_snapshot('BEARBIT','hr',html,ORIGIN+'/myhr.php')[1],{ORIGIN+'/myhr.php?page=2'})
        with self.assertRaises(UploadBlocked): parse_snapshot('BEARBIT','hr',HR_EMPTY,ORIGIN+'/index.php')
        with self.assertRaises(UploadBlocked): parse_snapshot('BEARBIT','hr',HR_EMPTY+'<a href="https://other.example/myhr.php?page=2">2</a>',ORIGIN+'/myhr.php')


class LiveGuardTests(StoreFixture):
    def guard(self, host, snapshot=True):
        self.store.update(lambda d:d['torrents'].append({'client_info':{'hash':A}}))
        if snapshot: self.observe()
        node = SimpleNamespace(name='fake')
        cleaner = app.NodeCleaner(node, {'enable':True}, {'enable':True})
        def request(endpoint, params):
            if endpoint.endswith('/info'): return [{'hash':A,'progress':1,'amount_left':0}], 'OK'
            return [{'url':'https://'+host+'/announce.php?passkey=do-not-log'}], 'OK'
        cleaner._purge_qbit_get_json = request
        return cleaner

    def test_bearbit_cc_can_pass_only_after_complete_fresh_evidence(self):
        c = self.guard('files.bit-sv.cc')
        with patch.object(app,'CLIENTS',SimpleNamespace(adapter_for=lambda n:SimpleNamespace(client_type='qbit',purge_reader=None))), patch.object(app,'MAPPING_FILE',str(self.store.path)), patch.object(app,'_mapping_store',lambda:self.store):
            self.assertTrue(c._check_purge_authorization(A)[0])
            self.store.record_failure('BEARBIT','hr')
            self.assertFalse(c._check_purge_authorization(A)[0])

    def test_host_suffix_is_not_equivalent_to_an_exact_verified_alias(self):
        c = self.guard('other.files.bit-sv.cc')
        with patch.object(app,'CLIENTS',SimpleNamespace(adapter_for=lambda n:SimpleNamespace(client_type='qbit',purge_reader=None))),patch.object(app,'_mapping_store',lambda:self.store):
            self.assertFalse(c._check_purge_authorization(A)[0])

    def test_dedbit_policy_checks_fresh_age_ratio_and_activity_even_for_emergency_candidates(self):
        c=self.guard('tracker.cab',snapshot=False)
        c.clean_settings.update(min_ratio=1.5,min_time=720,max_time=1440)
        c.global_cfg['site_obligations']={'DEDBIT':'cleaner_policy'}
        original=c._purge_qbit_get_json
        metadata={'ratio':2,'completion_on':time.time()-13*3600,'upspeed':0,'num_leechs':0}
        def request(endpoint,params):
            rows,error=original(endpoint,params)
            if endpoint.endswith('/info'): rows=[{**r,**metadata} for r in rows]
            return rows,error
        c._purge_qbit_get_json=request
        with patch.object(app,'CLIENTS',SimpleNamespace(adapter_for=lambda n:SimpleNamespace(client_type='qbit',purge_reader=None))),patch.object(app,'MAPPING_FILE',str(self.store.path)),patch.object(app,'_mapping_store',lambda:self.store):
            self.assertTrue(c._check_purge_authorization(A)[0])
            metadata['completion_on']=time.time()-2*3600
            self.assertFalse(c._check_purge_authorization(A)[0])
            metadata['completion_on']=time.time()-13*3600; metadata['ratio']=1
            self.assertFalse(c._check_purge_authorization(A)[0])
            metadata['ratio']=2; metadata['upspeed']=100000
            self.assertFalse(c._check_purge_authorization(A)[0])
            metadata['upspeed']=0; metadata['completion_on']=0
            self.assertFalse(c._check_purge_authorization(A)[0])


class SnapshotSyncTests(unittest.IsolatedAsyncioTestCase):
    setUp = StoreFixture.setUp

    def application(self):
        async def call(method, *args): return getattr(self.store, method)(*args)
        return SimpleNamespace(_mapping_store=lambda:self.store, _store_call=call)

    async def test_every_pagination_page_is_read_before_clear_is_recorded(self):
        self.store.record_required_snapshot('BEARBIT','hr',['1','2'])
        self.store.bind_obligation_listing('BEARBIT','1',A)
        self.store.bind_obligation_listing('BEARBIT','2',B)
        first = AUTH+'<table class="t"><tr><td><a href="details.php?id=1">one</a></td></tr></table><a href="myhr.php?page=2">Next</a>'
        second = AUTH+'<table class="t"><tr><td><a href="details.php?id=2">two</a></td></tr></table>'
        pages = {ORIGIN+'/myhr.php':first,ORIGIN+'/myhr.php?page=2':second,ORIGIN+'/mybonus.php':QUEST_EMPTY}
        async def open_page(url): return pages[url],url
        adapter=SimpleNamespace(site='BEARBIT',origin=ORIGIN,open=AsyncMock(side_effect=open_page),download_listing=AsyncMock())
        with contextlib.redirect_stdout(io.StringIO()): result=await sync_site(self.application(),adapter)
        self.assertEqual(result['hr']['pages'],2)
        self.assertEqual(self.store.protection_status(A,['BEARBIT']),'PROTECTED')
        self.assertEqual(self.store.protection_status('c'*40,['BEARBIT']),'NORMAL')
        adapter.download_listing.assert_not_called()

    async def test_failed_second_page_does_not_clear_previous_required_tasks(self):
        self.store.record_required_snapshot('BEARBIT','hr',['1'])
        self.store.bind_obligation_listing('BEARBIT','1',A)
        async def open_page(url):
            if 'page=2' in url: raise TimeoutError()
            if '/mybonus.php' in url: return QUEST_EMPTY,url
            return HR_EMPTY+'<a href="myhr.php?page=2">Next</a>',url
        adapter=SimpleNamespace(site='BEARBIT',origin=ORIGIN,open=open_page)
        with contextlib.redirect_stdout(io.StringIO()): result=await sync_site(self.application(),adapter)
        self.assertEqual(result['hr']['status'],'failed')
        self.assertEqual(self.store.protection_status(A,['BEARBIT']),'PROTECTED')

    async def test_unresolved_metainfo_retains_protection(self):
        async def open_page(url):
            if '/mybonus.php' in url: return QUEST_EMPTY,url
            return AUTH+'<table class="t"><tr><td><a href="details.php?id=1">one</a></td></tr></table>',url
        adapter=SimpleNamespace(site='BEARBIT',origin=ORIGIN,open=open_page,download_listing=AsyncMock(side_effect=TimeoutError()))
        with contextlib.redirect_stdout(io.StringIO()): result=await sync_site(self.application(),adapter)
        self.assertEqual(result['hr']['status'],'failed')
        self.assertEqual(self.store.protection_status(A,['BEARBIT']),'UNKNOWN')

    async def test_worker_thread_notification_is_scheduled_on_the_event_loop(self):
        cleaner=app.NodeCleaner(SimpleNamespace(name='fake'),{'enable':True},{'enable':True})
        notify=AsyncMock()
        with patch.object(app,'send_notify',notify):
            await asyncio.to_thread(cleaner._notify_results,{'Target Reached':{'header':'done','torrents':['neutral job']}})
            await asyncio.sleep(0)
        notify.assert_awaited_once()

if __name__ == '__main__': unittest.main()
