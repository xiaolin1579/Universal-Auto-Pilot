import unittest
from unittest.mock import Mock
from upload_client import QbitUploadClient
from auto_upload import UploadBlocked
from torrent_metainfo import bencode,_extract_torrent_info_hash


class QbitResponseTests(unittest.TestCase):
    def setUp(self):
        self.content=bencode({b'info':{b'name':b'file',b'length':1,b'piece length':1,b'pieces':b'a'*20}})
        self.target=_extract_torrent_info_hash(self.content)
        self.job={'target_hash':self.target,'save_path':'/downloads','target_site':'BITSUSE','metadata':{'title':'test'}}
        self.node=Mock();self.node.name='node';self.node.url='https://example.com/qbittorrent'
        self.client=QbitUploadClient(self.node)

    def response(self,status=200,text='',data=None):
        r=Mock(status_code=status,text=text)
        if data is None:r.json.side_effect=ValueError('not JSON')
        else:r.json.return_value=data
        self.node._execute_request.return_value=r
        return r

    def summary(self,**changes):
        return {'success_count':1,'failure_count':0,'pending_count':0,'added_torrent_ids':[self.target],**changes}

    def test_legacy_ok_still_supported(self):
        self.response(text='Ok.')
        self.assertTrue(self.client.add_paused(self.content,self.job))

    def test_json_success_for_exactly_one_matching_torrent(self):
        self.response(data=self.summary())
        self.assertTrue(self.client.add_paused(self.content,self.job))
        kwargs=self.node._execute_request.call_args.kwargs
        self.assertEqual(kwargs['data']['seedMode'],'false')
        self.assertEqual(kwargs['data']['stopped'],'true')
        self.node._execute_request.assert_called_once()

    def test_pending_and_mismatched_or_malformed_results_are_not_confirmed(self):
        for status,data in [(202,self.summary(success_count=0,pending_count=1,added_torrent_ids=[])),
            (200,self.summary(added_torrent_ids=['f'*40])),(200,self.summary(failure_count=1)),
            (200,self.summary(success_count=True)),(200,{}),(200,self.summary(added_torrent_ids=[]))]:
            with self.subTest(status=status,data=data):
                self.node._execute_request.reset_mock();self.response(status=status,data=data)
                self.assertFalse(self.client.add_paused(self.content,self.job))
                self.node._execute_request.assert_called_once()

    def test_error_status_does_not_retry_the_add(self):
        self.response(status=409)
        with self.assertRaises(RuntimeError):self.client.add_paused(self.content,self.job)
        self.node._execute_request.assert_called_once()

    def test_wrong_artifact_is_rejected_before_request(self):
        with self.assertRaises(UploadBlocked):self.client.add_paused(self.content,{**self.job,'target_hash':'f'*40})
        self.node._execute_request.assert_not_called()

    def test_recheck_accepts_no_content_response(self):
        self.response(status=204)
        self.assertTrue(self.client.recheck(self.target))
        self.node._execute_request.assert_called_once()

    def test_inventory_does_not_accept_204_as_valid_json_inventory(self):
        self.response(status=204)
        with self.assertRaises(RuntimeError):self.client.inventory(self.target)

    def test_resume_accepts_204_and_only_falls_back_on_404(self):
        self.response(status=204)
        self.assertTrue(self.client.resume(self.target))
        self.node._execute_request.assert_called_once()
        self.node._execute_request.reset_mock()
        self.node._execute_request.side_effect=[Mock(status_code=404),Mock(status_code=200)]
        self.assertTrue(self.client.resume(self.target))
        self.assertEqual(self.node._execute_request.call_count,2)

    def test_wrong_reconcile_state_gives_seed_command_before_client_action(self):
        from auto_upload import UploadWorker
        ledger=Mock()
        client=Mock();client.name='node'
        for state in ('verified','rechecking'):
            with self.subTest(state=state):
                ledger.get.return_value={'id':'a'*64,'state':state,'source_node':'node'}
                with self.assertRaisesRegex(UploadBlocked,'use seed'):
                    UploadWorker(ledger).reconcile_seed('a'*64,client)
        client.inventory.assert_not_called();client.recheck.assert_not_called()


if __name__=='__main__':unittest.main()
