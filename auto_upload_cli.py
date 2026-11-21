"""Standalone queue runner and explicit inspection/reconciliation commands."""
from auto_upload import UploadBlocked
import argparse
import asyncio
import json
import os
from auto_upload import UploadWorker
from upload_runner import UploadSession, ledger_for, client_for, prepare_job, run_cycle, enabled_rule
from upload_sites import listing_identity
from upload_notifications import UploadNotifications, safe_error


def parser():
    p = argparse.ArgumentParser(description='BearBit -> Dedbit/Bitsuse/TorrentDD auto upload')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    classify = sub.add_parser('classify', help='Suggest category fields without changing state or publishing')
    classify.add_argument('job_id', nargs='?')
    classify.add_argument('--all-drafts', action='store_true')
    classify.add_argument('--client', action='store_true', help='Read and verify source manifest from its client')
    classify.add_argument('--musicbrainz', action='store_true', help='Opt in to MusicBrainz lookup')
    classify.add_argument('--web', action='store_true', help='Opt in to web reference search (not classification evidence)')
    classify.add_argument('--catalog-file', help='Explicit normalized metadata from an authorized catalog')
    classify.add_argument('--save', action='store_true', help='Save suggestions only; never apply fields or publish')
    capabilities = sub.add_parser('repack-capabilities', help='Detect available torrent creator without creating a task')
    capabilities.add_argument('--node', required=True)
    rebuild = sub.add_parser('repack', help='Rebuild oversized metainfo with an automatically detected creator; never publishes')
    rebuild.add_argument('job_id')
    rebuild.add_argument('--max-bytes', type=int, required=True, help='Size cap verified on the destination form')
    inspect_form = sub.add_parser('inspect-form')
    inspect_form.add_argument('--site', choices=('DEDBIT', 'BITSUSE', 'TORRENTDD'), required=True)
    inspect_form.add_argument('--category', required=True)
    run = sub.add_parser('run'); run.add_argument('--once', action='store_true')
    run.add_argument('--interval', type=int, default=300)
    for name in ('prepare', 'inspect-source'):
        prepare = sub.add_parser(name)
        prepare.add_argument('--node', required=True); prepare.add_argument('--hash', required=True)
        prepare.add_argument('--url', required=True); prepare.add_argument('--zone', choices=('normal', 'special'), required=True)
        if name == 'prepare':
            prepare.add_argument('--target-site', choices=('DEDBIT', 'BITSUSE', 'TORRENTDD'),
                                 help='Destination; required when more than one category rule matches')
    configure = sub.add_parser('configure')
    configure.add_argument('job_id'); configure.add_argument('--file', required=True)
    for name in ('show', 'check-job', 'publish', 'seed', 'reconcile', 'verify-canonical', 'reconcile-seed',
                 'cancel', 'retry-publication', 'resolve-duplicate', 'repair-category'):
        cmd = sub.add_parser(name); cmd.add_argument('job_id')
        if name == 'reconcile': cmd.add_argument('--url', required=True)
        if name == 'verify-canonical':
            cmd.add_argument('--url', help='Must match the listing already recorded on the job')
        if name == 'resolve-duplicate':
            cmd.add_argument('--url', help='Existing destination listing; defaults to the recorded duplicate result')
        if name == 'repair-category':
            cmd.add_argument('--musicbrainz', action='store_true', help='Opt in to MusicBrainz evidence')
            cmd.add_argument('--catalog-file', help='Normalized evidence from an authorized catalog')
        if name == 'retry-publication':
            cmd.add_argument('--confirm-not-published', action='store_true', required=True,
                             help='Confirm you checked destination My Uploads and this job was not published')
    return p


async def execute(args, app):
    if args.command == 'classify':
        from classification_runner import classify_uploads
        return await classify_uploads(args, app)
    await app.initialize_mapping()
    ledger = ledger_for(app); cfg = app.CFG.get('AUTO_UPLOAD', {})
    from category_review import category_settings
    settings = category_settings(cfg, getattr(app, 'BASE_DIR', '.'))
    if args.command == 'repair-category':
        settings.update(enable=True, auto_fix=True)
        if args.musicbrainz: settings['musicbrainz'] = True
        if args.catalog_file: settings['catalog_file'] = args.catalog_file
    worker = UploadWorker(ledger, enable_publish=cfg.get('enable_publish') is True, category_review=settings)
    if args.command == 'status':
        return [{'id': j['id'], 'state': j['state'], 'source_hash': j['source_hash'],
                 'target_site': j['target_site'], 'target_category': j['target_category'],
                 'source_node': j['source_node']} for j in ledger.store.read()['upload_jobs'].values()]
    if args.command == 'inspect-form':
        from torrent_metainfo import upload_route_supported
        zone = next((z for z in ('normal', 'special') if upload_route_supported(args.site, z, args.category)), None)
        if zone is None:
            raise UploadBlocked('Destination category is not available in a supported upload zone')
        session = UploadSession(app, profile_name='auto_upload_cli_' + str(os.getpid()))
        try:
            adapter = await session.adapter(args.site)
            spec = await adapter.load_form({'target_site': args.site, 'target_zone': zone,
                'target_category': str(args.category), 'metadata': {},
                'rule': {'fields': {}, 'confirmations': {}}}, require_ready=False)
            return {'site': args.site, 'zone': zone, 'category': args.category, 'fields': spec['fields'],
                    'acknowledgements': spec['acknowledgements']}
        finally:
            await session.close()
    if args.command == 'configure':
        from pathlib import Path
        changes = json.loads(Path(args.file).read_text(encoding='utf-8'))
        return ledger.configure_job(args.job_id, changes)
    if args.command == 'repack-capabilities':
        config = next((n for n in app.CFG['NODES'] if n.get('name') == args.node and n.get('enable') is True), None)
        if config is None: raise UploadBlocked('Source node is not enabled')
        node = app.CLIENTS.create(config)
        try:
            if not await asyncio.to_thread(node.login): raise UploadBlocked('Source client login failed')
            from torrent_creators import detect_creator
            creator = await asyncio.to_thread(detect_creator, client_for(app, node))
            return {'node': args.node, 'provider': creator.provider if creator else 'onlyone', 'supported': True}
        finally:
            app.CLIENTS.close(node)
    if args.command == 'repack':
        if cfg.get('enable') is not True or type(args.max_bytes) is not int or args.max_bytes <= 0:
            raise UploadBlocked('Repack requires AUTO_UPLOAD.enable and a positive destination size cap')
        job = ledger.get(args.job_id)
        config = next((n for n in app.CFG['NODES'] if n.get('name') == job['source_node'] and n.get('enable') is True), None)
        if config is None: raise UploadBlocked('Source node is not enabled')
        node = app.CLIENTS.create(config)
        try:
            if not await asyncio.to_thread(node.login): raise UploadBlocked('Source client login failed')
            from torrent_repack import rebuild_metainfo
            from upload_sites import SITES
            result = await asyncio.to_thread(rebuild_metainfo, ledger, job['id'], client_for(app, node),
                {'max_file_size': args.max_bytes, 'announce': SITES[job['target_site']][0] + '/announce.php'})
            return {'id': result['id'], 'state': result['state'], 'metainfo_preflight': result['metainfo_preflight']}
        finally:
            app.CLIENTS.close(node)
    if args.command == 'show':
        return ledger.review(args.job_id)
    if args.command == 'cancel':
        return {'id': args.job_id, 'state': ledger.transition(args.job_id, {'draft', 'prepared'}, 'cancelled')['state']}
    if args.command in ('publish', 'seed', 'reconcile-seed', 'resolve-duplicate', 'run') and cfg.get('enable') is not True:
        raise UploadBlocked('AUTO_UPLOAD.enable must be true for runtime actions')
    if args.command == 'run':
        if args.interval < 30: raise UploadBlocked('Minimum interval is 30 seconds')
        while True:
            app.CFG = app.load_full_config()
            nodes = []
            try:
                for c in app.CFG.get('NODES', []):
                    if not c.get('enable'): continue
                    node = app.CLIENTS.create(c); nodes.append((node, c))
                    if not await asyncio.to_thread(node.login): nodes.pop(); app.CLIENTS.close(node)
                result = await run_cycle(app, nodes)
                print(json.dumps(result, ensure_ascii=False))
            finally:
                for node, _ in nodes: app.CLIENTS.close(node)
            if args.once: return None
            await asyncio.sleep(args.interval)
    job = None if args.command in ('prepare', 'inspect-source') else ledger.get(args.job_id)
    if args.command == 'resolve-duplicate':
        from duplicate_recovery import resolution_listing
        resolution_listing(job, args.url)
    if args.command == 'repair-category' and job['state'] not in ('draft', 'prepared'):
        raise UploadBlocked('Category repair only accepts unsubmitted jobs')
    if args.command in ('publish', 'seed', 'reconcile-seed', 'retry-publication') and not enabled_rule(cfg, job):
        raise UploadBlocked('The job category rule is no longer enabled or has changed')
    if args.command == 'retry-publication':
        if getattr(args, 'confirm_not_published', False) is not True:
            raise UploadBlocked('Confirm the job is absent from destination My Uploads before retrying')
        if job['state'] != 'publication_uncertain' or job.get('listing') or job.get('target_hash'):
            raise UploadBlocked('Only an uncertain publication without a recorded listing can be retried')
    if args.command == 'publish' and cfg.get('enable_publish') is not True:
        raise UploadBlocked('AUTO_UPLOAD.enable_publish must be true')
    if args.command in ('seed', 'reconcile-seed') and cfg.get('enable_seed') is not True:
        raise UploadBlocked('AUTO_UPLOAD.enable_seed must be true')
    if args.command == 'reconcile-seed' and job['state'] in ('verified', 'rechecking'):
        raise UploadBlocked('Job is ' + job['state'] + '; use seed ' + job['id']
                            + '. reconcile-seed only recovers adding/seed_uncertain jobs')
    if args.command in ('reconcile', 'verify-canonical'):
        allowed = ('publication_uncertain', 'submitting')
        if args.command == 'verify-canonical' or job['state'] == 'verification_failed':
            allowed = ('published', 'verification_failed')
        if job['state'] not in allowed:
            raise UploadBlocked('Cannot verify canonical torrent in state ' + job['state'])
        if job['state'] in ('published', 'verification_failed') and not job.get('listing'):
            raise UploadBlocked('Canonical verification requires a recorded listing')
    if args.command in ('publish', 'check-job', 'repair-category') and settings.get('enable') is True:
        from upload_classifier import origin_requires_review
        if origin_requires_review(job):
            result = await worker.review_categories(job['id'], None, None)
            if args.command == 'publish':
                raise UploadBlocked('Category review requires attention: origin_requires_review')
            return ledger.review(job['id'])
    session = UploadSession(app, profile_name='auto_upload_cli_' + str(os.getpid())); node = None
    notices = UploadNotifications(app, ledger)
    try:
        if args.command in ('reconcile', 'verify-canonical'):
            adapter = await session.adapter(job['target_site'])
            if args.command == 'verify-canonical' or job['state'] == 'verification_failed':
                if getattr(args, 'url', None):
                    requested = listing_identity(job['target_site'], args.url)
                    recorded = job['listing']
                    if (str(requested['torrent_id']) != str(recorded.get('torrent_id', ''))
                            or requested['details_url'] != recorded.get('details_url')):
                        raise UploadBlocked('URL does not match the listing recorded on this job')
                # Re-download the canonical metainfo over the authenticated session;
                # no form POST and no duplicate publication are performed here.
                result = await worker.verify_publication(job['id'], adapter)
            else:
                listing = listing_identity(job['target_site'], args.url)
                # Verify the authenticated canonical payload before accepting an ambiguous result.
                from torrent_metainfo import payload_signature, _extract_torrent_info_hash
                from auto_upload import publication_signature
                content = await adapter.download_listing(listing['details_url'])
                if payload_signature(content) != publication_signature(job) or _extract_torrent_info_hash(content) == job['source_hash']:
                    raise UploadBlocked('Listing does not match the uploaded payload/variant')
                ledger.record_publication(job['id'], listing)
                result = ledger.accept_canonical(job['id'], content)
        else:
            name = args.node if args.command in ('prepare', 'inspect-source') else job['source_node']
            config = next((n for n in app.CFG['NODES'] if n.get('name') == name and n.get('enable') is True), None)
            if config is None: raise UploadBlocked('Source node is not enabled')
            node = app.CLIENTS.create(config)
            if not await asyncio.to_thread(node.login): raise RuntimeError('Source client login failed')
            client = client_for(app, node)
            if args.command == 'inspect-source':
                from torrent_metainfo import verify_completed
                adapter = await session.adapter('BEARBIT')
                metadata, content = await adapter.read_source(args.url, args.hash.lower(), args.zone)
                manifest = verify_completed(content, client.inventory(args.hash), client.files(args.hash))
                return {'source_hash': args.hash.lower(), 'title': metadata['title'],
                        'source_category': metadata['category'], 'source_zone': metadata['zone'],
                        'total_size_bytes': manifest['total_size_bytes'], 'files': len(manifest['files']),
                        'padding_files': sum(bool(f.get('is_padding')) for f in manifest['files'])}
            if args.command == 'prepare':
                result = await prepare_job(app, session, node, args.hash, args.url, args.zone,
                                           getattr(args, 'target_site', None))
            elif args.command == 'check-job':
                return await worker.check_job(job['id'], await session.adapter(job['target_site']), client)
            elif args.command == 'repair-category':
                result = await worker.review_categories(job['id'], await session.adapter(job['target_site']), client)
                return {'id':result['id'], 'state':result['state'], 'category_review':result['category_review'],
                        'classification':result['classification']}
            elif args.command == 'resolve-duplicate':
                from duplicate_recovery import resolve_duplicate
                result = await resolve_duplicate(ledger, job['id'], await session.adapter(job['target_site']), client, args.url)
            elif args.command == 'publish':
                result = await worker.publish(job['id'], await session.adapter(job['target_site']), client,
                                              allow_manual_retry=True)
            elif args.command == 'retry-publication':
                result = await worker.retry_publication(job['id'], await session.adapter(job['target_site']), client,
                                                        confirmed_not_published=args.confirm_not_published)
            elif args.command == 'reconcile-seed':
                result = await asyncio.to_thread(worker.reconcile_seed, job['id'], client)
            else:
                result = await asyncio.to_thread(worker.seed, job['id'], client, cfg.get('enable_resume') is True)
        notices.record(result)
        outcome = {'id': result['id'], 'state': result['state'], 'target_site': result['target_site']}
        if args.command == 'retry-publication':
            outcome['manual_publish_required'] = True
        return outcome
    except Exception as exc:
        if job is not None and args.command in ('publish', 'seed', 'reconcile', 'verify-canonical',
                                                'reconcile-seed', 'retry-publication'):
            current = ledger.get(job['id'])
            notices.record(current, safe_error(exc, current))
        raise
    finally:
        try:
            if node is not None: app.CLIENTS.close(node)
            await session.close()
        finally:
            if args.command in ('prepare', 'publish', 'seed', 'reconcile', 'verify-canonical',
                                'reconcile-seed', 'retry-publication'):
                await notices.flush()


def main():
    args = parser().parse_args()
    import main as app
    app.CFG = app.load_full_config()
    try:
        result = asyncio.run(execute(args, app))
        if result is not None: print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        # Site/API exception text can contain cookies or a signed URL.
        print('Auto Upload failed:', str(exc) if isinstance(exc, UploadBlocked) else type(exc).__name__)
        raise SystemExit(1)


if __name__ == '__main__': main()
