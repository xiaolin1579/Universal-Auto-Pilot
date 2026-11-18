import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import main as app
import remote_control as remote
from shared_user_stats import coalesce_stats, storage_key, shared_account_key


def sample(up=100, dl=50, username='shared', raw_time='2026-11-18 12:00:00'):
    return {'username': username, 'up': up, 'dl': dl, 'bonus': 5, 'ratio': 2, 'raw_time': raw_time}


class SharedStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_cache_uses_one_cumulative_sample_without_summing_or_changing_other_sites(self):
        data = {'BITSUSE': sample('2 TB', '1 TB'), 'DEDBIT': sample('1900 GB', '900 GB'), 'BEARBIT': sample(8)}
        before = copy.deepcopy(data); merged = coalesce_stats(data)
        self.assertEqual(set(merged), {'DED/BITS', 'BEARBIT'})
        self.assertEqual(merged['DED/BITS']['up'], '2 TB'); self.assertEqual(data, before)
        self.assertEqual(coalesce_stats(merged), merged)

    def test_byte_and_gigabyte_counters_are_compared_in_the_same_units(self):
        merged = coalesce_stats({'BITSUSE': sample('500 B'), 'DEDBIT': sample('1 GB')})
        self.assertEqual(merged['DED/BITS']['up'], '1 GB')

    def test_history_keeps_one_latest_sample_per_hour_and_preserves_other_hours(self):
        data = {'BITSUSE': {'2026-11-18 12:00': sample(100), '2026-11-18 11:00': sample(90)},
            'DEDBIT': {'2026-11-18 12:00': sample(105, raw_time='2026-11-18 12:45:00')}}
        merged = coalesce_stats(data, history=True)
        self.assertEqual(len(merged['DED/BITS']), 2)
        self.assertEqual(merged['DED/BITS']['2026-11-18 12:00']['up'], 105)

    def test_different_or_unknown_accounts_are_not_merged(self):
        for username in ('another', 'N/A', ''):
            with self.subTest(username=username):
                cache = {'DEDBIT': sample(), 'BITSUSE': sample(username=username)}
                self.assertEqual(coalesce_stats(cache), cache)
                history = {site: {'2026-11-18 12:00': row} for site, row in cache.items()}
                self.assertEqual(coalesce_stats(history, history=True), history)

    def test_changed_account_does_not_append_to_the_existing_shared_history(self):
        history = {'DED/BITS': {'2026-11-18 12:00': sample()}}
        self.assertEqual(storage_key('BITSUSE', history, username='another', history=True), 'BITSUSE')
        self.assertEqual(shared_account_key('DEDBIT', 'SHARED'), shared_account_key('BITSUSE', 'shared'))

    def test_diff_uses_shared_baseline_and_does_not_count_the_same_account_twice(self):
        path = self.root / 'cache.json'
        path.write_text(json.dumps({'BITSUSE': sample('1 TB', '1 TB'), 'DEDBIT': sample('2 TB', '1 TB')}))
        current = {'username': 'shared', 'up': '3 TB', 'dl': '1 TB', 'bonus': '5', 'ratio': '3'}
        with patch.object(app, 'STATS_CACHE_FILE', str(path)):
            diff = app.get_stats_diff('BITSUSE', copy.deepcopy(current))
            again = app.get_stats_diff('DEDBIT', copy.deepcopy(current))
        self.assertIn('Up:', diff); self.assertEqual(again, '')
        self.assertEqual(list(json.loads(path.read_text())), ['DED/BITS'])

    def test_shared_hourly_storage_keeps_existing_retention_limit(self):
        path = self.root / 'history.json'; start = datetime(2026, 10, 17)
        rows = {(start + timedelta(hours=i)).strftime('%Y-%m-%d %H:00'): sample(i + 100) for i in range(760)}
        keys = list(rows)
        path.write_text(json.dumps({'BITSUSE': {k: rows[k] for k in keys[:500]}, 'DEDBIT': {k: rows[k] for k in keys[300:]}}))
        with patch.object(app, 'STATS_HISTORY_FILE', str(path)), patch.object(app, 'get_now', return_value=start + timedelta(hours=760)):
            app.save_hourly_snapshot('BITSUSE', sample(900))
        merged = json.loads(path.read_text())
        self.assertEqual(list(merged), ['DED/BITS']); self.assertEqual(len(merged['DED/BITS']), 744)

    def test_daily_and_monthly_reports_accept_both_aliases_and_show_one_shared_series(self):
        path = self.root / 'history.json'
        path.write_text(json.dumps({'BITSUSE': {'2026-11-18 11:00': sample(100)},
            'DEDBIT': {'2026-11-18 12:00': sample(105)}}))
        with patch.object(remote, 'STATS_HISTORY_FILE', str(path)), patch.object(remote, 'get_now', return_value=datetime(2026, 11, 18, 13)):
            for report in (remote.get_historical_report, remote.get_monthly_report):
                a = report('BITSUSE'); b = report('DEDBIT'); shared = report('DED/BITS')
                self.assertEqual(a, b); self.assertEqual(b, shared)
                self.assertIn('DED/BITS', shared); self.assertNotIn('Report Error', shared)


class SharedStatsCollectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = app.BotContext([], None, set(), set(), {})
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.reset = self.stack.enter_context(patch.object(app, 'reset_bot_config_to_default', new=Mock()))
        self.diff = self.stack.enter_context(patch.object(app, 'async_get_stats_diff', new=AsyncMock(return_value='')))
        self.snapshot = self.stack.enter_context(patch.object(app, 'save_hourly_snapshot', new=Mock()))
        self.stack.enter_context(patch('main.asyncio.sleep', new=AsyncMock()))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def config(self, site, username='shared'):
        return {'name': site, 'base_url': 'https://www.dedbit.com', 'username': username, 'password': 'private'}

    def page(self, username='shared', complete=True):
        header = '<a href="userdetails.php?id=999">someoneelse</a><a href="userdetails.php?id=1">' + username + '</a>'
        profile = 'Ratio: 2 Uploaded: 2 TB Downloaded: 1 TB Bonus: 5' if complete else 'Loading'
        return SimpleNamespace(url='https://www.dedbit.com/index.php',
            get_content=AsyncMock(side_effect=[header, profile]), get=AsyncMock())

    async def test_same_account_fetches_and_records_once_but_keeps_site_maintenance(self):
        first = self.page(); second = self.page()
        report = await app.get_site_stats(first, self.config('BITSUSE'), self.ctx)
        duplicate = await app.get_site_stats(second, self.config('DEDBIT'), self.ctx)
        self.assertIn('(DED/BITS)', report); self.assertEqual(duplicate, '')
        first.get.assert_awaited_once_with('https://www.dedbit.com/userdetails.php?id=1')
        second.get_content.assert_not_awaited(); self.snapshot.assert_called_once(); self.diff.assert_awaited_once()
        self.assertEqual(self.reset.call_count, 2)

    async def test_failed_first_read_can_retry_from_the_other_site(self):
        result = await app.get_site_stats(self.page(complete=False), self.config('BITSUSE'), self.ctx)
        self.assertIn('สถิติไม่ครบถ้วน', result); self.assertEqual(self.ctx.account_stats_seen, set())
        result = await app.get_site_stats(self.page(), self.config('DEDBIT'), self.ctx)
        self.assertIn('(DED/BITS)', result); self.snapshot.assert_called_once()

    async def test_different_accounts_are_not_skipped_as_duplicate_reads(self):
        await app.get_site_stats(self.page('first'), self.config('BITSUSE', 'first'), self.ctx)
        await app.get_site_stats(self.page('second'), self.config('DEDBIT', 'second'), self.ctx)
        self.assertEqual(self.snapshot.call_count, 2)

    async def test_dedbit_login_uses_the_requested_account(self):
        page = SimpleNamespace(url='https://www.bitsuse.com/bs_browse.php', get=AsyncMock(),
            select=AsyncMock(return_value=None), find=AsyncMock(return_value=None))
        with patch.object(app, 'CFG', {'SITE': [self.config('DEDBIT', 'another')]}), \
             patch.object(app, 'universal_login', new=AsyncMock(return_value=True)) as login:
            self.assertTrue(await app.ensure_dedbit_logged_in(page, self.config('BITSUSE')))
        used = login.call_args.args[1]
        self.assertEqual(used['username'], 'shared'); self.assertEqual(used['name'], 'DEDBIT')
        self.assertEqual(used['base_url'], 'https://www.dedbit.com')
