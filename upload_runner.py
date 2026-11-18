"""Opt-in runtime integration. No website publication unless enable_publish is true."""
from auto_upload import UploadBlocked
import asyncio
from collections import Counter
from copy import deepcopy
from time import monotonic
from upload_logging import log_event
import os
from pathlib import Path
from urllib.parse import urlsplit
from auto_upload import UploadLedger, UploadWorker, select_rule, select_rules, PUBLISH_CONFIRMATIONS
from torrent_metainfo import verify_completed
from upload_sites import BrowserSite
from upload_notifications import UploadNotifications, safe_error


def ledger_for(app):
    cfg = app.CFG.get('AUTO_UPLOAD', {})
    directory = Path(cfg.get('artifact_dir') or Path(app.BASE_DIR) / 'upload_artifacts')
    if not directory.is_absolute(): directory = Path(app.BASE_DIR) / directory
    return UploadLedger(app._mapping_store(), directory)


def client_for(app, node):
    factory = app.CLIENTS.adapter_for(node).upload_factory
    if factory is None:
        raise UploadBlocked('This client does not support inventory/files/paused-add/recheck/resume capabilities for upload')
    return factory(node)


class UploadSession:
    def __init__(self, app, profile_name='auto_upload'):
        self.app = app
        self.profile_name = profile_name
        self.browser = None
        self.adapters = {}

    async def adapter(self, site):
        if site in self.adapters: return self.adapters[site]
        config = next((s for s in self.app.CFG.get('SITE', [])
                       if s.get('name', '').upper() == site and s.get('enable') is True), None)
        if config is None: raise UploadBlocked('Site is not enabled: ' + site)
        if self.browser is None:
            log_event('browser_start', site=site)
            args = ['--proxy-server=' + os.environ['HTTPS_PROXY']] if os.environ.get('HTTPS_PROXY') else []
            self.browser = await self.app.launch_any_browser(self.profile_name, args, enable_images=True)
        tab = await self.browser.get('about:blank', new_tab=True)
        log_event('site_login', site=site)
        if not await self.app.ensure_site_logged_in(tab, config):
            raise RuntimeError('Site login failed: ' + site)
        adapter = BrowserSite(site, tab, enabled_paths={urlsplit(z.get('url', '')).path
                    for z in config.get('target_urls', []) if isinstance(z, dict) and z.get('enable', True)})
        log_event('site_ready', site=site)
        self.adapters[site] = adapter
        return adapter

    async def close(self):
        if self.browser is not None:
            log_event('browser_close')
            self.app.kill_specific_browser()
            await self.app.cleanup_profile()
            self.app.kill_xvfb()


async def prepare_job(app, session, node, source_hash, details_url, zone, target_site=None):
    client = client_for(app, node)
    source_adapter = await session.adapter('BEARBIT')
    metadata, content = await source_adapter.read_source(details_url, source_hash, zone)
    rule = select_rule(app.CFG.get('AUTO_UPLOAD', {}).get('rules', []), metadata, target_site)
    return await _prepare_destination(app, session, client, source_hash, metadata, content, rule)


async def _prepare_destination(app, session, client, source_hash, metadata, content, rule):
    stage = 'source inventory'
    log_event('prepare_stage', source_hash=source_hash, node=client.name, site=rule['target_site'], stage=stage)
    try:
        inventory, files = await asyncio.to_thread(lambda: (client.inventory(source_hash), client.files(source_hash)))
        stage = 'source manifest'
        log_event('prepare_stage', source_hash=source_hash, node=client.name, site=rule['target_site'], stage=stage)
        verify_completed(content, inventory, files)
        stage = 'destination login'
        log_event('prepare_stage', source_hash=source_hash, node=client.name, site=rule['target_site'], stage=stage)
        dest = await session.adapter(rule['target_site'])
        stage = 'destination form'
        log_event('prepare_stage', source_hash=source_hash, node=client.name, site=rule['target_site'], stage=stage)
        spec = await dest.load_form({'target_site': rule['target_site'], 'target_zone': metadata['zone'], 'metadata': metadata,
                                    'target_category': str(rule['target_category']), 'rule': rule}, require_ready=False)
        stage = 'prepare artifacts'
        log_event('prepare_stage', source_hash=source_hash, node=client.name, site=rule['target_site'], stage=stage)
        ledger = ledger_for(app)
        job = await asyncio.to_thread(ledger.prepare, content, inventory, files, metadata, rule,
                                       spec['announce'], readiness=spec['readiness'], form_schema=spec)
        from category_review import category_settings, review_categories
        settings = category_settings(app.CFG.get('AUTO_UPLOAD', {}), app.BASE_DIR)
        if settings.get('enable') is True and job['state'] in ('draft', 'prepared'):
            stage = 'category review'
            job = await review_categories(ledger, job['id'], spec, content, settings, settings['cache_dir'])
        if (app.CFG.get('AUTO_UPLOAD', {}).get('enable_publish') is True
                and job['state'] in ('draft', 'prepared')
                and not job.get('category_review', {}).get('issues')):
            stage = 'publication confirmations'
            log_event('prepare_stage', source_hash=source_hash, node=client.name, site=rule['target_site'], stage=stage)
            await asyncio.to_thread(ledger.confirm_for_publish, job['id'])
            job = ledger.get(job['id'])
        log_event('prepare_result', job)
        return job
    except Exception as exc:
        safe_reasons = {
            'Auto-upload currently supports v1 torrents only': 'v2/hybrid source unsupported; Auto Upload requires v1',
            **{text: text for text in (
                'Missing v1 piece hashes', 'Piece count does not match file sizes',
                'Symlinks need a dedicated client adapter',
                'Source is incomplete or does not match the exported torrent',
                'Source file inventory unavailable',
                'One or more source files are incomplete, including deselected files',
                'Source client file paths or sizes do not match metainfo',
                'Unsupported upload route or category', 'Unrecognized target announce',
                'Variant did not preserve payload with a distinct hash')},
        }
        reason = str(exc) if isinstance(exc, UploadBlocked) else safe_reasons.get(str(exc), type(exc).__name__)
        raise UploadBlocked(stage + ': ' + reason) from None


def enabled_rule(cfg, job):
    # Disabling or changing a rule invalidates publication of its older prepared jobs.
    def normalized(rule):
        if not isinstance(rule, dict):
            return None
        result = dict(rule)
        if cfg.get('enable_publish') is True:
            confirmations = result.get('confirmations', {})
            if (not isinstance(confirmations, dict)
                    or set(confirmations) - PUBLISH_CONFIRMATIONS.get(job['target_site'], set())
                    or any(type(value) is not bool for value in confirmations.values())):
                return None
            result.pop('confirmations', None)
        return result
    expected = normalized(job['rule'])
    return expected is not None and any(isinstance(r, dict) and r.get('enable') is True
        and normalized(r) == expected for r in cfg.get('rules', []))


async def run_cycle(app, active_nodes):
    cfg = app.CFG.get('AUTO_UPLOAD', {})
    if cfg.get('enable') is not True:
        log_event('cycle_disabled')
        return []
    recheck_wait = cfg.get('recheck_wait_seconds', 120)
    if type(recheck_wait) is not int or not 0 <= recheck_wait <= 1800:
        raise UploadBlocked('Recheck wait must be an integer from 0 to 1800 seconds')
    started = monotonic()
    ledger = ledger_for(app)
    notices = UploadNotifications(app, ledger)
    from category_review import category_settings
    worker = UploadWorker(ledger, enable_publish=cfg.get('enable_publish') is True,
                          category_review=category_settings(cfg, getattr(app, 'BASE_DIR', '.')))
    session = UploadSession(app)
    nodes = {node.name: node for node, _ in active_nodes}
    outcomes = []
    skipped = Counter()
    considered = 0
    discovered = 0
    queued = 0
    status = 'completed'
    source_cache = {}
    source_errors = set()
    def stopping():
        event = getattr(app, 'stop_event', None)
        return event is not None and event.is_set()
    limit = max(1, min(int(cfg.get('max_jobs_per_cycle', 1)), 10))
    log_event('cycle_start', nodes=len(nodes), max_jobs=limit,
              publish=cfg.get('enable_publish') is True, seed=cfg.get('enable_seed') is True,
              resume=cfg.get('enable_resume') is True, recheck_wait_seconds=recheck_wait)

    def skip(job, reason, detail=True):
        skipped[reason] += 1
        if detail:
            log_event('job_skip', job, reason=reason)

    def at_limit():
        if len(outcomes) >= limit:
            log_event('cycle_limit', processed=len(outcomes), max_jobs=limit, remaining=max(0, queued-considered))
            return True
        return False

    async def process_job(job, node, *, newly_prepared=False):
        nonlocal status
        job_started = monotonic()
        if stopping():
            status = 'stopped'
            if newly_prepared:
                notices.record(job)
                outcomes.append({'id': job['id'], 'state': job['state']})
                log_event('job_result', job, elapsed_seconds=round(monotonic()-job_started, 2))
            return
        try:
            if (cfg.get('category_review', {}).get('enable') is True and job['state'] in ('draft', 'prepared')):
                from upload_classifier import origin_requires_review
                if origin_requires_review(job):
                    job = await worker.review_categories(job['id'], None, None)
                    skip(job, 'category_origin_requires_review')
                    notices.record(job, 'Category review requires attention: origin_requires_review')
                    if newly_prepared:
                        outcomes.append({'id': job['id'], 'state': job['state']})
                        log_event('job_result', job, elapsed_seconds=round(monotonic()-job_started, 2))
                    return
            client = client_for(app, node)
            acted = newly_prepared
            if job['state'] in ('draft', 'prepared') and cfg.get('enable_publish') is True and not stopping():
                log_event('job_start', job, stage='publish')
                adapter = await session.adapter(job['target_site'])
                if not stopping():
                    acted = True
                    job = await worker.publish(job['id'], adapter, client)
            elif job['state'] in ('published', 'verification_failed') and not stopping():
                log_event('job_start', job, stage='verify_canonical')
                adapter = await session.adapter(job['target_site'])
                if not stopping():
                    acted = True
                    job = await worker.verify_publication(job['id'], adapter)
            if job['state'] in ('verified', 'rechecking', 'seed_uncertain') and cfg.get('enable_seed') is True and not stopping():
                log_event('job_start', job, stage='seed')
                acted = True
                job = await asyncio.to_thread(worker.seed, job['id'], client, cfg.get('enable_resume') is True,
                                              wait_seconds=recheck_wait, stopped=stopping)
            if stopping(): status = 'stopped'
            if acted:
                notices.record(job)
                outcomes.append({'id': job['id'], 'state': job['state']})
                log_event('job_result', job, elapsed_seconds=round(monotonic()-job_started, 2))
            elif not stopping():
                skip(job, 'seed_disabled' if job['state'] in ('verified', 'rechecking', 'seed_uncertain') else 'publish_disabled')
        except Exception as exc:
            current = ledger.get(job['id'])
            reason = safe_error(exc, current)
            log_event('job_error', current, reason=reason, error_type=type(exc).__name__,
                      elapsed_seconds=round(monotonic()-job_started, 2))
            notices.record(current, reason)
            outcome = {'id': job['id'], 'state': current['state'], 'error': reason}
            if current['state'] == 'publication_uncertain' and current.get('last_publication_failure'):
                outcome['publication_failure'] = current['last_publication_failure']
            outcomes.append(outcome)

    try:
        if stopping():
            status = 'stopped'
            return outcomes
        jobs = list(ledger.store.read()['upload_jobs'].values())
        queued = len(jobs)
        log_event('queue', queued=queued, counts=dict(Counter(job['state'] for job in jobs)))
        # Ambiguous publication/in-flight adds stay held. Uncertain seed jobs
        # can only recover an existing verified target; they never repeat add.
        for job in jobs:
            if stopping():
                status = 'stopped'
                return outcomes
            if at_limit(): return outcomes
            considered += 1
            if job['state'] in ('publication_uncertain', 'submitting', 'adding'):
                skip(job, 'reconciliation_required')
                notices.record(job)
                continue
            if job['state'] in ('seeding', 'cancelled'):
                skip(job, 'terminal_state', detail=False)
                continue
            if job.get('publication_retry', {}).get('manual_publish_required') is True:
                skip(job, 'manual_publish_required')
                notices.record(job)
                continue
            if job['state'] == 'draft' and cfg.get('enable_publish') is not True:
                skip(job, 'publish_disabled')
                continue
            node = nodes.get(job['source_node'])
            if node is None:
                skip(job, 'source_node_unavailable')
                continue
            if not enabled_rule(cfg, job):
                skip(job, 'rule_disabled_or_changed')
                continue
            await process_job(job, node)
        if at_limit(): return outcomes
        if stopping():
            status = 'stopped'
            return outcomes
        # Queue only completed hashes linked to BearBit by verified site_records.
        data = ledger.store.read()
        existing = {(j['source_hash'], j['target_site']) for j in data['upload_jobs'].values()}
        configured = [r for r in cfg.get('rules', []) if isinstance(r, dict) and r.get('enable') is True]
        zones = sorted({r.get('source_zone') for r in configured if r.get('source_zone') in ('normal', 'special')})
        for node in nodes.values():
            if stopping():
                status = 'stopped'
                return outcomes
            if app.CLIENTS.adapter_for(node).upload_factory is None:
                log_event('inventory', node=node.name, reason='client_unsupported')
                continue
            log_event('inventory', node=node.name, stage='read')
            inventory = await asyncio.to_thread(node.get_all_torrents_info)
            if not isinstance(inventory, list):
                log_event('inventory', node=node.name, reason='inventory_unavailable')
                continue
            completed = {str(t.get('hash', '')).lower() for t in inventory
                         if t.get('progress') == 1 and t.get('amount_left') == 0}
            log_event('inventory', node=node.name, total=len(inventory), completed=len(completed))
            for record in data['site_records'].values():
                if stopping():
                    status = 'stopped'
                    return outcomes
                source_hash = record.get('infohash')
                if record['site'] != 'BEARBIT' or source_hash not in completed: continue
                url = 'https://bearbit.org/details.php?id=' + record['torrent_id']
                for zone in zones:
                    if stopping():
                        status = 'stopped'
                        return outcomes
                    pending = {r.get('target_site') for r in configured if r.get('source_zone') == zone
                               and (source_hash, r.get('target_site')) not in existing}
                    if not pending: continue
                    cache_key = (source_hash, record['torrent_id'], zone)
                    if cache_key in source_errors:
                        skipped['source_already_checked'] += 1
                        continue
                    try:
                        log_event('stage', node=node.name, source_hash=source_hash, stage='read_source_' + zone)
                        client = client_for(app, node)
                        source_adapter = await session.adapter('BEARBIT')
                        if cache_key not in source_cache:
                            metadata, content = await source_adapter.read_source(url, source_hash, zone)
                            source_cache[cache_key] = (deepcopy(metadata), content)
                        metadata, content = source_cache[cache_key]
                        metadata = deepcopy(metadata)
                        rules = select_rules(cfg.get('rules', []), metadata)
                    except Exception as exc:
                        if isinstance(exc, UploadBlocked) and str(exc) in (
                                'Source category is not present in this BearBit zone',
                                'An explicit category rule is required for this BearBit category'):
                            source_errors.add(cache_key)
                        log_event('source_error', node=node.name, source_hash=source_hash,
                                  reason=safe_error(exc), error_type=type(exc).__name__)
                        continue
                    for rule in rules:
                        if stopping():
                            status = 'stopped'
                            return outcomes
                        pair = (source_hash, rule['target_site'])
                        if pair in existing: continue
                        try:
                            job = await _prepare_destination(app, session, client, source_hash, metadata, content, rule)
                        except Exception as exc:
                            log_event('source_error', node=node.name, source_hash=source_hash, site=rule['target_site'],
                                      stage='prepare_destination', reason=safe_error(exc), error_type=type(exc).__name__)
                            continue
                        discovered += 1
                        existing.add(pair)
                        await process_job(job, node, newly_prepared=True)
                        if at_limit(): return outcomes
                if at_limit(): return outcomes
        return outcomes
    except BaseException as exc:
        status = 'failed'
        log_event('cycle_error', error_type=type(exc).__name__)
        raise
    finally:
        try:
            try:
                await session.close()
            finally:
                await notices.flush()
        except BaseException as exc:
            status = 'failed'
            log_event('cycle_error', stage='cleanup', error_type=type(exc).__name__)
            raise
        finally:
            log_event('cycle_summary', status=status, queued=queued, processed=len(outcomes),
                      discovered=discovered, skipped=sum(skipped.values()), skip_reasons=dict(skipped),
                      remaining=max(0, queued-considered), elapsed_seconds=round(monotonic()-started, 2),
                      counts=dict(Counter(item['state'] for item in outcomes)))
