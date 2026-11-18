import contextlib
import io
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import test_single_cycle as fixtures
from upload_notifications import UploadNotifications


class SummaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.SingleCycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger = self.fixture.ledger
        self.job = self.fixture.prepare()
        self.app = self.fixture.app
        self.app.send_notify = AsyncMock(return_value=True)
        self.now = datetime(2026, 11, 18, 10, tzinfo=timezone.utc)
        self.addCleanup(patch.stopall)
        patch('upload_notifications.utcnow', side_effect=lambda: self.now).start()
        patch('upload_notifications.stamp', side_effect=lambda value=None: (value or self.now).isoformat()).start()
        self.notices = UploadNotifications(self.app, self.ledger)

    def note(self):
        return self.ledger.store.read().get('auto_upload_runtime', {}).get('notifications', {})

    def record(self, **kw):
        self.notices.record_summary(processed=1, discovered=0, skipped={}, status='completed', **kw)

    async def test_summary_is_throttled_across_restarts_and_repeats_when_still_held(self):
        self.record(); await self.notices.flush()
        self.assertEqual(self.app.send_notify.await_count, 1)
        self.assertNotIn('pending', self.note())
        self.now += timedelta(seconds=3599)
        self.notices = UploadNotifications(self.app, self.ledger)
        self.record(); await self.notices.flush()
        self.assertEqual(self.app.send_notify.await_count, 1)
        self.now += timedelta(seconds=1)
        self.record(); await self.notices.flush()
        self.assertEqual(self.app.send_notify.await_count, 2)

    async def test_failed_delivery_is_durable_and_reports_only_confirmed_delivery(self):
        self.app.send_notify.return_value = False
        self.record()
        output = io.StringIO()
        with contextlib.redirect_stdout(output): await self.notices.flush()
        self.assertIn('not_delivered', output.getvalue())
        self.assertIn('pending', self.note()); self.assertNotIn('sent_at', self.note())
        self.app.send_notify.return_value = True
        self.notices = UploadNotifications(self.app, self.ledger)
        with contextlib.redirect_stdout(output): await self.notices.flush()
        self.assertNotIn('pending', self.note())
        self.assertIn('sent_at', self.note())
        self.assertEqual(self.ledger.get(self.job['id'])['state'], 'prepared')

    async def test_summary_contains_hold_reasons_without_job_titles_or_credentials(self):
        self.ledger.transition(self.job['id'], {'prepared'}, 'draft',
            readiness={'missing_fields': ['c100cat']},
            category_review={'version': 1, 'status': 'review_required', 'issues': ['origin_requires_review'],
                             'applied_fields': {}, 'auto_fix': True, 'job_fingerprint': 'a' * 64},
            duplicate_check={'status': 'unknown'},
            metadata={'title': 'private title token=secret'})
        self.record()
        message = self.note()['pending']['message']
        self.assertIn('ค้าง 1 งาน', message)
        for phrase in ('ข้อมูลฟอร์ม', 'หมวดหมู่ต้องตรวจสอบ', 'การตรวจงานซ้ำ'):
            self.assertIn(phrase, message)
        self.assertNotIn('private title', message); self.assertNotIn('secret', message)

    async def test_disabling_summary_preserves_job_notices_and_discards_stale_summary(self):
        self.record(); self.assertIn('pending', self.note())
        self.app.CFG['AUTO_UPLOAD']['summary_interval_seconds'] = 0
        self.record(); self.notices.record(self.job)
        await self.notices.flush()
        self.assertEqual(self.app.send_notify.await_count, 1)
        self.assertIn('พร้อมอัปโหลด', self.app.send_notify.await_args.args[0])
        self.assertNotIn('pending', self.note())

    async def test_no_summary_when_all_jobs_are_terminal(self):
        self.record()
        self.ledger.transition(self.job['id'], {'prepared'}, 'cancelled')
        self.record(); await self.notices.flush()
        self.app.send_notify.assert_not_awaited()

    async def test_job_error_deduplication_is_unchanged(self):
        self.notices.record(self.job, 'Duplicate search incomplete: page budget exceeded')
        await self.notices.flush()
        self.notices = UploadNotifications(self.app, self.ledger)
        self.notices.record(self.job, 'Duplicate search incomplete: page budget exceeded')
        await self.notices.flush()
        self.assertEqual(self.app.send_notify.await_count, 1)

    async def test_disabling_summary_is_respected_by_direct_command_flush(self):
        self.record()
        self.app.CFG['AUTO_UPLOAD']['summary_interval_seconds'] = 0
        await self.notices.flush()
        self.app.send_notify.assert_not_awaited()
