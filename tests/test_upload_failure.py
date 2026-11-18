import unittest
from unittest.mock import AsyncMock, patch

from auto_upload import UploadBlocked, UploadRejected, publication_failure
from upload_sites import BrowserSite, known_upload_rejection


class UploadFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_server_failure_stays_uncertain(self):
        # Server failures can follow a partly completed POST: never treat an
        # unrecognized failure page as permission to submit again.
        adapter = BrowserSite('BITSUSE', AsyncMock())
        adapter.tab.evaluate.side_effect = [None, 'https://www.bitsuse.com/takeupload.php']
        adapter.tab.get_content.return_value = (
            '<title>Upload failed!</title><h1>Upload failed!</h1>'
            '<p>An unrecognized server error with token=secret</p>')
        with patch('upload_sites.asyncio.sleep', new=AsyncMock()):
            with self.assertRaises(UploadBlocked) as result:
                await adapter.submit({})
        self.assertNotIsInstance(result.exception, UploadRejected)
        failure = publication_failure(adapter, result.exception)
        self.assertEqual(failure['response_code'], 'upload_failed_unclassified')
        self.assertEqual(failure['stage'], 'await_result')
        self.assertNotIn('secret', str(failure))
        self.assertNotIn('secret', str(result.exception))

    async def test_confirmed_title_limit_remains_explicit_rejection(self):
        error = known_upload_rejection(
            '<title>Upload failed!</title><h1>Upload failed!</h1>'
            '<p>Torrent name รับได้ไม่เกิน 80</p>')
        self.assertIsInstance(error, UploadRejected)
        self.assertEqual((error.code, error.title_limit), ('title_too_long', 80))

    async def test_failure_text_without_failure_heading_is_not_rejection(self):
        self.assertIsNone(known_upload_rejection(
            '<title>Upload</title><p>Upload failed! Torrent name รับได้ไม่เกิน 80</p>'))

    async def test_diagnostics_only_accept_controlled_codes(self):
        adapter = type('Adapter', (), {'_publication_context': {
            'response_code': 'token=secret', 'page_path': '/takeupload.php?token=secret',
            'stage': 'token=secret'}})()
        failure = publication_failure(adapter, RuntimeError('token=secret'))
        self.assertEqual(failure['response_code'], 'unknown')
        self.assertEqual(failure['page_path'], 'unknown')
        self.assertNotIn('secret', str(failure))


if __name__ == '__main__':
    unittest.main()
