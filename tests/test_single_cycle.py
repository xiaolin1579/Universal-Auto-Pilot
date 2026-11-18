import contextlib
import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from auto_upload import UploadBlocked, UploadLedger
from torrent_metainfo import bencode, create_variant, _extract_torrent_info_hash
from torrent_store import MappingStore
from upload_runner import run_cycle


class Clock:
    def __init__(self): self.now = 0; self.on_sleep = lambda: None
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds; self.on_sleep()


class Client:
    name = 'seedbox'
    def __init__(self, source, inventory):
        self.source = source; self.row = inventory
        self.phases = ['checkingUP', 'pausedUP']; self.added = False
        self.proof = False; self.started = False; self.confirm_start = True
        self.calls = dict(add=0, recheck=0, resume=0)
        self.accept_add = True; self.files_valid = True; self.target_path = None

    def inventory(self, target, missing_ok=False):
        if target == self.row['hash']: return copy.deepcopy(self.row)
        if not self.added:
            if missing_ok: return None
            raise UploadBlocked('Target missing')
        state = 'uploading' if self.started and self.confirm_start else self.phases[0]
        if len(self.phases) > 1: self.phases.pop(0)
        return {**self.row, 'hash': target, 'state': state,
                'save_path': self.target_path or self.row['save_path']}

    def files(self, target):
        return [{'path': 'payload.bin' if self.files_valid else 'wrong.bin',
                 'size_bytes': 4, 'progress': 1}]

    def add_paused(self, content, job):
        assert _extract_torrent_info_hash(content) == job['target_hash']
        self.calls['add'] += 1; self.added = self.accept_add
        return self.accept_add

    def recheck(self, target): self.calls['recheck'] += 1; return True
    def recheck_complete(self, target): return self.proof
    def resume(self, target): self.calls['resume'] += 1; self.started = True; return True


class SingleCycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = MappingStore(self.root / 'mapping.json'); self.store.initialize()
        self.ledger = UploadLedger(self.store, self.root / 'artifacts')
        self.source = bencode({b'announce': b'https://bearbit.org/announce.php', b'info': {
            b'name': b'payload.bin', b'length': 4, b'piece length': 4,
            b'pieces': b'a' * 20, b'private': 1}})
        self.digest = _extract_torrent_info_hash(self.source)
        self.inventory = {'node': 'seedbox', 'hash': self.digest, 'name': 'payload.bin',
            'progress': 1, 'amount_left': 0, 'size_bytes': 4, 'state': 'pausedUP', 'save_path': '/downloads'}
        self.client = Client(self.source, self.inventory)
        self.rule = {'enable': True, 'source_zone': 'special', 'source_category': 'test',
                     'target_site': 'BITSUSE', 'target_category': '180'}
        self.metadata = {'site': 'BEARBIT', 'infohash': self.digest, 'zone': 'special',
            'zone_verified': True, 'category': 'test', 'title': 'Test payload',
            'description': 'Test description', 'details_url': 'https://bearbit.org/details.php?id=1'}
        self.announce = 'https://www.bitsuse.com/announce.php'
        self.canonical = create_variant(self.source, 'BITSUSE', 'special', '180', self.announce)
        self.spec = {'announce': self.announce, 'fields': {}, 'acknowledgements': [], 'readiness': {'ready': True}}
        self.cfg = {'enable': True, 'enable_publish': True, 'enable_seed': True, 'enable_resume': True,
                    'max_jobs_per_cycle': 1, 'recheck_wait_seconds': 10, 'rules': [self.rule]}
        self.flag = threading.Event(); self.clock = Clock()
        self.app = SimpleNamespace(CFG={'AUTO_UPLOAD': self.cfg}, BASE_DIR=str(self.root), stop_event=self.flag,
            CLIENTS=SimpleNamespace(adapter_for=lambda n: SimpleNamespace(upload_factory=object())))
        self.node = SimpleNamespace(name='seedbox', get_all_torrents_info=Mock(return_value=[self.inventory]))
        self.reader = SimpleNamespace(read_source=AsyncMock(return_value=(self.metadata, self.source)))
        self.site = SimpleNamespace(load_form=AsyncMock(side_effect=lambda *a, **k: copy.deepcopy(self.spec)),
            check_duplicates=AsyncMock(return_value={'status': 'clear', 'complete': True}),
            fill=AsyncMock(), submit=AsyncMock(return_value={'torrent_id': '10',
                'details_url': 'https://www.bitsuse.com/bs_details.php?id=10'}),
            resolve_publication=AsyncMock(return_value=None),
            download_listing=AsyncMock(return_value=self.canonical))
        self.session = SimpleNamespace(close=AsyncMock(), adapter=AsyncMock(
            side_effect=lambda site: self.reader if site == 'BEARBIT' else self.site))
        self.store.save_site_view('BEARBIT', {'1': {'hash': self.digest}})

    async def run_round(self):
        self.output = io.StringIO()
        with patch('upload_runner.ledger_for', return_value=self.ledger), \
             patch('upload_runner.client_for', return_value=self.client), \
             patch('upload_runner.UploadSession', return_value=self.session), \
             patch('auto_upload.time', self.clock), contextlib.redirect_stdout(self.output):
            result = await run_cycle(self.app, [(self.node, {})])
        self.events = [json.loads(l[l.index('{"event":'):]) for l in self.output.getvalue().splitlines() if '{"event":' in l]
        return result

    def prepare(self):
        return self.ledger.prepare(self.source, self.inventory, self.client.files(self.digest), self.metadata,
            self.rule, self.announce, readiness=self.spec['readiness'], form_schema=self.spec)

    def job(self): return next(iter(self.store.read()['upload_jobs'].values()))

    def existing_target(self, state='seed_uncertain'):
        job = self.prepare()
        self.ledger.transition(job['id'], {'prepared'}, 'submitting')
        self.ledger.record_publication(job['id'], self.site.submit.return_value)
        job = self.ledger.accept_canonical(job['id'], self.canonical)
        if state != 'verified': self.ledger.transition(job['id'], {'verified'}, state)
        self.client.added = True; self.client.phases = ['stoppedDL']
        self.install_recheck()
        return self.job()

    def install_recheck(self):
        def recheck(target):
            self.client.calls['recheck'] += 1
            self.client.phases = ['checkingUP', 'pausedUP']
            return True
        self.client.recheck = recheck

    async def test_new_job_reaches_seeding_and_counts_once(self):
        result = await self.run_round()
        self.assertEqual([r['state'] for r in result], ['seeding'])
        self.assertEqual(self.client.calls, dict(add=1, recheck=1, resume=1))
        self.site.submit.assert_awaited_once(); self.site.download_listing.assert_awaited_once()
        self.assertEqual(self.events[-1]['processed'], 1); self.assertEqual(self.events[-1]['discovered'], 1)
        self.assertEqual(self.job()['notifications']['observed_state'], 'seeding')
        self.session.close.assert_awaited_once()

    async def test_queued_prepared_job_finishes_in_the_same_round(self):
        self.prepare()
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seeding'); self.assertEqual(self.events[-1]['discovered'], 0)
        self.reader.read_source.assert_not_awaited(); self.assertEqual(self.client.calls['add'], 1)

    async def test_timeout_keeps_state_then_next_round_finishes_without_republication_or_readd(self):
        self.client.phases = ['checkingUP']
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'rechecking'); self.assertEqual(self.clock.now, 10)
        self.assertTrue(self.job()['recheck_observed'])
        self.assertTrue(any(e.get('status') == 'wait_timeout' for e in self.events))
        self.assertEqual(self.client.calls, dict(add=1, recheck=1, resume=0))
        self.client.phases = ['pausedUP']
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seeding')
        self.site.submit.assert_awaited_once(); self.assertEqual(self.client.calls, dict(add=1, recheck=1, resume=1))

    async def test_shutdown_while_waiting_does_not_resume_or_restart_any_stage(self):
        self.clock.on_sleep = self.flag.set
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'rechecking'); self.assertEqual(self.clock.now, 1)
        self.assertEqual(self.client.calls['resume'], 0); self.assertEqual(self.events[-1]['status'], 'stopped')
        self.session.close.assert_awaited_once()

    async def test_publication_uncertainty_is_only_reconciled_with_reads(self):
        self.site.submit.side_effect = TimeoutError('private response')
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'publication_uncertain'); self.assertEqual(self.client.calls['add'], 0)
        self.assertNotIn('private response', self.output.getvalue())
        self.site.resolve_publication.assert_not_awaited()
        self.assertEqual(await self.run_round(), [])
        self.site.resolve_publication.assert_awaited_once()
        self.site.submit.assert_awaited_once()
        self.assertEqual(self.job()['publication_check']['attempts'], 1)

    async def test_late_listing_is_reconciled_without_another_post(self):
        self.site.submit.side_effect = TimeoutError('private response')
        await self.run_round()
        listing = {'torrent_id': '11', 'details_url': 'https://www.bitsuse.com/bs_details.php?id=11'}
        self.site.resolve_publication.return_value = listing
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'verified')
        self.assertEqual(self.job()['listing'], listing)
        self.site.submit.assert_awaited_once(); self.site.resolve_publication.assert_awaited_once()

    async def test_reconciliation_stops_after_the_attempt_budget(self):
        self.prepare()
        self.store.update(lambda d: d['upload_jobs'][next(iter(d['upload_jobs']))].update(
            state='publication_uncertain',
            publication_check={'attempts': 6, 'found': False, 'checked_at': '2026-11-18T00:00:00+00:00'}))
        self.assertEqual(await self.run_round(), [])
        self.site.resolve_publication.assert_not_awaited()
        self.site.submit.assert_not_awaited()

    async def test_unconfirmed_add_is_not_retried_in_this_or_next_round(self):
        self.client.accept_add = False
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seed_uncertain')
        retry = await self.run_round()
        self.assertEqual(retry[0]['state'], 'seed_uncertain'); self.assertIn('Target hash is absent', retry[0]['error'])
        self.assertEqual(self.client.calls, dict(add=1, recheck=0, resume=0)); self.site.submit.assert_awaited_once()

    async def test_new_job_reuses_existing_target_without_another_add(self):
        self.client.added = True; self.client.phases = ['stoppedDL']; self.install_recheck()
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seeding')
        self.assertEqual(self.client.calls, dict(add=0, recheck=1, resume=1))
        self.assertEqual(self.job()['seed_recovery']['method'], 'existing_client_torrent')
        self.assertTrue(any(e.get('stage') == 'existing_target_rechecked' for e in self.events))

    async def test_uncertain_existing_target_at_zero_percent_is_rechecked_and_resumed(self):
        self.existing_target()
        inventory = self.client.inventory
        def pending(target, **kwargs):
            row = inventory(target, **kwargs)
            if target != self.digest and not self.client.calls['recheck']:
                row.update(progress=0, amount_left=4)
            return row
        self.client.inventory = pending
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seeding'); self.site.submit.assert_not_awaited()
        self.assertEqual(self.client.calls, dict(add=0, recheck=1, resume=1))

    async def test_unconfirmed_add_that_did_create_a_target_can_recover_next_round(self):
        def pending(content, job):
            self.client.calls['add'] += 1; self.client.added = True; self.client.phases = ['stoppedDL']
            return False
        self.client.add_paused = pending
        result = await self.run_round(); self.assertEqual(result[0]['state'], 'seed_uncertain')
        self.install_recheck()
        result = await self.run_round(); self.assertEqual(result[0]['state'], 'seeding')
        self.assertEqual(self.client.calls, dict(add=1, recheck=1, resume=1)); self.site.submit.assert_awaited_once()

    async def test_existing_target_with_wrong_path_is_not_modified(self):
        self.existing_target(); self.client.target_path = '/different'
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seed_uncertain'); self.assertIn('save path differs', result[0]['error'])
        self.assertEqual(self.client.calls, dict(add=0, recheck=0, resume=0))

    async def test_existing_target_with_wrong_manifest_is_not_modified(self):
        self.existing_target(); files = self.client.files
        self.client.files = lambda h: files(h) if h == self.digest else [{'path': 'wrong.bin', 'size_bytes': 4}]
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seed_uncertain'); self.assertIn('manifest differs', result[0]['error'])
        self.assertEqual(self.client.calls, dict(add=0, recheck=0, resume=0))

    async def test_existing_target_with_wrong_hash_is_not_modified(self):
        self.existing_target(); inventory = self.client.inventory
        def different(target, **kwargs):
            row = inventory(target, **kwargs)
            if target != self.digest: row['hash'] = 'f' * 40
            return row
        self.client.inventory = different
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seed_uncertain'); self.assertIn('hash differs', result[0]['error'])
        self.assertEqual(self.client.calls, dict(add=0, recheck=0, resume=0))

    async def test_active_existing_target_requires_explicit_reconciliation(self):
        self.existing_target(); self.client.phases = ['uploading']
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seed_uncertain'); self.assertIn('not stopped', result[0]['error'])
        self.assertEqual(self.client.calls, dict(add=0, recheck=0, resume=0))

    async def test_uncertain_target_does_not_recover_from_incomplete_source(self):
        self.existing_target(); self.client.row.update(progress=0, amount_left=4)
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seed_uncertain'); self.assertIn('error', result[0])
        self.assertEqual(self.client.calls, dict(add=0, recheck=0, resume=0))

    async def test_uncertain_target_does_not_recover_if_canonical_artifact_changed(self):
        job = self.existing_target(); Path(job['canonical_artifact']).write_bytes(self.source)
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seed_uncertain'); self.assertIn('Canonical artifact changed', result[0]['error'])
        self.assertEqual(self.client.calls, dict(add=0, recheck=0, resume=0))

    async def test_uncertain_target_respects_disabled_rule_and_seed_switch(self):
        self.existing_target(); self.cfg['enable_seed'] = False
        self.assertEqual(await self.run_round(), [])
        self.cfg['enable_seed'] = True; self.rule['enable'] = False
        self.assertEqual(await self.run_round(), [])
        self.assertEqual(self.client.calls, dict(add=0, recheck=0, resume=0))

    async def test_inflight_adding_state_is_not_automatically_reconciled(self):
        self.existing_target(state='adding')
        self.assertEqual(await self.run_round(), [])
        self.assertEqual(self.client.calls, dict(add=0, recheck=0, resume=0))

    async def test_shutdown_during_existing_target_validation_prevents_recheck(self):
        self.existing_target(); files = self.client.files
        def read(target):
            result = files(target)
            if target != self.digest: self.flag.set()
            return result
        self.client.files = read
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seed_uncertain'); self.assertEqual(self.events[-1]['status'], 'stopped')
        self.assertEqual(self.client.calls, dict(add=0, recheck=0, resume=0))

    async def test_existing_target_recheck_failure_keeps_uncertainty_without_adding(self):
        self.existing_target()
        def fail(target):
            self.client.calls['recheck'] += 1
            self.assertEqual(self.job()['state'], 'adding')
            raise TimeoutError('private API response')
        self.client.recheck = fail
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seed_uncertain'); self.assertNotIn('private API response', self.output.getvalue())
        self.assertEqual(self.client.calls, dict(add=0, recheck=1, resume=0))

    async def test_existing_target_disappearing_during_validation_does_not_trigger_add(self):
        self.existing_target(state='verified'); inventory = self.client.inventory; reads = 0
        def vanished(target, **kwargs):
            nonlocal reads
            if target != self.digest:
                reads += 1
                if reads > 1: return None
            return inventory(target, **kwargs)
        self.client.inventory = vanished
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'verified'); self.assertIn('Target hash is absent', result[0]['error'])
        self.assertEqual(self.client.calls, dict(add=0, recheck=0, resume=0))

    async def test_draft_does_not_bypass_missing_form_fields(self):
        self.spec['fields'] = {'required_field': {'required': True}}
        self.spec['readiness'] = {'ready': False, 'missing_fields': ['required_field']}
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'draft'); self.assertIn('error', result[0])
        self.site.submit.assert_not_awaited(); self.assertEqual(self.client.calls['add'], 0)

    async def test_publish_disabled_prepares_without_publishing(self):
        self.cfg['enable_publish'] = False
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'prepared'); self.site.submit.assert_not_awaited()
        self.assertEqual(self.client.calls['add'], 0)

    async def test_seed_disabled_stops_after_canonical_verification(self):
        self.cfg['enable_seed'] = False
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'verified'); self.assertEqual(self.client.calls['add'], 0)

    async def test_resume_disabled_adds_and_rechecks_without_waiting(self):
        self.cfg['enable_resume'] = False
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'rechecking'); self.assertEqual(self.clock.now, 0)
        self.assertEqual(self.client.calls, dict(add=1, recheck=1, resume=0))

    async def test_zero_wait_preserves_single_pass_recheck_behavior(self):
        self.cfg['recheck_wait_seconds'] = 0
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'rechecking'); self.assertEqual(self.clock.now, 0)
        self.assertEqual(self.client.calls['resume'], 0)

    async def test_default_wait_is_120_seconds_when_configuration_is_absent(self):
        self.cfg.pop('recheck_wait_seconds'); self.client.phases = ['checkingUP']
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'rechecking'); self.assertEqual(self.clock.now, 120)

    async def test_shutdown_after_preparation_keeps_job_without_publishing(self):
        from upload_runner import _prepare_destination
        async def prepare(*args):
            job = await _prepare_destination(*args); self.flag.set(); return job
        with patch('upload_runner._prepare_destination', side_effect=prepare):
            result = await self.run_round()
        self.assertEqual(result[0]['state'], 'prepared'); self.assertEqual(self.events[-1]['status'], 'stopped')
        self.site.submit.assert_not_awaited(); self.assertEqual(self.client.calls['add'], 0)

    async def test_unobserved_recheck_requires_client_proof_even_when_progress_is_complete(self):
        self.client.phases = ['pausedUP']
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'rechecking'); self.assertEqual(self.clock.now, 10)
        self.assertFalse(self.job()['recheck_observed']); self.assertEqual(self.client.calls['resume'], 0)

    async def test_fast_recheck_with_independent_client_proof_can_finish(self):
        self.client.phases = ['pausedUP']; self.client.proof = True
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seeding'); self.assertEqual(self.clock.now, 0)

    async def test_delayed_resume_confirmation_does_not_repeat_resume_in_a_poll_loop(self):
        self.client.confirm_start = False
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'rechecking'); self.assertEqual(self.client.calls['resume'], 1)

    async def test_asynchronous_resume_is_confirmed_in_the_same_round_without_repeating_start(self):
        self.client.confirm_start = False
        def confirm():
            if self.client.started: self.client.confirm_start = True
        self.clock.on_sleep = confirm
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'seeding'); self.assertEqual(self.client.calls['resume'], 1)
        self.assertEqual(self.clock.now, 10)

    async def test_path_changed_while_waiting_blocks_resume(self):
        self.clock.on_sleep = lambda: setattr(self.client, 'target_path', '/different')
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'rechecking'); self.assertIn('error', result[0])
        self.assertEqual(self.client.calls['resume'], 0)

    async def test_manifest_changed_while_waiting_blocks_resume(self):
        self.clock.on_sleep = lambda: setattr(self.client, 'files_valid', False)
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'rechecking'); self.assertIn('error', result[0])
        self.assertEqual(self.client.calls['resume'], 0)

    async def test_invalid_canonical_does_not_add_target(self):
        self.site.download_listing.return_value = self.source
        result = await self.run_round()
        self.assertEqual(result[0]['state'], 'verification_failed'); self.assertEqual(self.client.calls['add'], 0)

    async def test_manual_publication_gate_is_preserved(self):
        job = self.prepare()
        self.ledger.transition(job['id'], {'prepared'}, 'prepared', publication_retry={'manual_publish_required': True})
        self.assertEqual(await self.run_round(), [])
        self.site.submit.assert_not_awaited(); self.assertEqual(self.client.calls['add'], 0)

    async def test_disabled_rule_is_not_advanced(self):
        self.prepare(); self.rule['enable'] = False
        self.assertEqual(await self.run_round(), []); self.site.submit.assert_not_awaited()

    async def test_invalid_wait_configuration_is_rejected_before_opening_a_browser(self):
        for value in (True, -1, 1801, '120', None):
            with self.subTest(value=value):
                self.cfg['recheck_wait_seconds'] = value
                with self.assertRaises(UploadBlocked): await self.run_round()
        self.session.adapter.assert_not_awaited(); self.assertEqual(self.client.calls['add'], 0)

    def extra_job(self, marker):
        import torrent_metainfo
        source, _ = torrent_metainfo._decode_bencode_value(self.source)
        source[b'info'][b'pieces'] = marker.encode() * 20
        content = bencode(source)
        digest = _extract_torrent_info_hash(content)
        inventory = {**self.inventory, 'hash': digest}
        metadata = {**self.metadata, 'infohash': digest}
        return self.ledger.prepare(content, inventory, self.client.files(digest), metadata,
                                   self.rule, self.announce, readiness=self.spec['readiness'], form_schema=self.spec)

    async def test_full_blocked_queue_gives_new_work_a_turn_and_rotates_after_restart(self):
        first, second = self.extra_job('b'), self.extra_job('c')
        self.site.check_duplicates.side_effect = UploadBlocked('Duplicate search incomplete: page budget exceeded')
        result = await self.run_round()
        self.assertEqual([r['id'] for r in result], [first['id']])
        self.assertTrue(self.store.read()['auto_upload_runtime']['scheduler']['discovery_first'])
        result = await self.run_round()
        self.assertEqual(len(result), 1)
        self.assertNotIn(result[0]['id'], (first['id'], second['id']))
        self.assertEqual(self.events[-1]['discovered'], 1)
        self.assertFalse(self.store.read()['auto_upload_runtime']['scheduler']['discovery_first'])
        result = await self.run_round()
        self.assertEqual([r['id'] for r in result], [second['id']])
        self.site.submit.assert_not_awaited()
        self.assertEqual(self.client.calls['add'], 0)

    async def test_discovery_reservation_still_shares_the_existing_total_limit(self):
        old = [self.extra_job(c) for c in 'bcd']
        self.cfg['max_jobs_per_cycle'] = 2
        self.site.check_duplicates.side_effect = UploadBlocked('Duplicate search incomplete: page budget exceeded')
        first = await self.run_round()
        self.assertEqual([r['id'] for r in first], [j['id'] for j in old[:2]])
        second = await self.run_round()
        self.assertEqual(len(second), 2)
        self.assertNotIn(second[0]['id'], [j['id'] for j in old])
        self.assertEqual(second[1]['id'], old[2]['id'])
        self.assertEqual(self.events[-1]['processed'], 2)
        self.assertEqual(self.events[-1]['discovered'], 1)
        self.site.submit.assert_not_awaited()

    async def test_empty_discovery_turn_does_not_waste_the_queue_budget(self):
        first, second = self.extra_job('b'), self.extra_job('c')
        self.site.check_duplicates.side_effect = UploadBlocked('Duplicate search incomplete: page budget exceeded')
        await self.run_round()
        self.node.get_all_torrents_info.return_value = []
        result = await self.run_round()
        self.assertEqual([r['id'] for r in result], [second['id']])
        self.assertEqual(self.events[-1]['discovered'], 0)
        self.assertEqual(len(result), 1)
