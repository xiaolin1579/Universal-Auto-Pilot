import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from auto_upload import UploadBlocked, validate_metainfo_size
from upload_sites import BrowserSite, upload_form


def form_html(limit):
    return ('''<form id="frmupload" method="post" enctype="multipart/form-data" action="/takeupload.php">
        <input type="hidden" name="csrf" value="test">
        <input type="file" name="file">
        <select name="type"><option value="180">Category</option></select>
        <input name="passkey" value="https://www.bitsuse.com/announce.php">
        <input type="checkbox" name="ack2"><input type="checkbox" name="ack3">'''
        + limit + '</form><script>var subcatHtml = {};</script>')


class UploadSizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_form_limit_and_boundary(self):
        spec = upload_form('BITSUSE', form_html('<input name="MAX_FILE_SIZE" value="1000000">'),
                           'https://www.bitsuse.com/bs_upload.php', '180')
        self.assertEqual(spec['max_file_size'], 1000000)
        validate_metainfo_size(1000000, spec)
        with self.assertRaisesRegex(UploadBlocked, '1009819 > 1000000'):
            validate_metainfo_size(1009819, spec)

    async def test_absent_limit_does_not_invent_a_size_cap(self):
        spec = upload_form('BITSUSE', form_html(''), 'https://www.bitsuse.com/bs_upload.php', '180')
        validate_metainfo_size(1009819, spec)

    async def test_changed_or_ambiguous_limit_is_refused(self):
        for limit in ('0', '-1', 'n/a', ''):
            with self.subTest(limit=limit), self.assertRaises(UploadBlocked):
                upload_form('BITSUSE', form_html(f'<input name="MAX_FILE_SIZE" value="{limit}">'),
                            'https://www.bitsuse.com/bs_upload.php', '180')
        with self.assertRaises(UploadBlocked):
            upload_form('BITSUSE', form_html('<input name="MAX_FILE_SIZE" value="10">' * 2),
                        'https://www.bitsuse.com/bs_upload.php', '180')

    async def test_oversized_artifact_never_reaches_browser(self):
        tab = AsyncMock()
        adapter = BrowserSite('BITSUSE', tab)
        adapter.form = {'max_file_size': 1000000}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'oversized.torrent'
            path.write_bytes(b'x' * 1009819)
            with self.assertRaisesRegex(UploadBlocked, 'exceeds destination limit'):
                await adapter.fill({}, str(path))
        tab.evaluate.assert_not_awaited()
        tab.send.assert_not_awaited()
