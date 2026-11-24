import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import main as app
from upload_runner import UploadSession, upload_browser_needs_images


class BlankPageTests(unittest.TestCase):
    def test_reuses_initial_tab(self):
        browser = SimpleNamespace(get=AsyncMock(return_value='page'))
        page = asyncio.run(app.get_blank_page(browser))
        self.assertEqual(page, 'page')
        browser.get.assert_awaited_once_with('about:blank')


class UploadBrowserImageTests(unittest.TestCase):
    def test_images_are_off_without_an_ocr_site(self):
        target = SimpleNamespace(CFG={'SITE': [{'name': 'BEARBIT', 'enable': True}]})
        self.assertFalse(upload_browser_needs_images(target))

    def test_unlimitz_or_explicit_setting_enables_images(self):
        target = SimpleNamespace(CFG={'SITE': [{'name': 'UNLIMITZ', 'enable': True}]})
        self.assertTrue(upload_browser_needs_images(target))
        target.CFG = {'SITE': [{'name': 'BEARBIT', 'enable': True}],
                      'SETTING': {'browser_upload_images': True}}
        self.assertTrue(upload_browser_needs_images(target))


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

    async def test_browser_launch_disables_images_for_normal_sites(self):
        browser = SimpleNamespace(get=AsyncMock(return_value=object()))
        target = self.make_app(browser)
        session = UploadSession(target, 'test_profile')
        with patch('upload_runner.BrowserSite', side_effect=lambda site, tab, **kw:
                   SimpleNamespace(site=site, tab=tab)):
            await session.adapter('BEARBIT')
        self.assertIs(target.launch_any_browser.await_args.kwargs['enable_images'], False)

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
