"""Read-only classification workflow, with explicit metadata-only --save."""
import asyncio
from pathlib import Path

from auto_upload import UploadBlocked
from torrent_metainfo import (_extract_torrent_info_hash, payload_signature,
                              _extract_torrent_manifest, verify_completed)
from upload_classifier import classify_job, extract_identity, origin_requires_review
from upload_runner import ledger_for, client_for


def _manifest(job, content):
    if (_extract_torrent_info_hash(content) != job['source_hash']
            or payload_signature(content) != job['content_group_id']):
        raise UploadBlocked('Source artifact identity differs from the queued job')
    return _extract_torrent_manifest(content)


def _files(manifest):
    return [{'path':manifest['root_name']+'/'+f['path'] if manifest['multi_file'] else f['path'],
             'size_bytes':f['size_bytes']} for f in manifest['files'] if not f.get('is_padding')]


async def classify_uploads(args, app):
    if bool(args.job_id) == bool(args.all_drafts):
        raise UploadBlocked('Specify one job ID or --all-drafts')
    ledger = ledger_for(app)
    data = ledger.store.read()  # Deliberately no initialize_mapping, browser or notification.
    if args.all_drafts:
        jobs = [j for j in data['upload_jobs'].values() if j['state']=='draft']
    else:
        if args.job_id not in data['upload_jobs']: raise UploadBlocked('Upload job not found')
        jobs = [data['upload_jobs'][args.job_id]]
    if args.save and any(j['state'] not in ('draft','prepared') for j in jobs):
        raise UploadBlocked('Classification suggestions may only be saved for draft/prepared jobs')
    from metadata_providers import load_catalog_records, lookup_metadata
    try:
        imported = load_catalog_records(args.catalog_file) if args.catalog_file else []
    except (OSError,ValueError) as exc:
        raise UploadBlocked('Catalog metadata file is unavailable or invalid ('+type(exc).__name__+')') from None
    results = []
    for job in jobs:
        diagnostics = []
        files = None
        manifest_source = 'unavailable'
        identity = extract_identity(job)
        # Labels about origin are routed to human review, without preparing fields or fetching sources.
        origin_review = origin_requires_review(job, identity)
        if not origin_review:
            try:
                path = Path(job['source_artifact'])
                if path.stat().st_size > 16*1024**2: raise ValueError('Metainfo too large')
                files = _files(_manifest(job,path.read_bytes())); manifest_source='source_metainfo'
            except (OSError,ValueError,KeyError):
                diagnostics.append('source_artifact_unavailable')
            if args.client:
                config = next((n for n in app.CFG.get('NODES',[]) if n.get('name')==job['source_node'] and n.get('enable') is True),None)
                if config is None:
                    diagnostics.append('source_node_unavailable')
                else:
                    node = app.CLIENTS.create(config)
                    try:
                        if not await asyncio.to_thread(node.login): raise UploadBlocked('Source client login failed')
                        client = client_for(app,node)
                        live = await asyncio.to_thread(client.inventory,job['source_hash'])
                        if live.get('save_path') != job['save_path']: raise UploadBlocked('Source path changed')
                        if not hasattr(client,'request'): raise UploadBlocked('Read-only source export is not supported by this client')
                        response = await asyncio.to_thread(client.request,'GET','torrents/export',params={'hash':job['source_hash']})
                        content=response.content; manifest=_manifest(job,content)
                        verify_completed(content,live,await asyncio.to_thread(client.files,job['source_hash']))
                        files=_files(manifest);manifest_source='verified_client_export'
                        diagnostics=[d for d in diagnostics if d!='source_artifact_unavailable']
                    except Exception as exc:
                        diagnostics.append('client_manifest_unavailable_'+type(exc).__name__)
                    finally:
                        app.CLIENTS.close(node)
        lookups={'records':[],'references':[],'diagnostics':[],'providers':[]}
        if (args.musicbrainz or args.web) and not origin_review:
            lookups=await asyncio.to_thread(lookup_metadata,identity,musicbrainz=args.musicbrainz,web=args.web,
                cache_dir=Path(app.BASE_DIR)/'.metadata_cache')
        report=classify_job(job,files=files,records=imported+lookups['records'],references=lookups['references'],
                            diagnostics=diagnostics+lookups['diagnostics'])
        report['manifest_source']=manifest_source;report['providers']=lookups['providers'];report['saved']=False
        if args.save:
            ledger.save_classification(job['id'],report)
            report['saved']=True
        results.append(report)
    return results if args.all_drafts else results[0]
