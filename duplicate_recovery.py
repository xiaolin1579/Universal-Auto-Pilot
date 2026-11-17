"""Attach an authenticated existing listing to an already seeding local torrent.

Never submits an upload or calls add/recheck/resume/delete on the client.
"""
import asyncio
from copy import deepcopy

from auto_upload import UploadBlocked, write_artifact, upload_filename
from torrent_store import infohash, stamp
from torrent_metainfo import (_extract_torrent_info_hash, payload_signature,
                              _extract_torrent_manifest, _decode_bencode_value,
                              valid_target_announce, verify_completed)
from upload_recovery import verified_source, MAX_METAINFO_BYTES
from upload_sites import listing_identity


def resolution_listing(job, url=None):
    resolution = job.get('duplicate_resolution') or {}
    if job['state'] not in ('draft', 'prepared') and not (
            job['state'] == 'seeding' and resolution.get('method') == 'existing_destination_torrent'):
        raise UploadBlocked('Only an unsubmitted duplicate job or a previously resolved duplicate can be resolved')
    if job.get('repack_request') or job.get('repack'):
        raise UploadBlocked('Repacked jobs require a separate duplicate review')
    evidence = job.get('duplicate_check') or {}
    if url is None:
        if evidence.get('status') != 'duplicate' or evidence.get('complete') is not True:
            raise UploadBlocked('A complete duplicate result or an explicit --url is required')
        url = (evidence.get('listing') or {}).get('details_url')
    try:
        return listing_identity(job['target_site'], url)
    except (ValueError, TypeError):
        raise UploadBlocked('Invalid destination listing URL') from None


def commit_resolution(ledger, expected, listing, source, canonical, target, files):
    key = expected['id']
    listing = listing_identity(expected['target_site'], listing['details_url'])
    digest = _extract_torrent_info_hash(canonical)
    if (len(canonical) > MAX_METAINFO_BYTES or digest == expected['source_hash']
            or _extract_torrent_info_hash(source) != expected['source_hash']
            or payload_signature(source) != expected['content_group_id']
            or payload_signature(canonical) != expected['content_group_id']):
        raise UploadBlocked('Existing listing does not have the exact queued source layout and pieces')
    decoded, _ = _decode_bencode_value(canonical)
    manifest = _extract_torrent_manifest(canonical)
    if (decoded[b'info'].get(b'private') != 1
            or any(k in decoded for k in (b'url-list', b'httpseeds', b'nodes'))
            or not manifest['announce_urls']
            or any(not valid_target_announce(expected['target_site'], url) for url in manifest['announce_urls'])):
        raise UploadBlocked('Existing canonical torrent has an unrecognized tracker or alternate data source')
    if target.get('node') != expected['source_node'] or target.get('save_path') != expected['save_path']:
        raise UploadBlocked('Existing target client or save path differs from the queued source')
    if str(target.get('state', '')).lower() not in ('uploading', 'stalledup', 'forcedup'):
        raise UploadBlocked('Existing target must already be seeding; resolve-duplicate does not resume torrents')
    verify_completed(canonical, target, files)

    def apply(data):
        current = data['upload_jobs'][key]
        if (current['state'] != expected['state'] or current.get('updated_at') != expected.get('updated_at')
                or not ledger.same_configuration(current, expected)
                or current.get('duplicate_check') != expected.get('duplicate_check')
                or current.get('listing') != expected.get('listing')
                or current.get('target_hash') != expected.get('target_hash')):
            raise UploadBlocked('Job changed during duplicate recovery; inspect it again')
        if current.get('target_hash') not in (None, digest) or current.get('listing') not in (None, listing):
            raise UploadBlocked('Job is already linked to a different destination torrent')
        if any(j['id'] != key and (j.get('target_hash') == digest or j.get('listing') == listing)
               and j['state'] != 'cancelled'
               for j in data['upload_jobs'].values()):
            raise UploadBlocked('Existing target is already linked to another upload job')
        signature = current['content_group_id']
        if any(g['id'] != signature and digest in g['hashes'] for g in data['content_groups'].values()):
            raise UploadBlocked('Existing target belongs to another content group')
        site = current['target_site']; tid = listing['torrent_id']; site_key = site + ':' + tid
        record = data['site_records'].get(site_key)
        if record and record.get('infohash') not in (None, digest):
            raise UploadBlocked('Destination listing is already mapped to another hash')
        rows = [r for r in data['torrents'] if infohash(r['client_info'].get('hash')) == digest]
        row = rows[0] if rows else None
        if row and row.get('content_group_id') not in (None, signature):
            raise UploadBlocked('Existing torrent record belongs to another content group')
        if row and any(str(r.get('site_key', '')).upper() == site and str(r.get('torrent_id')) != tid
                       for r in row.get('site_listings', []) if r.get('verified')):
            raise UploadBlocked('Existing torrent record has a conflicting verified listing')
        if current['state'] == 'seeding':
            return deepcopy(current)  # Idempotent: rechecks precede this transaction.
        checked = stamp()
        filename = upload_filename(current)
        current.update(state='seeding', target_hash=digest, listing=listing, updated_at=checked, verified_at=checked,
            source_artifact=write_artifact(ledger.artifacts, key, 'source', source, filename),
            canonical_artifact=write_artifact(ledger.artifacts, key, 'canonical', canonical, filename),
            duplicate_check={'status':'duplicate','complete':True,'listing':listing,
                             'method':'authenticated_listing_and_live_client','checked_at':checked},
            duplicate_resolution={'method':'existing_destination_torrent','checked_at':checked,
                                  'target_hash':digest,'listing':listing})
        if current.get('publication_retry'):
            current['publication_retry'].update(manual_publish_required=False, resolved_at=checked)
        group = data['content_groups'][signature]
        for value in (current['source_hash'], digest):
            if value not in group['hashes']: group['hashes'].append(value)
        if record is None:
            record = {'site':site,'torrent_id':tid,'infohash':digest,
                'legacy_state':{'hash':digest,'name':current['metadata']['title'],'url':listing['details_url']},
                'obligations':{k:{'status':'unknown'} for k in ('hr','quest_seed')}}
            data['site_records'][site_key] = record
        else:
            record['infohash'] = digest  # Preserve H&R, manual holds and existing observations.
            record['legacy_state'].update(hash=digest, url=listing['details_url'])
        if row is None:
            row = {'client_info':{'hash':digest,'name':target.get('name') or current['metadata']['title']},
                'source_site':site,'source_site_verified':True,'source_zone_id':current['target_zone'],
                'source_zone_verified':True,
                'source_zone_evidence':{'method':'authenticated_existing_destination_torrent',
                    'details_url':listing['details_url'],'verified_at':checked},
                'variant_info':{'role':'existing_destination_variant','original_infohash':current['source_hash'],
                    'target_site':site,'target_zone_id':current['target_zone'],'verified':True},
                'site_listings':[],'node_locations':[]}
            data['torrents'].append(row)
        row['content_group_id'] = signature
        linked = next((r for r in row.get('site_listings', []) if str(r.get('site_key','')).upper() == site
                       and str(r.get('torrent_id')) == tid), None)
        if linked is None:
            linked = {}
            row.setdefault('site_listings', []).append(linked)
        linked.update(site_key=site,torrent_id=tid,details_url=listing['details_url'],
                      zone_id=current['target_zone'],verified=True)
        locations = row.setdefault('node_locations', [])
        locations[:] = [loc for loc in locations if loc.get('seedbox_host') != current['source_node']]
        locations.append({'seedbox_host':current['source_node'],'save_path':current['save_path'],
            'present':True,'inventory_verified':True,'verified_at':checked,
            'verification_method':'live_existing_destination_torrent'})
        return deepcopy(current)
    return ledger.store.update(apply)


async def resolve_duplicate(ledger, key, adapter, client, url=None):
    job = ledger.get(key)
    listing = resolution_listing(job, url)
    if adapter.site != job['target_site']:
        raise UploadBlocked('Wrong destination adapter')
    if adapter.enabled_paths is not None:
        from upload_sites import SITES
        if SITES[adapter.site][2][job['target_zone']] not in adapter.enabled_paths:
            raise UploadBlocked('Destination zone is disabled in site configuration')
    source = await asyncio.to_thread(verified_source, job, client)
    canonical = await adapter.download_listing(listing['details_url'])
    digest = _extract_torrent_info_hash(canonical)
    target = await asyncio.to_thread(client.inventory, digest, missing_ok=True)
    if target is None:
        raise UploadBlocked('Destination torrent is not present on the source client; use the Cross-Seed workflow')
    files = await asyncio.to_thread(client.files, digest)
    return await asyncio.to_thread(commit_resolution, ledger, job, listing, source, canonical, target, files)
