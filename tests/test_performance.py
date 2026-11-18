import asyncio
import contextlib
import copy
import io
import threading
import time
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs,urlsplit
from unittest.mock import AsyncMock,Mock,patch

import requests
import main as app
from auto_upload import UploadBlocked
from cleaner_obligations import refresh_cleaner_evidence,refresh_before_cleaner
from storage_reads import bounded_read,qbit_snapshot,roots_overlap,_clone_session
from upload_runner import run_cycle

A='a'*40
B='b'*40


class Response:
    def __init__(self,value,status=200): self.value=value; self.status_code=status; self.closed=False
    def json(self): return copy.deepcopy(self.value)
    def close(self): self.closed=True


class ParallelReadTests(unittest.TestCase):
    def test_reads_are_bounded_and_results_keep_original_order(self):
        active=0;peak=0;lock=threading.Lock()
        def read(value):
            nonlocal active,peak
            with lock: active+=1;peak=max(peak,active)
            time.sleep(.02 if value%2 else .01)
            with lock: active-=1
            return value*2
        self.assertEqual(bounded_read(range(12),read),[v*2 for v in range(12)])
        self.assertGreater(peak,1);self.assertLessEqual(peak,4)

    def test_failed_read_never_returns_a_partial_snapshot(self):
        calls=[];lock=threading.Lock()
        def read(value):
            with lock: calls.append(value)
            if value==0: raise ValueError('failed')
            time.sleep(.03)
            return value
        with self.assertRaises(ValueError): bounded_read(range(30),read)
        self.assertLessEqual(len(calls),4)

    def test_shutdown_stops_new_reads_and_does_not_return_partial_results(self):
        stop=threading.Event();read=Mock()
        with self.assertRaises(ValueError): bounded_read(range(4),read,stopped=lambda:True)
        read.assert_not_called()
        def finish(value): stop.set();return value
        with self.assertRaises(ValueError): bounded_read(range(30),finish,stopped=stop.is_set)

    def test_invalid_concurrency_is_rejected(self):
        for workers in [0,5,True]:
            with self.assertRaises(ValueError): bounded_read([1],lambda x:x,workers=workers)

    def test_http_sessions_and_cookie_jars_are_isolated(self):
        source=requests.Session();source.auth=('user','private');source.trust_env=False
        source.headers['X-Test']='read-only';source.cookies.set('SID','private',domain='client.example')
        clone=_clone_session(source)
        try:
            self.assertIsNot(clone,source);self.assertIsNot(clone.cookies,source.cookies)
            self.assertEqual(clone.auth,source.auth);self.assertFalse(clone.trust_env)
            self.assertEqual(clone.headers['Connection'],'close')
            clone.cookies.set('SID','changed',domain='client.example')
            self.assertEqual(source.cookies.get('SID',domain='client.example'),'private')
            self.assertIsNot(clone.adapters['https://'],source.adapters['https://'])
        finally: clone.close();source.close()


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.rows=[{'hash':A,'content_path':'/data/a','save_path':'/data'},
                   {'hash':B,'content_path':'/data/b','save_path':'/data'}]
        self.after=copy.deepcopy(self.rows);self.files={A:[{'name':'a'}],B:[{'name':'b'}]}
        self.node=SimpleNamespace(url='https://client.example',s=requests.Session())
        self.addCleanup(self.node.s.close)
        self.node._execute_request=Mock(side_effect=lambda *a,**k:Response(self.rows if self.node._execute_request.call_count==1 else self.after))
        self.transports=[];self.calls=[]
        test=self
        class Reader:
            def get(self,url,**kwargs):
                test.calls.append((url,kwargs))
                self.last=Response(test.files[parse_qs(urlsplit(url).query)['hash'][0]])
                return self.last
            def close(self): self.closed=True
        def clone(source):
            reader=Reader();self.transports.append(reader);return reader
        self.patcher=patch('storage_reads._clone_session',side_effect=clone)
        self.patcher.start();self.addCleanup(self.patcher.stop)

    def snapshot(self): return qbit_snapshot(self.node,app._storage_path,app._storage_file,lambda:False)

    def test_all_files_are_verified_and_membership_is_read_again(self):
        result=self.snapshot()
        self.assertEqual(result,[{'hash':A,'root':'/data/a','files':{'/data/a'}},
                                 {'hash':B,'root':'/data/b','files':{'/data/b'}}])
        self.assertEqual(len(self.calls),2);self.assertEqual(self.node._execute_request.call_count,2)
        for _,kwargs in self.calls:
            self.assertTrue(kwargs['verify']);self.assertFalse(kwargs['allow_redirects'])
        self.assertTrue(all(t.closed and t.last.closed for t in self.transports))

    def test_new_or_moved_job_invalidates_the_snapshot(self):
        self.after.append({'hash':'c'*40,'content_path':'/data/c','save_path':'/data'})
        with self.assertRaisesRegex(ValueError,'changed'): self.snapshot()
        self.node._execute_request.reset_mock()
        self.after=copy.deepcopy(self.rows);self.after[0]['save_path']='/elsewhere'
        with self.assertRaisesRegex(ValueError,'changed'): self.snapshot()

    def test_invalid_inventory_is_rejected_before_file_reads(self):
        self.rows.append(copy.deepcopy(self.rows[0]))
        with self.assertRaises(ValueError): self.snapshot()
        self.assertEqual(self.calls,[])

    def test_missing_or_outside_file_never_authorizes_a_snapshot(self):
        self.files[B]=[]
        with self.assertRaises(ValueError): self.snapshot()
        self.node._execute_request.reset_mock();self.files[B]=[{'name':'../b'}]
        with self.assertRaises(ValueError): self.snapshot()
        self.node._execute_request.reset_mock();self.files[B]=[{'name':'unrelated'}]
        with self.assertRaises(ValueError): self.snapshot()

    def test_shared_root_can_be_refused_without_reading_files(self):
        self.rows[1]['content_path']='/data/a/sub'
        self.assertTrue(roots_overlap(self.node,A,app._storage_path,lambda:False))
        self.assertEqual(self.calls,[])

    def test_disjoint_roots_do_not_themselves_authorize_deletion(self):
        self.assertFalse(roots_overlap(self.node,A,app._storage_path,lambda:False))
        self.assertEqual(self.calls,[])


class OwnershipShortcutTests(unittest.TestCase):
    def test_known_local_overlap_skips_full_inventory_and_peers(self):
        node=SimpleNamespace(name='fake');cleaner=app.NodeCleaner(node,{'enable':True},{'enable':True})
        registry=SimpleNamespace(adapter_for=Mock(return_value=SimpleNamespace(storage_conflict_reader=lambda n,h:True)),create=Mock())
        with patch.object(app,'CFG',{'NODES':[{'name':'fake','storage_id':'disk'}]}),patch.object(app,'CLIENTS',registry),patch.object(app,'_read_storage_inventory') as read:
            self.assertFalse(cleaner._can_delete_data(A))
            read.assert_not_called();registry.create.assert_not_called()

    def test_no_overlap_still_reads_disabled_peers_and_all_file_references(self):
        node=SimpleNamespace(name='fake');peer=SimpleNamespace(login=Mock(return_value=True))
        cleaner=app.NodeCleaner(node,{'enable':True},{'enable':True})
        registry=SimpleNamespace(adapter_for=lambda n:SimpleNamespace(storage_conflict_reader=lambda n,h:False),create=Mock(return_value=peer),close=Mock())
        cfg={'NODES':[{'name':'fake','storage_id':'disk'},{'name':'disabled','storage_id':'disk','enable':False}]}
        references=[[{'hash':A,'root':'/data/a','files':{'/data/a'}}],[{'hash':B,'root':'/data/b','files':{'/data/b'}}]]
        with patch.object(app,'CFG',cfg),patch.object(app,'CLIENTS',registry),patch.object(app,'_read_storage_inventory',side_effect=references) as read:
            self.assertTrue(cleaner._can_delete_data(A));self.assertEqual(read.call_count,2)
        registry.create.assert_called_once();peer.login.assert_called_once();registry.close.assert_called_once_with(peer)


class SharedBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_evidence_collectors_share_one_session_closed_once(self):
        session=SimpleNamespace(close=AsyncMock())
        target=SimpleNamespace(stop_event=SimpleNamespace(is_set=lambda:False))
        with patch('upload_runner.UploadSession',return_value=session) as ctor,patch('cleaner_obligations.refresh_before_cleaner',new=AsyncMock(return_value={})) as obligations,patch('auto_upload_retention.refresh_upload_retention',new=AsyncMock(return_value=[])) as uploads:
            await refresh_cleaner_evidence(target)
        ctor.assert_called_once();obligations.assert_awaited_once_with(target,session=session)
        uploads.assert_awaited_once_with(target,session=session);session.close.assert_awaited_once()

    async def test_shutdown_does_not_start_second_collector(self):
        flag=threading.Event()
        session=SimpleNamespace(close=AsyncMock());target=SimpleNamespace(stop_event=flag)
        async def obligations(*a,**k): flag.set();return {}
        with patch('upload_runner.UploadSession',return_value=session),patch('cleaner_obligations.refresh_before_cleaner',new=AsyncMock(side_effect=obligations)),patch('auto_upload_retention.refresh_upload_retention',new=AsyncMock()) as uploads:
            await refresh_cleaner_evidence(target)
        uploads.assert_not_awaited();session.close.assert_awaited_once()

    async def test_stopped_before_refresh_does_not_create_a_session(self):
        with patch('upload_runner.UploadSession') as ctor:
            await refresh_cleaner_evidence(SimpleNamespace(stop_event=SimpleNamespace(is_set=lambda:True)))
        ctor.assert_not_called()


class UploadCycleSpeedTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        flag=threading.Event()
        rule={'enable':True,'source_zone':'normal','target_site':'DEDBIT'}
        target=SimpleNamespace(CFG={'AUTO_UPLOAD':{'enable':True,'rules':[rule],'max_jobs_per_cycle':10}},BASE_DIR='/tmp',
            CLIENTS=SimpleNamespace(adapter_for=lambda n:SimpleNamespace(upload_factory=object())),stop_event=flag)
        ledger=Mock();ledger.store.read.return_value={'upload_jobs':{},'site_records':{'BEARBIT:1':{'site':'BEARBIT','torrent_id':'1','infohash':A}}}
        notices=SimpleNamespace(record=Mock(),record_summary=Mock(),flush=AsyncMock())
        source=SimpleNamespace(read_source=AsyncMock(return_value=({'title':'neutral'},b'metainfo')))
        session=SimpleNamespace(close=AsyncMock(),adapter=AsyncMock(return_value=source))
        nodes=[(SimpleNamespace(name=name,get_all_torrents_info=Mock(return_value=[{'hash':A,'progress':1,'amount_left':0}])),{}) for name in ('one','two')]
        return target,ledger,notices,session,source,nodes,rule

    async def execute(self,context,prepare):
        target,ledger,notices,session,source,nodes,rule=context
        with patch('upload_runner.ledger_for',return_value=ledger),patch('upload_runner.UploadNotifications',return_value=notices),patch('upload_runner.UploadSession',return_value=session),patch('upload_runner.select_rules',return_value=[rule]),patch('upload_runner.client_for',side_effect=lambda a,n:n),patch('upload_runner._prepare_destination',new=prepare),contextlib.redirect_stdout(io.StringIO()):
            return await run_cycle(target,nodes)

    async def test_source_read_is_shared_but_each_node_still_prepares_independently(self):
        context=self.context();seen=[]
        async def prepare(a,s,c,h,m,content,r):
            seen.append((c.name,m['title']));m['title']='changed'
            raise UploadBlocked('Destination unavailable')
        await self.execute(context,AsyncMock(side_effect=prepare))
        self.assertEqual(seen,[('one','neutral'),('two','neutral')]);context[4].read_source.assert_awaited_once()

    async def test_permanent_zone_mismatch_is_not_read_twice_in_same_cycle(self):
        context=self.context();context[4].read_source.side_effect=UploadBlocked('Source category is not present in this BearBit zone')
        prepare=AsyncMock();await self.execute(context,prepare)
        context[4].read_source.assert_awaited_once();prepare.assert_not_awaited()

    async def test_transient_failures_remain_retryable_on_another_node(self):
        context=self.context();context[4].read_source.side_effect=[TimeoutError(),({'title':'neutral'},b'metainfo')]
        await self.execute(context,AsyncMock(side_effect=UploadBlocked('Destination unavailable')))
        self.assertEqual(context[4].read_source.await_count,2)

    async def test_stopped_cycle_does_not_start_browser_or_inventory(self):
        context=self.context();context[0].stop_event.set()
        await self.execute(context,AsyncMock())
        context[3].adapter.assert_not_awaited()
        for node,_ in context[5]:node.get_all_torrents_info.assert_not_called()

    async def test_shutdown_after_source_read_does_not_prepare_or_scan_next_node(self):
        context=self.context()
        async def source(*a): context[0].stop_event.set();return {'title':'neutral'},b'metainfo'
        context[4].read_source.side_effect=source
        prepare=AsyncMock();await self.execute(context,prepare)
        prepare.assert_not_awaited();context[5][1][0].get_all_torrents_info.assert_not_called()

    async def test_cycle_cache_is_not_reused_across_rounds(self):
        context=self.context();context[4].read_source.side_effect=UploadBlocked('Source category is not present in this BearBit zone')
        await self.execute(context,AsyncMock());await self.execute(context,AsyncMock())
        self.assertEqual(context[4].read_source.await_count,2)
