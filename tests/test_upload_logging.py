import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from upload_logging import log_event
from upload_runner import run_cycle


def events(output):
    return [json.loads(line[line.index('{"event":'):]) for line in output.splitlines() if '{"event":' in line]


def job(key='a', state='publication_uncertain'):
    return {'id':key*64, 'state':state, 'source_node':'OnlyOne', 'target_site':'BITSUSE',
            'source_hash':'b'*40, 'rule':{'enable':True}, 'metadata':{}}


class LoggingTests(unittest.TestCase):
    def test_redacts_secrets_and_ignores_unapproved_fields(self):
        output=io.StringIO()
        row={**job(), 'password':'hidden-job-password','metadata':{'cookie':'hidden-metadata'}}
        with redirect_stdout(output):
            log_event('job_error',row,
                reason='Failed https://example.com/announce.php?passkey=abc password="long secret" csrf=abcdef',
                cookie='hidden-cookie',announce='hidden-announce',config={'token':'hidden-config'})
        text=output.getvalue()
        for secret in ('hidden-job-password','hidden-metadata','hidden-cookie','hidden-announce','hidden-config','long secret','abcdef','example.com'):
            self.assertNotIn(secret,text)
        record=events(text)[0]
        self.assertEqual(record['job'],'a'*12)
        self.assertIn('[REDACTED]',record['reason'])
        self.assertEqual(len(text.splitlines()),1)
        self.assertRegex(text,r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+07:00')

    def test_authorization_header_and_log_injection_are_removed(self):
        output=io.StringIO()
        with redirect_stdout(output):
            log_event('stage',reason='Authorization: Bearer invisible\npassword=invisible2',node='name\nFAKE LOG')
        self.assertNotIn('invisible',output.getvalue())
        self.assertEqual(len(output.getvalue().splitlines()),1)

    def test_logging_failure_cannot_break_a_post_result(self):
        with patch('builtins.print',side_effect=BrokenPipeError()):
            log_event('state_change',job(state='published'))

    def test_config_or_html_is_never_dumped_as_a_field(self):
        output=io.StringIO()
        with redirect_stdout(output):
            log_event('stage',reason={'password':'secret'},html='<input value=secret>',
                      counts={'verified':2,'unknown key':9,'nested':{'token':'secret'}})
        record=events(output.getvalue())[0]
        self.assertNotIn('secret',output.getvalue())
        self.assertEqual(record['counts'],{'verified':2})


class CycleLoggingTests(unittest.IsolatedAsyncioTestCase):
    def context(self,rows,cfg=None):
        app=SimpleNamespace(CFG={'AUTO_UPLOAD':{'enable':True,**(cfg or {})}})
        ledger=Mock();ledger.store.read.return_value={'upload_jobs':{j['id']:j for j in rows},'site_records':{}}
        ledger.get.side_effect=lambda key: next(j for j in rows if j['id']==key)
        notices=Mock();notices.flush=AsyncMock()
        session=Mock();session.close=AsyncMock();session.adapter=AsyncMock()
        return app,ledger,notices,session

    async def test_uncertain_job_logs_reason_without_retry(self):
        app,ledger,notices,session=self.context([job()])
        worker=Mock();worker.publish=AsyncMock();worker.seed=Mock()
        output=io.StringIO()
        with patch('upload_runner.ledger_for',return_value=ledger), \
             patch('upload_runner.UploadNotifications',return_value=notices), \
             patch('upload_runner.UploadSession',return_value=session), \
             patch('upload_runner.UploadWorker',return_value=worker),redirect_stdout(output):
            result=await run_cycle(app,[])
        self.assertEqual(result,[]);worker.publish.assert_not_awaited();worker.seed.assert_not_called()
        record=events(output.getvalue())
        self.assertEqual(next(r for r in record if r['event']=='job_skip')['reason'],'reconciliation_required')
        summary=record[-1]
        self.assertEqual(summary['event'],'cycle_summary');self.assertEqual(summary['skipped'],1)
        self.assertEqual(summary['processed'],0);self.assertIn('elapsed_seconds',summary)
        session.close.assert_awaited_once();notices.flush.assert_awaited_once()

    async def test_error_and_limit_are_visible_without_leaking_exception_text(self):
        rows=[job('a','prepared'),job('c','verified')]
        app,ledger,notices,session=self.context(rows,{'enable_publish':True,'max_jobs_per_cycle':1,'rules':[{'enable':True}]})
        worker=Mock();worker.publish=AsyncMock(side_effect=PermissionError('password=do-not-log-this'))
        worker.seed=Mock();node=SimpleNamespace(name='OnlyOne')
        output=io.StringIO()
        with patch('upload_runner.ledger_for',return_value=ledger), \
             patch('upload_runner.UploadNotifications',return_value=notices), \
             patch('upload_runner.UploadSession',return_value=session), \
             patch('upload_runner.UploadWorker',return_value=worker), \
             patch('upload_runner.client_for',return_value=Mock()),redirect_stdout(output):
            result=await run_cycle(app,[(node,{})])
        record=events(output.getvalue())
        self.assertEqual(result[0]['error'],'PermissionError')
        self.assertNotIn('do-not-log-this',output.getvalue())
        error=next(r for r in record if r['event']=='job_error')
        self.assertEqual(error['error_type'],'PermissionError')
        self.assertTrue(any(r['event']=='cycle_limit' for r in record))
        self.assertEqual(record[-1]['processed'],1);self.assertEqual(record[-1]['remaining'],1)
        worker.publish.assert_awaited_once();worker.seed.assert_not_called()

    async def test_cleanup_error_still_produces_failed_summary(self):
        app,ledger,notices,session=self.context([])
        session.close.side_effect=RuntimeError('token=hidden-cleanup-error')
        output=io.StringIO()
        with patch('upload_runner.ledger_for',return_value=ledger), \
             patch('upload_runner.UploadNotifications',return_value=notices), \
             patch('upload_runner.UploadSession',return_value=session),redirect_stdout(output):
            with self.assertRaises(RuntimeError): await run_cycle(app,[])
        self.assertEqual(events(output.getvalue())[-1]['status'],'failed')
        self.assertNotIn('hidden-cleanup-error',output.getvalue());notices.flush.assert_awaited_once()

    async def test_disabled_cycle_does_not_open_mapping_or_clients(self):
        output=io.StringIO()
        with patch('upload_runner.ledger_for',side_effect=AssertionError('must not run')),redirect_stdout(output):
            result=await run_cycle(SimpleNamespace(CFG={'AUTO_UPLOAD':{'enable':False}}),[])
        self.assertEqual(result,[])
        self.assertEqual(events(output.getvalue())[0]['event'],'cycle_disabled')


if __name__=='__main__': unittest.main()
