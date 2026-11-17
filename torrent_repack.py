"""Durable, source-bound rebuilding for oversized upload metainfo."""
import hashlib
import posixpath
import secrets
from pathlib import Path

from auto_upload import (UploadBlocked, MetainfoTooLarge, create_job_variant,
                         validate_metainfo_size, repacked_source, write_artifact)
from torrent_creators import detect_creator, remote_path
from torrent_metainfo import (create_variant, payload_signature, _extract_torrent_manifest,
                              _extract_torrent_info_hash, _decode_bencode_value, bencode)
from torrent_store import MappingStore, stamp
from upload_logging import log_event


def choose_piece_size(source, job, spec):
    """Estimate only the hash-table size; actual remote hashes are still required."""
    value, _ = _decode_bencode_value(source)
    manifest = _extract_torrent_manifest(source)
    if any(f.get('is_padding') for f in manifest['files']):
        raise UploadBlocked('Metainfo rebuilding does not yet support padding files')
    piece = max(32768, 1 << value[b'info'][b'piece length'].bit_length())
    while piece <= 64 * 1024**2:
        count = (manifest['total_size_bytes'] + piece - 1) // piece
        value[b'info'][b'piece length'] = piece
        value[b'info'][b'pieces'] = b'\0' * (count * 20)
        estimate = create_variant(bencode(value), job['target_site'], job['target_zone'],
                                  job['target_category'], spec['announce'])
        if len(estimate) <= spec['max_file_size']:
            return piece
        piece *= 2
    raise UploadBlocked('Metainfo cannot fit the destination limit with a supported piece size')


def rebuild_metainfo(ledger, key, client, spec):
    job = ledger.get(key)
    if job['state'] not in ('draft', 'prepared', 'publication_uncertain'):
        raise UploadBlocked('Only an unsubmitted or uncertain job can rebuild its metainfo')
    if client.name != job['source_node']:
        raise UploadBlocked('Metainfo rebuilding must run on the source node')
    from upload_recovery import verified_source
    source = verified_source(job, client)
    original = create_variant(source, job['target_site'], job['target_zone'], job['target_category'], spec['announce'])
    try:
        validate_metainfo_size(len(original), spec)
    except MetainfoTooLarge:
        pass
    else:
        raise UploadBlocked('Metainfo rebuilding is only allowed when the original exceeds the live size limit')
    if job.get('repack'):
        validate_metainfo_size(len(create_job_variant(job, source, spec['announce'])), spec)
        return job
    request = job.get('repack_request')
    provider = request.get('provider', 'onlyone') if request else None
    creator = detect_creator(client, provider)
    if creator is None:
        from onlyone_repack import rebuild_onlyone
        return rebuild_onlyone(ledger, key, client, spec)
    lock = MappingStore(ledger.store.path.with_name('.torrent-creator-' + hashlib.sha256(
        client.node.url.encode()).hexdigest()[:16]))
    handle = lock.try_lock()
    if handle is None: raise UploadBlocked('Another worker is checking this torrent creator')
    try:
        manifest = _extract_torrent_manifest(source)
        source_path = (job['save_path'] if creator.provider == 'rutorrent' and manifest['multi_file']
                       else posixpath.join(job['save_path'], manifest['root_name']))
        source_path = remote_path(source_path)
        if request and (request['provider'] != creator.provider or request['source_path'] != source_path):
            raise UploadBlocked('Torrent creator source location changed during rebuilding')
        cache = Path(ledger.artifacts) / key / 'creator-result'
        if not request:
            claim = {'provider': creator.provider, 'source_path': source_path,
                     'piece_size': choose_piece_size(source, job, spec),
                     'marker': 'uap-' + secrets.token_hex(16), 'claimed_at': stamp()}
            def claim_start(data):
                current = data['upload_jobs'][key]
                if current['state'] != job['state'] or not ledger.same_configuration(current, job):
                    raise UploadBlocked('Job changed before torrent creation')
                current.update(repack_request=claim, updated_at=stamp())
            ledger.store.update(claim_start)
            from upload_sites import SITES
            log_event('repack_start', job, provider=creator.provider, piece_size=claim['piece_size'])
            # A lost response leaves the durable claim held. Never repeat this POST.
            content = creator.start(claim, SITES[job['target_site']][0] + '/announce.php')
            if creator.provider == 'deluge':
                write_artifact(ledger.artifacts, key, 'creator-result', content, claim['marker'] + '.torrent')
            raise UploadBlocked('Torrent creator started; poll again later to validate its result')
        if creator.provider == 'deluge':
            path = cache / (request['marker'] + '.torrent')
            if not path.is_file():
                raise UploadBlocked('Deluge creator result unresolved; inspect the saved remote output; do not repeat creation')
            if path.stat().st_size > 16 * 1024**2: raise UploadBlocked('Torrent creator result exceeds read limit')
            candidate = path.read_bytes()
        else:
            log_event('repack_status', job, provider=creator.provider, status='checking', piece_size=request['piece_size'])
            candidate = creator.result(request)
        evidence = {'provider': creator.provider, 'source_hash': job['source_hash'],
                    'source_signature': job['content_group_id'], 'candidate_hash': _extract_torrent_info_hash(candidate),
                    'candidate_signature': payload_signature(candidate), 'checked_at': stamp()}
        repacked_source({**job, 'repack': evidence}, source, candidate=candidate)
        decoded, _ = _decode_bencode_value(candidate)
        if decoded[b'info'][b'piece length'] != request['piece_size']:
            raise UploadBlocked('Torrent creator result piece size differs from the request')
        variant = create_variant(candidate, job['target_site'], job['target_zone'], job['target_category'], spec['announce'])
        validate_metainfo_size(len(variant), spec)
        evidence['artifact'] = write_artifact(ledger.artifacts, key, 'repacked', candidate, evidence['candidate_hash'] + '.torrent')
        prepared = write_artifact(ledger.artifacts, key, 'prepared', variant, _extract_torrent_info_hash(variant) + '.torrent')
        def finish(data):
            current = data['upload_jobs'][key]
            if current['state'] != job['state'] or not ledger.same_configuration(current, job):
                raise UploadBlocked('Job changed while validating rebuilt metainfo')
            current.update(repack=evidence, prepared_hash=_extract_torrent_info_hash(variant),
                prepared_artifact=prepared, updated_at=stamp(), metainfo_preflight={'status': 'ready',
                    'max_file_size': spec['max_file_size'], 'variant_bytes': len(variant), 'checked_at': stamp()})
            return dict(current)
        result = ledger.store.update(finish)
        log_event('repack_ready', result, provider=creator.provider, metainfo_bytes=len(variant),
                  max_file_size=spec['max_file_size'])
        return result
    finally:
        lock.unlock(handle)
