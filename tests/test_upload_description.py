import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from auto_upload import UploadBlocked, upload_description
from torrent_metainfo import bencode, _extract_torrent_info_hash
from upload_sites import BrowserSite


def filled_values(script):
    return json.JSONDecoder().raw_decode(script.split('values = ', 1)[1])[0]


class UploadDescriptionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.artifact = Path(self.tmp.name) / 'payload.torrent'
        self.artifact.write_bytes(bencode({b'info': {b'name': b'payload', b'length': 1,
            b'piece length': 1, b'pieces': b'a' * 20}}))
        self.body = '[quote]Original credit and description[/quote]\n[img]https://example.org/poster.jpg[/img]'
        self.job = {'id': 'a' * 64, 'source_site': 'BEARBIT', 'target_site': 'BITSUSE',
            'target_category': '180', 'metadata': {'site': 'BEARBIT', 'title': 'Example payload', 'description': self.body},
            'rule': {'fields': {}, 'short_description': 'A short description'},
            'prepared_hash': _extract_torrent_info_hash(self.artifact.read_bytes())}

    def tab(self):
        return SimpleNamespace(evaluate=AsyncMock(return_value=True),
            send=AsyncMock(side_effect=[SimpleNamespace(node_id=1), 2, None]))

    async def test_dedbit_and_bitsuse_submit_footer_with_original_body_without_changing_job(self):
        for site in ('DEDBIT', 'BITSUSE'):
            with self.subTest(site=site):
                job = copy.deepcopy(self.job); job['target_site'] = site; original = copy.deepcopy(job)
                tab = self.tab(); adapter = BrowserSite(site, tab); adapter.form = {'acknowledgements': []}
                await adapter.fill(job, str(self.artifact))
                values = filled_values(tab.evaluate.call_args.args[0])
                self.assertTrue(values['descr'].startswith(self.body + '\n\n'))
                self.assertIn('\nเครดิตต้นฉบับ: เว็บเพื่อนบ้าน\n', values['descr'])
                self.assertTrue(values['descr'].endswith('[url=https://github.com/xiaolin1579/Universal-Auto-Pilot]Universal-Auto-Pilot[/url]'))
                self.assertEqual(values['description'], 'A short description')
                self.assertEqual(job, original)

    async def test_existing_footer_is_not_added_again_when_filling_a_queued_job(self):
        self.job['metadata']['description'] = upload_description(self.body) + '\n'
        tab = self.tab(); adapter = BrowserSite('BITSUSE', tab); adapter.form = {'acknowledgements': []}
        await adapter.fill(self.job, str(self.artifact))
        text = filled_values(tab.evaluate.call_args.args[0])['descr']
        self.assertEqual(text.count('เครดิตต้นฉบับ:'), 1)
        self.assertEqual(text.count('อัปโหลดด้วยสคริปต์:'), 1)
        self.assertTrue(text.startswith(self.body))

    def torrentdd(self):
        job = copy.deepcopy(self.job); job['target_site'] = 'TORRENTDD'; job['target_category'] = '19'
        job['rule'] = {'fields': {'poster': 'https://example.org/poster.jpg'},
                       'confirmations': {'cf1': True, 'cf2': True}}
        tab = self.tab()
        adapter = BrowserSite('TORRENTDD', tab); adapter.form = {'fields': {}}
        return job, tab, adapter

    async def test_torrentdd_submission_snapshot_contains_footer_and_keeps_source_metadata(self):
        job, tab, adapter = self.torrentdd(); original = copy.deepcopy(job)
        await adapter.fill(job, str(self.artifact))
        values = filled_values(tab.evaluate.call_args_list[0].args[0])
        self.assertTrue(values['descr'].startswith(self.body + '\n\n'))
        self.assertIn('[url=https://github.com/xiaolin1579/Universal-Auto-Pilot]Universal-Auto-Pilot[/url]', values['descr'])
        self.assertEqual(adapter._torrentdd_submission['values']['descr'], values['descr'])
        self.assertEqual(job, original)

    async def test_torrentdd_counts_footer_in_length_limit_before_touching_the_browser(self):
        job, tab, adapter = self.torrentdd(); job['metadata']['description'] = 'x' * 8990
        with self.assertRaisesRegex(UploadBlocked, 'description exceeds its supported limit'):
            await adapter.fill(job, str(self.artifact))
        tab.evaluate.assert_not_awaited(); tab.send.assert_not_awaited()
        self.assertEqual(job['metadata']['description'], 'x' * 8990)
