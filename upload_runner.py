"""Opt-in runtime integration. No website publication unless enable_publish is true."""
from auto_upload import UploadBlocked
import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit
from auto_upload import UploadLedger, UploadWorker, select_rule, select_rules, PUBLISH_CONFIRMATIONS
from upload_sites import BrowserSite


def ledger_for(app):
    cfg = app.CFG.get('AUTO_UPLOAD', {})
    directory = Path(cfg.get('artifact_dir') or Path(app.BASE_DIR) / 'upload_artifacts')
    if not directory.is_absolute(): directory = Path(app.BASE_DIR) / directory
    return UploadLedger(app._mapping_store(), directory)


def client_for(app, node):
    factory = app.CLIENTS.adapter_for(node).upload_factory
    if factory is None:
        raise UploadBlocked('This client does not support export/manifest/paused-add/recheck capabilities for upload')
    return factory(node)


class UploadSession:
    def __init__(self, app):
        self.app = app
        self.browser = None
        self.adapters = {}

    async def adapter(self, site):
        if site in self.adapters: return self.adapters[site]
        config = next((s for s in self.app.CFG.get('SITE', [])
                       if s.get('name', '').upper() == site and s.get('enable') is True), None)
        if config is None: raise UploadBlocked('Site is not enabled: ' + site)
        if self.browser is None:
            args = ['--proxy-server=' + os.environ['HTTPS_PROXY']] if os.environ.get('HTTPS_PROXY') else []
            self.browser = await self.app.launch_any_browser('auto_upload', args, enable_images=True)
        tab = await self.browser.get('about:blank', new_tab=True)
        if not await self.app.ensure_site_logged_in(tab, config):
            raise RuntimeError('Site login failed: ' + site)
        adapter = BrowserSite(site, tab, enabled_paths={urlsplit(z.get('url', '')).path
                    for z in config.get('target_urls', []) if isinstance(z, dict) and z.get('enable', True)})
        self.adapters[site] = adapter
        return adapter

    async def close(self):
        if self.browser is not None:
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
    # Destination form must be current and compatible before an artifact is prepared.
    dest = await session.adapter(rule['target_site'])
    spec = await dest.load_form({'target_site': rule['target_site'], 'target_zone': metadata['zone'], 'metadata': metadata,
                                'target_category': str(rule['target_category']), 'rule': rule}, require_ready=False)
    inventory, files = await asyncio.to_thread(lambda: (client.inventory(source_hash), client.files(source_hash)))
    ledger = ledger_for(app)
    job = await asyncio.to_thread(ledger.prepare, content, inventory, files, metadata, rule,
                                   spec['announce'], readiness=spec['readiness'], form_schema=spec)
    if (app.CFG.get('AUTO_UPLOAD', {}).get('enable_publish') is True
            and job['state'] in ('draft', 'prepared')):
        await asyncio.to_thread(ledger.confirm_for_publish, job['id'])
        job = ledger.get(job['id'])
    return job


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
    if cfg.get('enable') is not True: return []
    ledger = ledger_for(app)
    worker = UploadWorker(ledger, enable_publish=cfg.get('enable_publish') is True)
    session = UploadSession(app)
    nodes = {node.name: node for node, _ in active_nodes}
    outcomes = []
    limit = max(1, min(int(cfg.get('max_jobs_per_cycle', 1)), 10))
    try:
        # Continue existing durable jobs first. Ambiguous POST/add states never auto-retry.
        for job in ledger.store.read()['upload_jobs'].values():
            if len(outcomes) >= limit: return outcomes
            if job['state'] in ('seeding', 'cancelled', 'publication_uncertain', 'submitting', 'adding', 'seed_uncertain'):
                continue
            if job['state'] == 'draft' and cfg.get('enable_publish') is not True:
                continue
            node = nodes.get(job['source_node'])
            if node is None or not enabled_rule(cfg, job): continue
            try:
                client = client_for(app, node)
                acted = False
                if job['state'] in ('draft', 'prepared') and cfg.get('enable_publish') is True:
                    adapter = await session.adapter(job['target_site'])
                    acted = True
                    job = await worker.publish(job['id'], adapter, client)
                elif job['state'] in ('published', 'verification_failed'):
                    adapter = await session.adapter(job['target_site'])
                    acted = True
                    job = await worker.verify_publication(job['id'], adapter)
                if job['state'] in ('verified', 'rechecking') and cfg.get('enable_seed') is True:
                    acted = True
                    job = await asyncio.to_thread(worker.seed, job['id'], client, cfg.get('enable_resume') is True)
                if acted:
                    outcomes.append({'id': job['id'], 'state': job['state']})
            except Exception as exc:
                # No response bodies/URLs/cookies/announce tokens in logs.
                outcomes.append({'id': job['id'], 'state': ledger.get(job['id'])['state'], 'error': str(exc) if isinstance(exc, UploadBlocked) else type(exc).__name__})
        if len(outcomes) >= limit: return outcomes
        # Queue only hashes linked to BearBit by verified, authenticated site_records.
        data = ledger.store.read()
        existing = {(j['source_hash'], j['target_site']) for j in data['upload_jobs'].values()}
        configured = [r for r in cfg.get('rules', []) if isinstance(r, dict) and r.get('enable') is True]
        zones = sorted({r.get('source_zone') for r in configured if r.get('source_zone') in ('normal', 'special')})
        for node in nodes.values():
            if app.CLIENTS.adapter_for(node).upload_factory is None: continue
            inventory = await asyncio.to_thread(node.get_all_torrents_info)
            if not isinstance(inventory, list): continue
            completed = {str(t.get('hash', '')).lower() for t in inventory
                         if t.get('progress') == 1 and t.get('amount_left') == 0}
            for record in data['site_records'].values():
                source_hash = record.get('infohash')
                if record['site'] != 'BEARBIT' or source_hash not in completed: continue
                url = 'https://bearbit.org/details.php?id=' + record['torrent_id']
                for zone in zones:
                    pending = {r.get('target_site') for r in configured if r.get('source_zone') == zone
                               and (source_hash, r.get('target_site')) not in existing}
                    if not pending: continue
                    try:
                        client = client_for(app, node)
                        source_adapter = await session.adapter('BEARBIT')
                        metadata, content = await source_adapter.read_source(url, source_hash, zone)
                        rules = select_rules(cfg.get('rules', []), metadata)
                    except Exception as exc:
                        print('[Auto Upload] Source not ready:', source_hash[:12], zone, str(exc) if isinstance(exc, UploadBlocked) else type(exc).__name__)
                        continue
                    for rule in rules:
                        pair = (source_hash, rule['target_site'])
                        if pair in existing: continue
                        try:
                            job = await _prepare_destination(app, session, client, source_hash, metadata, content, rule)
                            outcomes.append({'id': job['id'], 'state': job['state']})
                            existing.add(pair)
                        except Exception as exc:
                            print('[Auto Upload] Destination not ready:', source_hash[:12], rule['target_site'],
                                  str(exc) if isinstance(exc, UploadBlocked) else type(exc).__name__)
                        if len(outcomes) >= limit: return outcomes
                if len(outcomes) >= limit: return outcomes
        return outcomes
    finally:
        await session.close()
