import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bs4 import BeautifulSoup
import main as app
import remote_control as remote
from user_stats_reading import profile_transfer, counter_delta


PROFILE = '''Uploaded: 1.147 PB Downloaded: 133.974 TB Ratio: 8.770 Bonus: 235
<table><tr><td>Uploaded</td><td>1.14748 PB <small>(220.53 GB per day)</small></td></tr>
<tr><td>Downloaded</td><td>133.974 TB <small>(25.14 GB per day)</small></td></tr></table>'''


def sample(up, clock, *, detailed=False):
    row = {'username': 'example', 'ratio': 8.770, 'up': app.parse_size(up),
           'dl': app.parse_size('133.974 TB'), 'bonus': 235, 'raw_time': clock}
    if detailed: row.update(up_precision='PB:5', up_display=up, up_source='profile_row')
    return row


class ProfilePrecisionTests(unittest.TestCase):
    def test_profile_total_is_selected_ahead_of_coarse_header_and_average(self):
        soup = BeautifulSoup(PROFILE, 'html.parser')
        self.assertEqual(profile_transfer(soup, 'up'), {'value': '1.14748 PB', 'precision': 'PB:5', 'source': 'profile_row'})
        self.assertEqual(profile_transfer(soup, 'dl')['value'], '133.974 TB')

    def test_unknown_or_empty_profile_rows_do_not_guess_a_total(self):
        for content in ('Uploaded: 1.147 PB', '<tr><td>Uploaded</td><td></td></tr>',
                        '<tr><td>Average Uploaded</td><td>220 GB</td></tr>'):
            self.assertIsNone(profile_transfer(BeautifulSoup(content, 'html.parser'), 'up'))

    def test_precision_transition_is_not_reported_as_transfer_gain(self):
        before = sample('1.147 PB', '2026-11-18 18:00:00')
        after = sample('1.14748 PB', '2026-11-18 19:00:00', detailed=True)
        self.assertIsNone(counter_delta(before, after, 'up'))

    def test_diff_reanchors_then_reports_profile_increment_in_gigabytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.json'
            old = {'username': 'example', 'up': '1.147 PB', 'dl': '133.974 TB', 'bonus': '235', 'ratio': '8.770'}
            path.write_text(json.dumps({'BEARBIT': old}))
            precise = {**old, 'up': '1.14748 PB', 'up_precision': 'PB:5', 'up_display': '1.14748 PB'}
            with patch.object(app, 'STATS_CACHE_FILE', str(path)):
                first = app.get_stats_diff('BEARBIT', precise)
                next_value = {**precise, 'up': '1.14749 PB', 'up_display': '1.14749 PB'}
                second = app.get_stats_diff('BEARBIT', next_value)
            self.assertIn('ความละเอียดข้อมูลเปลี่ยน', first); self.assertNotIn('Up:', first)
            self.assertIn('10.49 GB', second)

    def test_hourly_anomaly_guard_keeps_matching_precision_and_display(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory) / 'history.json'
            old = sample('1.14748 PB', '2026-11-18 18:00:00', detailed=True)
            path.write_text(json.dumps({'BEARBIT': {'2026-11-18 18:00': old}}))
            bad = {'username': 'example', 'up': 0, 'dl': old['dl'], 'ratio': 0, 'bonus': 235,
                   'up_precision': 'B:0', 'up_display': '0 B'}
            with patch.object(app, 'STATS_HISTORY_FILE', str(path)), patch.object(app, 'get_now', return_value=datetime(2026, 11, 18, 19)):
                app.save_hourly_snapshot('BEARBIT', bad)
            saved = json.loads(path.read_text())['BEARBIT']['2026-11-18 19:00']
            self.assertEqual(saved['up'], old['up']); self.assertEqual(saved['up_precision'], 'PB:5')
            self.assertEqual(saved['up_display'], '1.14748 PB')


class ProfileCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_collector_and_snapshot_keep_profile_precision(self):
        header = '<a href="userdetails.php?id=1">example</a>'
        page = SimpleNamespace(url='https://bearbit.org/index.php', get=AsyncMock(),
            get_content=AsyncMock(side_effect=[header, header, PROFILE]))
        context = app.BotContext([], None, set(), set(), {})
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch('main.asyncio.sleep', new=AsyncMock()))
            for name in ('clear_bearbit_notifications', 'sync_hr_with_web', 'sync_seed_quest_with_web', 'auto_vote_snatched'):
                stack.enter_context(patch.object(app, name, new=AsyncMock()))
            stack.enter_context(patch.object(app, 'get_bearbit_item_status', return_value='NONE'))
            stack.enter_context(patch.object(app, 'async_get_stats_diff', new=AsyncMock(return_value='')))
            snapshot = stack.enter_context(patch.object(app, 'save_hourly_snapshot', new=Mock()))
            report = await app.get_site_stats(page, {'name': 'BEARBIT', 'base_url': 'https://bearbit.org', 'username': 'example'}, context)
        self.assertIn('1.14748 PB', report)
        saved = snapshot.call_args.args[1]
        self.assertEqual(saved['up_precision'], 'PB:5'); self.assertEqual(saved['up_display'], '1.14748 PB')
        self.assertEqual(saved['up'], app.parse_size('1.14748 PB'))


class PrecisionReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'history.json'
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(remote, 'STATS_HISTORY_FILE', str(self.path)))
        self.stack.enter_context(patch.object(remote, 'get_now', return_value=datetime(2026, 11, 18, 20)))
        self.history = {'2026-11-18 00:00': sample('1.147 PB', '2026-11-18 00:04:00'),
            '2026-11-18 18:00': sample('1.14748 PB', '2026-11-18 18:39:00', detailed=True)}

    def save(self): self.path.write_text(json.dumps({'BEARBIT': self.history}))

    def test_report_labels_real_sample_interval_and_does_not_invent_a_precision_jump(self):
        self.save(); report = remote.get_historical_report('BEARBIT')
        self.assertIn('Since Last Snapshot', report); self.assertIn('00:04–18:39', report)
        self.assertNotIn('Last 1 Hour', report); self.assertIn('ข้อมูลวันนี้: 2 จุด', report)
        self.assertIn('1.14748 PB', report); self.assertNotIn('503.32 GB', report)
        self.assertIn('ความละเอียดใหม่', report)
        monthly = remote.get_monthly_report('BEARBIT')
        self.assertNotIn('503.32 GB', monthly); self.assertIn('ความละเอียดใหม่', monthly)

    def test_daily_and_monthly_gain_use_comparable_profile_samples(self):
        self.history['2026-11-18 19:00'] = sample('1.14749 PB', '2026-11-18 19:40:00', detailed=True)
        self.save()
        for fn in (remote.get_historical_report, remote.get_monthly_report):
            report = fn('BEARBIT')
            self.assertIn('10.49 GB', report); self.assertNotIn('Report Error', report)
            self.assertNotIn('503.32 GB', report)

    def test_legacy_history_remains_readable_at_its_original_precision(self):
        self.history = {'2026-11-18 00:00': sample('1.146 PB', '2026-11-18 00:04:00'),
                        '2026-11-18 18:00': sample('1.147 PB', '2026-11-18 18:39:00')}
        self.save(); report = remote.get_historical_report('BEARBIT')
        self.assertIn('1.02 TB', report); self.assertNotIn('ความละเอียดใหม่', report)
