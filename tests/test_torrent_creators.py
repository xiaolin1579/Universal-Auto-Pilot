import base64
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_onlyone_repack as fixtures
from test_onlyone_repack import torrent
from auto_upload import UploadBlocked, publication_signature, create_job_variant
from torrent_creators import QbitCreator, RutorrentCreator, DelugeCreator, detect_creator
from torrent_repack import rebuild_metainfo, choose_piece_size
from upload_client import QbitUploadClient


class Response:
    def __init__(self, data=None, status=200, content=b''):
        self.data, self.status_code, self.content = data, status, content
    def json(self): return self.data
    def close(self): pass
    def iter_content(self, size): yield self.content


class CreatorTests(unittest.TestCase):
    setUp = fixtures.RepackTests.setUp

    def native(self, lose_response=False):
        calls = []; rows = []
        def request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == 'POST':
                data = kwargs['data']
                # The source claim must be durable before any remote mutation.
                self.assertEqual(self.ledger.get(self.job['id'])['repack_request']['marker'], data['comment'])
                rows.append({'comment': data['comment'], 'sourcePath': data['sourcePath'],
                             'pieceSize': data['pieceSize'], 'private': True, 'format': 'v1',
                             'status': 'Finished', 'taskID': 'owned-task'})
                if lose_response: raise TimeoutError()
                return Response({'taskID': 'owned-task'})
            if 'torrentFile' in url: return Response(content=self.candidate)
            return Response(rows)
        node = SimpleNamespace(name='OnlyOne', url='https://client.example/qbittorrent', _execute_request=request)
        client = QbitUploadClient(node)
        client.inventory = self.client.inventory; client.files = self.client.files
        return client, calls, rows

    def test_qbit_lost_post_is_recovered_without_another_post(self):
        client, calls, _ = self.native(lose_response=True)
        with self.assertRaises(UploadBlocked): rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        result = rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        self.assertEqual(result['repack']['provider'], 'qbittorrent')
        self.assertEqual(sum(c[0] == 'POST' for c in calls), 1)
        self.assertEqual(result['content_group_id'], self.job['content_group_id'])
        self.assertLessEqual(len(create_job_variant(result, self.source, self.spec['announce'])), self.spec['max_file_size'])
        data = next(c[2]['data'] for c in calls if c[0] == 'POST')
        self.assertEqual(data['startSeeding'], 'false')
        self.assertNotIn('torrentFilePath', data)
        self.assertNotIn('?', data['trackers'])

    def test_missing_task_never_repeats_creation(self):
        client, calls, rows = self.native()
        with self.assertRaises(UploadBlocked): rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        rows.clear()
        with self.assertRaisesRegex(UploadBlocked, 'unresolved'):
            rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        self.assertEqual(sum(c[0] == 'POST' for c in calls), 1)
        with self.assertRaises(UploadBlocked): self.ledger.transition(self.job['id'], {'prepared'}, 'cancelled')

    def test_wrong_task_binding_is_rejected(self):
        client, _, rows = self.native()
        with self.assertRaises(UploadBlocked): rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        rows[0]['sourcePath'] = '/other/data'
        with self.assertRaisesRegex(UploadBlocked, 'differs'):
            rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        self.assertNotIn('repack', self.ledger.get(self.job['id']))

    def test_uncertain_publication_keeps_its_old_signature(self):
        self.ledger.transition(self.job['id'], {'prepared'}, 'publication_uncertain')
        client, _, _ = self.native()
        with self.assertRaises(UploadBlocked): rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        result = rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        self.assertEqual(result['state'], 'publication_uncertain')
        self.assertEqual(publication_signature(result), self.job['content_group_id'])

    def test_result_layout_and_actual_limit_are_checked(self):
        for candidate in (torrent(piece=2*1024**2, name=b'wrong.bin'), torrent(piece=4*1024**2)):
            client, _, _ = self.native()
            with self.assertRaises(UploadBlocked): rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
            self.candidate = candidate
            with self.assertRaises(UploadBlocked): rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        self.assertNotIn('repack', self.ledger.get(self.job['id']))

    def test_oversize_candidate_stays_held(self):
        client, _, _ = self.native()
        with self.assertRaises(UploadBlocked): rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        with self.assertRaises(UploadBlocked):
            rebuild_metainfo(self.ledger, self.job['id'], client, {**self.spec, 'max_file_size': 200})
        self.assertNotIn('repack', self.ledger.get(self.job['id']))

    def test_small_torrent_does_not_probe_creator(self):
        client, calls, _ = self.native()
        with self.assertRaises(UploadBlocked):
            rebuild_metainfo(self.ledger, self.job['id'], client, {**self.spec, 'max_file_size': 100000})
        self.assertEqual(calls, [])

    def test_impossible_limit_does_not_claim_or_post(self):
        client, calls, _ = self.native()
        with self.assertRaisesRegex(UploadBlocked, 'cannot fit'):
            rebuild_metainfo(self.ledger, self.job['id'], client, {**self.spec, 'max_file_size': 1})
        self.assertNotIn('repack_request', self.ledger.get(self.job['id']))
        self.assertFalse(any(c[0] == 'POST' for c in calls))

    def test_only_explicit_absence_allows_onlyone_fallback(self):
        client, _, _ = self.native()
        for status in (401, 403, 500, 302):
            client.node._execute_request = lambda *a, **k: Response(status=status)
            with patch('onlyone_repack.OnlyOneMaker') as fallback:
                with self.assertRaises(UploadBlocked): detect_creator(client)
                fallback.assert_not_called()
        client.node._execute_request = lambda *a, **k: Response(status=404)
        with patch('onlyone_repack.OnlyOneMaker'):
            self.assertIsNone(detect_creator(client))

    def test_native_request_schema_rejects_traversal(self):
        client, _, _ = self.native()
        with self.assertRaises(UploadBlocked): rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        def mutate(data): data['upload_jobs'][self.job['id']]['repack_request']['source_path'] = '/a/../b'
        with self.assertRaises(ValueError): self.store.update(mutate)

    def test_deluge_version_methods_and_no_session_add(self):
        calls = []
        def rpc(method, params, **kw):
            calls.append((method, params, kw))
            if method == 'core.get_path_size': return -1
            return ['new.torrent', base64.b64encode(self.candidate).decode()]
        node = SimpleNamespace(version='2.2.0', methods={'core.create_torrent', 'core.get_path_size'}, rpc=rpc)
        maker = DelugeCreator(SimpleNamespace(node=node))
        self.assertTrue(maker.available())
        node.version = '2.1.1'; self.assertFalse(maker.available()); node.version = '2.2.0'
        claim = {'source_path': '/data/payload.bin', 'marker': 'uap-' + 'a'*32, 'piece_size': 2*1024**2}
        self.assertEqual(maker.start(claim, self.spec['announce']), self.candidate)
        args = calls[-1][1]
        self.assertFalse(args[9]); self.assertEqual(args[10], 'v1')
        self.assertEqual(args[2], 2*1024**2); self.assertTrue(calls[-1][2]['mutation'])
        self.assertEqual(args[4], '/data/' + claim['marker'] + '.torrent')

    def test_deluge_existing_output_never_overwritten(self):
        node = SimpleNamespace(rpc=lambda *a, **k: 123)
        with self.assertRaisesRegex(UploadBlocked, 'already exists'):
            DelugeCreator(SimpleNamespace(node=node)).start(
                {'source_path': '/data/source', 'marker': 'uap-' + 'a'*32}, self.spec['announce'])

    def test_deluge_cached_result_recovers_without_another_rpc(self):
        calls = []
        def rpc(method, params, **kw):
            calls.append(method)
            return -1 if method == 'core.get_path_size' else ['new.torrent', base64.b64encode(self.candidate).decode()]
        node = SimpleNamespace(url='https://deluge.example', rpc=rpc)
        self.client.node = node
        maker = DelugeCreator(self.client)
        with patch('torrent_repack.detect_creator', return_value=maker):
            with self.assertRaisesRegex(UploadBlocked, 'started'):
                rebuild_metainfo(self.ledger, self.job['id'], self.client, self.spec)
            result = rebuild_metainfo(self.ledger, self.job['id'], self.client, self.spec)
            self.assertEqual(result['repack']['provider'], 'deluge')
            self.assertEqual(calls.count('core.create_torrent'), 1)

    def test_deluge_lost_response_keeps_claim_without_repeating_rpc(self):
        calls = []
        def rpc(method, params, **kw):
            calls.append(method)
            if method == 'core.get_path_size': return -1
            raise TimeoutError()
        self.client.node = SimpleNamespace(url='https://deluge.example', rpc=rpc)
        maker = DelugeCreator(self.client)
        with patch('torrent_repack.detect_creator', return_value=maker):
            with self.assertRaises(TimeoutError): rebuild_metainfo(self.ledger, self.job['id'], self.client, self.spec)
            with self.assertRaisesRegex(UploadBlocked, 'unresolved'):
                rebuild_metainfo(self.ledger, self.job['id'], self.client, self.spec)
            self.assertEqual(calls.count('core.create_torrent'), 1)

    def test_stale_job_never_accepts_creator_result(self):
        client, _, _ = self.native()
        with self.assertRaises(UploadBlocked): rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        original = QbitCreator.result
        def result(maker, claim):
            content = original(maker, claim)
            def change(data): data['upload_jobs'][self.job['id']]['field_overrides'] = {'title': 'changed'}
            self.store.update(change)
            return content
        with patch.object(QbitCreator, 'result', result), self.assertRaisesRegex(UploadBlocked, 'Job changed'):
            rebuild_metainfo(self.ledger, self.job['id'], client, self.spec)
        self.assertNotIn('repack', self.ledger.get(self.job['id']))

    def test_rutorrent_task_binding_and_download(self):
        maker = RutorrentCreator(SimpleNamespace(node=SimpleNamespace(url='https://box.example/RPC2')))
        self.assertEqual(maker.base, 'https://box.example/rutorrent')
        claim = {'source_path': '/data/payload', 'piece_size': 2*1024**2, 'marker': 'uap-' + 'a'*32}
        params = {'path_edit': '/data/payload/', 'piece_size': '2048', 'comment': claim['marker'],
                  'start_seeding': '0', 'private': '1', 'hybrid': None}
        calls = []
        def request(route, **kw):
            calls.append((route, kw))
            if kw.get('binary'): return self.candidate
            return {'123abc.456': {'status': 0, 'params': params}}
        maker.request = request
        self.assertEqual(maker.result(claim), self.candidate)
        maker.start(claim, self.spec['announce'])
        self.assertEqual(calls[-1][1]['data']['piece_size'], 2048)
        self.assertEqual(calls[-1][1]['data']['start_seeding'], 0)
        self.assertNotIn('hybrid', calls[-1][1]['data'])
        params['hybrid'] = '1'
        with self.assertRaisesRegex(UploadBlocked, 'differs'): maker.result(claim)
        params['hybrid'] = None
        params['path_edit'] = '/other'
        with self.assertRaisesRegex(UploadBlocked, 'differs'): maker.result(claim)

    def test_rutorrent_cross_origin_credentials_rejected(self):
        with self.assertRaises(UploadBlocked):
            RutorrentCreator(SimpleNamespace(node=SimpleNamespace(
                url='https://box.example/RPC2', rutorrent_url='https://other.example/rutorrent')))


if __name__ == '__main__': unittest.main()
