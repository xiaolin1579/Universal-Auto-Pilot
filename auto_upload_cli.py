"""Standalone queue runner and explicit inspection/reconciliation commands."""
from auto_upload import UploadBlocked
import argparse
import asyncio
import json
from auto_upload import UploadWorker
from upload_runner import UploadSession, ledger_for, client_for, prepare_job, run_cycle, enabled_rule
from upload_sites import listing_identity


def parser():
    p = argparse.ArgumentParser(description='BearBit -> Dedbit/Bitsuse/TorrentDD auto upload')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
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
    for name in ('show', 'check-job', 'publish', 'seed', 'reconcile', 'reconcile-seed', 'cancel'):
        cmd = sub.add_parser(name); cmd.add_argument('job_id')
        if name == 'reconcile': cmd.add_argument('--url', required=True)
    return p


async def execute(args, app):
    await app.initialize_mapping()
    ledger = ledger_for(app); cfg = app.CFG.get('AUTO_UPLOAD', {})
    worker = UploadWorker(ledger, enable_publish=cfg.get('enable_publish') is True)
    if args.command == 'status':
        return [{'id': j['id'], 'state': j['state'], 'source_hash': j['source_hash'],
                 'target_site': j['target_site'], 'target_category': j['target_category'],
                 'source_node': j['source_node']} for j in ledger.store.read()['upload_jobs'].values()]
    if args.command == 'inspect-form':
        from torrent_metainfo import upload_route_supported
        zone = next((z for z in ('normal', 'special') if upload_route_supported(args.site, z, args.category)), None)
        if zone is None:
            raise UploadBlocked('Destination category is not available in a supported upload zone')
        session = UploadSession(app)
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
    if args.command == 'show':
        return ledger.review(args.job_id)
    if args.command == 'cancel':
        return {'id': args.job_id, 'state': ledger.transition(args.job_id, {'draft', 'prepared'}, 'cancelled')['state']}
    if args.command in ('publish', 'seed', 'reconcile-seed', 'run') and cfg.get('enable') is not True:
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
    if args.command in ('publish', 'seed', 'reconcile-seed') and not enabled_rule(cfg, job):
        raise UploadBlocked('The job category rule is no longer enabled or has changed')
    if args.command == 'publish' and cfg.get('enable_publish') is not True:
        raise UploadBlocked('AUTO_UPLOAD.enable_publish must be true')
    if args.command in ('seed', 'reconcile-seed') and cfg.get('enable_seed') is not True:
        raise UploadBlocked('AUTO_UPLOAD.enable_seed must be true')
    session = UploadSession(app); node = None
    try:
        if args.command == 'reconcile':
            if job['state'] not in ('publication_uncertain', 'submitting'):
                raise UploadBlocked('Only an uncertain submission can be reconciled')
            adapter = await session.adapter(job['target_site'])
            listing = listing_identity(job['target_site'], args.url)
            # Verify the authenticated canonical payload before accepting an ambiguous result.
            from torrent_metainfo import payload_signature, _extract_torrent_info_hash
            content = await adapter.download_listing(listing['details_url'])
            if payload_signature(content) != job['content_group_id'] or _extract_torrent_info_hash(content) == job['source_hash']:
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
            elif args.command == 'publish':
                result = await worker.publish(job['id'], await session.adapter(job['target_site']), client)
            elif args.command == 'reconcile-seed':
                result = await asyncio.to_thread(worker.reconcile_seed, job['id'], client)
            else:
                result = await asyncio.to_thread(worker.seed, job['id'], client, cfg.get('enable_resume') is True)
        return {'id': result['id'], 'state': result['state'], 'target_site': result['target_site']}
    finally:
        if node is not None: app.CLIENTS.close(node)
        await session.close()


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
