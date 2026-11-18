import copy
import io
import unittest
from urllib.parse import parse_qs, urlsplit
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import main as app
from cross_seed_validation import source_file_pairs, preflight_evidence
from torrent_metainfo import bencode, _extract_torrent_manifest


class CrossSeedFixture:
    def setUp(self):
        self.content=bencode({b'announce':b'https://tracker.example/announce',b'comment':b'x'*1100,
            b'info':{b'name':b'Series',b'files':[
                {b'path':[b'sub',b'E01.mkv'],b'length':1024},
                {b'path':[b'poster.jpg'],b'length':15}],
                b'piece length':1024,b'pieces':b'a'*40,b'private':1}})
        self.manifest=_extract_torrent_manifest(self.content)
        self.source_hash='D'*40;self.digest=self.manifest['info_hash'].upper()
        self.files=[{'path':'Series/'+f['path'],'size_bytes':f['size_bytes']} for f in self.manifest['files']]
        self.config={'name':'test','enable':True,'type':'qbit'}
        self.added=False
        self.source_info={'hash':self.source_hash.lower(),'name':'Series','progress':1,'amount_left':0,
            'total_size':1039,'save_path':'/data','content_path':'/data/Series','state':'uploading'}
        self.node=SimpleNamespace(name='test',url='https://client.example',auth=None,
            get_torrent_file_manifest=Mock(side_effect=lambda _:copy.deepcopy(self.files)),
            add=Mock(side_effect=self.add),stop_torrent=Mock(),resume_torrent=Mock(),delete=Mock())
        self.node._execute_request=Mock(side_effect=self.request)
        proof={'status':'filelist_manifest_verified_recheck_required','action':'different_info_hash_requires_paused_add_and_recheck',
            'content_verification':'NOT_VERIFIED_RECHECK_REQUIRED','candidate_info_hash':self.digest,
            'candidate_trackers':self.manifest['announce_urls'],'candidate_manifest':self.manifest,
            'torrent_content':self.content,'source_total_size_bytes':1039,'candidate_total_size_bytes':1039,
            'source_file_mapping':[{'source_path':f['path'],'candidate_path':f['path'],
                'source_size_bytes':f['size_bytes'],'candidate_size_bytes':f['size_bytes']} for f in self.manifest['files']],
            'candidate_extra_files':[]}
        self.match={**preflight_evidence(proof),'source_hash':self.source_hash,'site_name':'DEDBIT',
            'source_node':self.node,'source_node_name':'test','source_node_config':self.config,
            'source_torrent_name':'Series','source_save_path':'/data','source_size_bytes':1039,
            'source_site_verified':False}

    def add(self,**kwargs):self.added=True;return True

    def request(self,method,url,**kwargs):
        if method=='GET' and url.endswith('/torrents/info'):
            digest=kwargs['params']['hashes'].upper()
            if digest==self.source_hash:rows=[copy.deepcopy(self.source_info)]
            elif digest==self.digest and self.added:
                rows=[{**self.source_info,'hash':self.digest.lower(),'state':'stoppedUP'}]
            else:rows=[]
            return SimpleNamespace(status_code=200,json=lambda:rows)
        if method=='POST' and url.endswith('/torrents/recheck'):
            return SimpleNamespace(status_code=204)
        raise AssertionError('Unexpected request')

    def execute(self,match=None,config=None):
        output=io.StringIO()
        with patch.object(app,'CFG',config or {'CROSS_SEED_EXECUTION':{'enable_new_hash_add':True},'NODES':[self.config]}),redirect_stdout(output):
            app.run_opt_in_new_hash_cross_seed([self.match if match is None else match],[(self.node,self.config)])
        return output.getvalue()


class CrossSeedPathTests(unittest.TestCase):
    def test_strip_only_one_verified_root_and_keep_subdirectories(self):
        rows=[{'path':'Series/sub/E01.mkv','size_bytes':5},{'path':'Series/E02.mkv','size_bytes':6}]
        self.assertEqual(source_file_pairs(rows,'Series',True),[('sub/E01.mkv',5),('E02.mkv',6)])
        self.assertEqual(source_file_pairs([{'path':'Series/Series/E01.mkv','size_bytes':5}],'Series',True),[('Series/E01.mkv',5)])

    def test_bare_paths_single_file_and_mixed_prefixes_are_not_guessed(self):
        rows=[{'path':'sub/E01.mkv','size_bytes':5}]
        self.assertEqual(source_file_pairs(rows,'Series',True),[('sub/E01.mkv',5)])
        self.assertEqual(source_file_pairs([{'path':'file.mkv','size_bytes':5}],'file.mkv',False),[('file.mkv',5)])
        rows=[{'path':'Series/a','size_bytes':5},{'path':'other/b','size_bytes':6}]
        self.assertEqual(source_file_pairs(rows,'Series',True),[('Series/a',5),('other/b',6)])

    def test_no_traversal_absolute_paths_duplicate_paths_or_negative_sizes(self):
        for rows in ([{'path':'../a','size_bytes':5}],[{'path':'Series/../a','size_bytes':5}],
                     [{'path':'/data/a','size_bytes':5}],[{'path':'C:\\a','size_bytes':5}],
                     [{'path':'a','size_bytes':-1}],[{'path':'a','size_bytes':1}]*2):
            with self.subTest(rows=rows),self.assertRaises(ValueError):source_file_pairs(rows,'Series',True)


class CrossSeedEncodingTests(unittest.TestCase):
    def test_bearbit_thai_keyword_uses_cp874_and_preserves_filters(self):
        keyword='เสือชะนีเก้ง'
        url=app.build_search_url('BEARBIT',keyword,selected_cats=[7],searchin=1,incldead=1)
        query=parse_qs(urlsplit(url).query,encoding='cp874',errors='strict')
        self.assertEqual(query['search'],[keyword])
        self.assertEqual(query['c7'],['1']);self.assertEqual(query['searchin'],['1'])
        self.assertEqual(query['incldead'],['1'])

    def test_other_trackers_remain_utf8_and_ascii_bearbit_is_unchanged(self):
        for site in ('DEDBIT','BITSUSE'):
            url=app.build_search_url(site,'ทดสอบ 中文')
            self.assertEqual(parse_qs(urlsplit(url).query)['search'],['ทดสอบ 中文'])
        self.assertEqual(parse_qs(urlsplit(app.build_search_url('BEARBIT','Example S01E02')).query)['search'],['Example S01E02'])

    def test_unencodable_keyword_is_skipped_without_dropping_identity_characters(self):
        self.assertIsNone(app.build_search_url('BEARBIT','Example 😀'))


class CrossSeedExecutionTests(CrossSeedFixture,unittest.TestCase):
    def test_evidence_keeps_verification_state_sizes_and_original_bytes(self):
        self.assertEqual(self.match['content_verification'],'NOT_VERIFIED_RECHECK_REQUIRED')
        self.assertEqual(self.match['source_total_size_bytes'],1039)
        self.assertEqual(self.match['candidate_total_size_bytes'],1039)
        self.assertIs(self.match['torrent_content'],self.content)

    def test_qbit_root_prefix_passes_and_204_recheck_is_accepted_without_resume(self):
        output=self.execute()
        self.assertIn('PAUSED_ADD_RECHECK_REQUESTED',output)
        self.node.add.assert_called_once()
        self.assertTrue(self.node.add.call_args.kwargs['paused'])
        self.assertFalse(self.node.add.call_args.kwargs['skip_checking'])
        self.node.resume_torrent.assert_not_called();self.node.delete.assert_not_called()
        self.node.stop_torrent.assert_not_called()

    def test_live_gates_support_a_read_only_dry_run(self):
        output=io.StringIO()
        with patch.object(app,'CFG',{'CROSS_SEED_EXECUTION':{'enable_new_hash_add':True},'NODES':[self.config]}),redirect_stdout(output):
            app.run_opt_in_new_hash_cross_seed([self.match],[(self.node,self.config)],dry_run=True)
        self.assertIn('live gates passed; dry-run only',output.getvalue())
        self.node.add.assert_not_called()
        self.assertTrue(all(call.args[0]=='GET' for call in self.node._execute_request.call_args_list))

    def test_real_subdirectory_difference_still_rejects(self):
        self.files[0]['path']='Series/other/E01.mkv'
        self.assertIn('Exact source/candidate path or size mismatch',self.execute())
        self.node.add.assert_not_called()

    def test_source_without_verified_client_root_is_not_treated_as_multi_file(self):
        self.files=[{'path':f['path'],'size_bytes':f['size_bytes']} for f in self.manifest['files']]
        self.assertIn('Source client multi-file root layout',self.execute())
        self.node.add.assert_not_called()

    def test_cross_seed_add_requests_stopped_and_original_layout_on_qbit5(self):
        request=Mock(return_value=SimpleNamespace(status_code=200,text='Ok.'))
        node=SimpleNamespace(name='test',url='https://client.example',auth=None,_execute_request=request)
        self.assertTrue(app.QbitNode.add(node,self.content,paused=True,cross_seed=True,skip_checking=False,save_path='/data'))
        data=request.call_args.kwargs['data']
        self.assertEqual(data['paused'],'true');self.assertEqual(data['stopped'],'true')
        self.assertEqual(data['skip_checking'],'false');self.assertEqual(data['seedMode'],'false')
        self.assertEqual(data['contentLayout'],'Original')

    def test_missing_verification_state_still_rejects(self):
        match=dict(self.match);match.pop('content_verification')
        self.assertIn('unexpected verification state',self.execute(match))
        self.node.add.assert_not_called()

    def test_modified_metainfo_or_root_or_source_incompleteness_still_rejects(self):
        for change in ('content','root','source'):
            with self.subTest(change=change):
                self.added=False;self.node.add.reset_mock()
                match=dict(self.match);self.source_info['progress']=1
                if change=='content':match['torrent_content']=self.content.replace(b'a'*40,b'b'*40)
                if change=='root':match['source_torrent_name']='Other'
                if change=='source':self.source_info['progress']=0.5
                self.execute(match)
                self.node.add.assert_not_called()

    def test_existing_hash_or_unavailable_enabled_node_prevents_duplicate_add(self):
        self.added=True
        self.assertIn('already present',self.execute())
        self.node.add.assert_not_called()
        config={'CROSS_SEED_EXECUTION':{'enable_new_hash_add':True},'NODES':[self.config,{'name':'missing','enable':True,'type':'qbit'}]}
        self.assertIn('Cannot inspect all enabled nodes',self.execute(config=config))
        self.node.add.assert_not_called()


class CrossSeedPreflightTests(CrossSeedFixture,unittest.IsolatedAsyncioTestCase):
    async def test_root_prefix_survives_preflight_collection_and_audit(self):
        filelist={'visibility':'visible','url':'https://www.dedbit.com/filelist.php?id=1','title':'File List',
            'entries':[{'name':f['path'],'size_estimates':[{'bytes':f['size_bytes'],'tolerance_bytes':0}]} for f in self.manifest['files']]}
        target={'source_node':self.node,'source_hash':self.source_hash,'source_torrent_name':'Series',
                'source_size_bytes':1039,'source_save_path':'/data'}
        with patch.object(app,'download_torrent_smart',new=AsyncMock(return_value=self.content)), \
             patch.object(app,'_read_cross_seed_filelist',new=AsyncMock(return_value=filelist)):
            result=await app._preflight_cross_seed_candidate_single_source(object(),target,'DEDBIT','1','https://www.dedbit.com/details.php?id=1','https://www.dedbit.com/download.php?id=1')
        self.assertTrue(result['ok'],result.get('error'))
        match={**self.match,**preflight_evidence(result)}
        self.assertEqual(match['source_file_mapping'][0]['source_path'],'sub/E01.mkv')
        output=io.StringIO()
        with patch.object(app,'record_cross_seed_known_sites',new=AsyncMock()), \
             patch.object(app,'record_cross_seed_site_listings',new=AsyncMock()),redirect_stdout(output):
            await app.audit_cross_seed_execution_plan([match],[(self.node,self.config)])
        self.assertIn('WOULD_ADD_PAUSED_AND_RECHECK',output.getvalue())
        self.assertNotIn('relative_path_mismatch',output.getvalue())
        self.assertIn('PAUSED_ADD_RECHECK_REQUESTED',self.execute(match))


class SingleFileCrossSeedTests(CrossSeedFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.content=bencode({b'announce':b'https://tracker.example/announce',b'comment':b'x'*1100,
            b'info':{b'name':b'Episode10.mkv',b'length':1039,b'piece length':1024,b'pieces':b'a'*40,b'private':1}})
        self.manifest=_extract_torrent_manifest(self.content)
        self.digest=self.manifest['info_hash'].upper()
        self.files=[{'path':'Episode10.mkv','size_bytes':1039}]
        self.source_info.update(name='Episode10.mkv',content_path='/data/Episode10.mkv')
        self.match.update(candidate_info_hash=self.digest,candidate_manifest=self.manifest,torrent_content=self.content,
            source_torrent_name='Episode10.mkv',source_file_mapping=[{'source_path':'Episode10.mkv',
                'candidate_path':'Episode10.mkv','source_size_bytes':1039,'candidate_size_bytes':1039}])

    def test_exact_single_file_is_added_paused_and_rechecked(self):
        self.assertIn('PAUSED_ADD_RECHECK_REQUESTED',self.execute())
        self.node.add.assert_called_once()
        kwargs=self.node.add.call_args.kwargs
        self.assertTrue(kwargs['paused']);self.assertFalse(kwargs['skip_checking'])
        self.assertEqual(kwargs['save_path'],'/data')
        self.node.resume_torrent.assert_not_called();self.node.delete.assert_not_called()

    def test_single_file_dry_run_has_no_mutation(self):
        with patch.object(app,'CFG',{'CROSS_SEED_EXECUTION':{'enable_new_hash_add':True},'NODES':[self.config]}),redirect_stdout(io.StringIO()) as out:
            app.run_opt_in_new_hash_cross_seed([self.match],[(self.node,self.config)],dry_run=True)
        self.assertIn('live gates passed; dry-run only',out.getvalue())
        self.node.add.assert_not_called()
        self.assertTrue(all(c.args[0]=='GET' for c in self.node._execute_request.call_args_list))

    def test_single_file_is_not_matched_by_basename_inside_a_directory(self):
        self.files=[{'path':'Other/Episode10.mkv','size_bytes':1039}]
        self.assertIn('Exact source/candidate path or size mismatch',self.execute())
        self.node.add.assert_not_called()

    def test_source_with_additional_files_is_not_a_single_file_match(self):
        self.files=[{'path':'Episode10.mkv','size_bytes':1024},{'path':'extra.txt','size_bytes':15}]
        self.assertIn('file count differs',self.execute())
        self.node.add.assert_not_called()

    def test_source_path_name_size_or_progress_change_prevents_add(self):
        for field,value in [('content_path','/elsewhere/Episode10.mkv'),('name','Renamed.mkv'),
                            ('total_size',1038),('progress',0.5)]:
            before=copy.deepcopy(self.source_info)
            with self.subTest(field=field):
                self.source_info[field]=value;self.execute();self.node.add.assert_not_called()
            self.source_info=before

    def test_existing_candidate_or_unavailable_other_node_prevents_add(self):
        self.added=True
        self.assertIn('already present',self.execute());self.node.add.assert_not_called()
        self.added=False
        other=SimpleNamespace(name='other',url='https://other.example',auth=None,
            _execute_request=Mock(return_value=SimpleNamespace(status_code=503)))
        other_cfg={'name':'other','type':'qbit','enable':True}
        with patch.object(app,'CFG',{'CROSS_SEED_EXECUTION':{'enable_new_hash_add':True},'NODES':[self.config,other_cfg]}),redirect_stdout(io.StringIO()) as out:
            app.run_opt_in_new_hash_cross_seed([self.match],[(self.node,self.config),(other,other_cfg)])
        self.assertIn('cannot inspect other',out.getvalue());self.node.add.assert_not_called()

    def test_changed_candidate_bytes_prevent_single_file_add(self):
        self.match['torrent_content']=self.content.replace(b'a'*40,b'b'*40)
        self.assertIn('metainfo differs',self.execute());self.node.add.assert_not_called()

    def test_post_add_path_mismatch_stops_without_recheck_resume_or_delete(self):
        original=self.node._execute_request.side_effect
        def request(method,url,**kwargs):
            result=original(method,url,**kwargs)
            if method=='GET' and self.added and kwargs.get('params',{}).get('hashes')==self.digest.lower():
                row=result.json()[0];row['content_path']='/wrong/Episode10.mkv'
                return SimpleNamespace(status_code=200,json=lambda:[row])
            return result
        self.node._execute_request.side_effect=request
        self.assertIn('post-add path/size verification failed',self.execute())
        self.node.stop_torrent.assert_called_once()
        self.node.resume_torrent.assert_not_called();self.node.delete.assert_not_called()
        self.assertTrue(all(c.args[0]=='GET' for c in self.node._execute_request.call_args_list))


if __name__=='__main__':unittest.main()
