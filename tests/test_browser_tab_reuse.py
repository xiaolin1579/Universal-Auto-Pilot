import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import main as app
from upload_runner import UploadSession


class BlankPageTests(unittest.TestCase):
    def test_reuses_initial_tab(self):
        browser = SimpleNamespace(get=AsyncMock(return_value='page'))
        page = asyncio.run(app.get_blank_page(browser))
        self.assertEqual(page, 'page')
        browser.get.assert_awaited_once_with('about:blank')


class UploadSessionTabTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self, browser):
        sites = [
            {'name': name, 'enable': True, 'base_url': f'https://{name.lower()}.example',
             'target_urls': []}
            for name in ('BEARBIT', 'DEDBIT')
        ]
        return SimpleNamespace(
            CFG={'SITE': sites},
            launch_any_browser=AsyncMock(return_value=browser),
            ensure_site_logged_in=AsyncMock(return_value=True),
            kill_specific_browser=Mock(return_value=True),
            cleanup_profile=AsyncMock(return_value=True),
            kill_xvfb=Mock(),
        )

    async def test_first_site_reuses_initial_tab_and_later_sites_open_new_tabs(self):
        pages = [object(), object()]
        browser = SimpleNamespace(get=AsyncMock(side_effect=pages))
        target = self.make_app(browser)
        session = UploadSession(target, 'test_profile')

        with patch('upload_runner.BrowserSite', side_effect=lambda site, tab, **kw:
                   SimpleNamespace(site=site, tab=tab)):
            first = await session.adapter('BEARBIT')
            second = await session.adapter('DEDBIT')

        self.assertEqual(
            browser.get.await_args_list,
            [call('about:blank'), call('about:blank', new_tab=True)]
        )
        self.assertIs(first.tab, pages[0])
        self.assertIs(second.tab, pages[1])

    async def test_relaunching_browser_reuses_initial_tab_again(self):
        pages = [object(), object(), object()]
        browser = SimpleNamespace(get=AsyncMock(side_effect=pages))
        target = self.make_app(browser)
        session = UploadSession(target, 'test_profile')

        with patch('upload_runner.BrowserSite', side_effect=lambda site, tab, **kw:
                   SimpleNamespace(site=site, tab=tab)):
            await session.adapter('BEARBIT')
            session.browser = None
            await session.adapter('DEDBIT')

        self.assertEqual(
            browser.get.await_args_list,
            [call('about:blank'), call('about:blank')]
        )


if __name__ == '__main__':
    unittest.main()
