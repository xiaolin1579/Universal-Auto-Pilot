import asyncio
import contextlib
import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main as app


def target(name):
    return {'source_site': name, 'keyword': name,
            'target_size_bytes': 1, 'source_zone_verified': False}


class ScanBrowserStateTests(unittest.TestCase):
    def test_holds_browser_page_and_session(self):
        state = app.ScanBrowserState()
        self.assertIsNone(state.browser)
        browser, page, session = object(), object(), object()
        state.update(browser, page, session)
        self.assertIs(state.browser, browser)
        self.assertIs(state.site_page, page)
        self.assertIs(state.dl_session, session)


class CrossSeedReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_targets_share_one_browser_state(self):
        targets = [target('a'), target('b'), target('c')]
        states = []

        async def scan(**kwargs):
            states.append(kwargs['browser_state'])
            return [kwargs['cross_seed_target']['source_site']]

        with patch.object(app, 'scrape_site_search_nodriver', new=AsyncMock(side_effect=scan)) as scrape, \
                contextlib.redirect_stdout(io.StringIO()):
            matches = await app.scan_cross_seed_targets(
                targets, {}, {}, [], threading.Event(), {}, None
            )

        self.assertEqual(matches, ['a', 'b', 'c'])
        self.assertEqual(scrape.await_count, 3)
        self.assertEqual(len(states), 3)
        self.assertTrue(all(s is states[0] for s in states))
        self.assertIsInstance(states[0], app.ScanBrowserState)

    async def test_stop_event_stops_before_next_target(self):
        flag = threading.Event()
        targets = [target('a'), target('b'), target('c')]

        async def scan(**kwargs):
            flag.set()
            return []

        with patch.object(app, 'scrape_site_search_nodriver', new=AsyncMock(side_effect=scan)) as scrape, \
                contextlib.redirect_stdout(io.StringIO()):
            matches = await app.scan_cross_seed_targets(
                targets, {}, {}, [], flag, {}, None
            )

        self.assertEqual(matches, [])
        scrape.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
