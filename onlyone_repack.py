"""OnlyOne's account-scoped torrent maker. Never sends a tracker passkey.

One provider task is shared by the account. Persist a claim before starting it;
subsequent cycles only poll. An interrupted POST is never automatically repeated.
"""
import hashlib
import posixpath
from pathlib import Path
from urllib.parse import urlsplit, urlencode

from auto_upload import (UploadBlocked, MetainfoTooLarge, create_job_variant, validate_metainfo_size,
                         write_artifact, repacked_source)
from torrent_metainfo import (create_variant, verify_completed, payload_signature,
                              _extract_torrent_manifest, _extract_torrent_info_hash)
from torrent_store import MappingStore, stamp
from upload_logging import log_event


class OnlyOneMaker:
    def __init__(self, client):
        self.client = client
        self.node = client.node
        parsed = urlsplit(self.node.url)
        host = parsed.hostname or ''
        if (getattr(self.node, 'provider_sso', False) is not True or parsed.scheme != 'https'
                or not (host == 'onlyoneseedbox.opik.net' or host.endswith('.onlyoneseedbox.opik.net'))
                or not parsed.path.endswith('/qbittorrent') or parsed.username or parsed.password):
            raise UploadBlocked('Metainfo rebuilding currently requires a verified OnlyOne SSO node')
        self.base = self.node.url.rsplit('/qbittorrent', 1)[0]

    def request(self, route, body=None, binary=False):
        response = self.node._execute_request('GET' if body is None else 'POST', self.base + route,
            **({'json': body, 'headers': {'X-Requested-With': 'mk'}} if body is not None else {}),
            verify=True, allow_redirects=False, timeout=30, stream=binary)
        if response is None or response.status_code != 200:
            if response is not None: response.close()
            raise UploadBlocked('OnlyOne maker request was not confirmed')
        if binary:
            content = bytearray()
            try:
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > 16 * 1024**2:
                        raise UploadBlocked('OnlyOne maker metainfo exceeds read limit')
                return bytes(content)
            finally:
                response.close()
        data = response.json()
        if not isinstance(data, dict):
            raise UploadBlocked('OnlyOne maker returned an invalid response')
        return data

    def location(self, job, source):
        manifest = _extract_torrent_manifest(source)
        name = manifest['root_name']
        # Initial support is the provider file-manager root, as shown in /maker.
        # A nested/renamed source needs explicit mapping rather than guessing paths.
        if posixpath.basename(job['save_path'].rstrip('/')) != 'downloads':
            raise UploadBlocked('OnlyOne rebuilding currently requires the default downloads directory')
        listing = self.request('/api/fm/list')
        if listing.get('root') != 'files' or listing.get('path') != '':
            raise UploadBlocked('OnlyOne file-manager root changed')
        entries = listing.get('entries')
        matches = [e for e in entries if isinstance(e, dict) and e.get('name') == name] if isinstance(entries, list) else []
        kind = 'dir' if manifest['multi_file'] else 'file'
        if len(matches) != 1 or matches[0].get('type') != kind:
            raise UploadBlocked('OnlyOne source is absent or ambiguous in the file-manager root')
        return {'root': 'files', 'path': name}


def rebuild_onlyone(ledger, key, client, spec):
    job = ledger.get(key)
    if job['state'] not in ('draft', 'prepared', 'publication_uncertain'):
        raise UploadBlocked('Only an unsubmitted or uncertain job can rebuild its metainfo')
    if client.name != job['source_node']:
        raise UploadBlocked('Metainfo rebuilding must run on the source node')
    source = Path(job['source_artifact']).read_bytes()
    live = client.inventory(job['source_hash'])
    if live.get('save_path') != job['save_path']:
        raise UploadBlocked('Source save path changed before rebuilding')
    verify_completed(source, live, client.files(job['source_hash']))
    if payload_signature(source) != job['content_group_id']:
        raise UploadBlocked('Source identity changed before rebuilding')
    original = create_variant(source, job['target_site'], job['target_zone'], job['target_category'], spec['announce'])
    try:
        validate_metainfo_size(len(original), spec)
    except MetainfoTooLarge:
        pass
    else:
        raise UploadBlocked('Metainfo rebuilding is only allowed when the original exceeds the live size limit')
    maker = OnlyOneMaker(client)
    lock = MappingStore(ledger.store.path.with_name('.onlyone-maker-' + hashlib.sha256(maker.base.encode()).hexdigest()[:16]))
    handle = lock.try_lock()
    if handle is None:
        raise UploadBlocked('Another worker is checking the OnlyOne maker')
    try:
        location = maker.location(job, source)
        status = maker.request('/api/tenant/maker')
        log_event('repack_status', job, status=status.get('state'), done=status.get('done'), total=status.get('total'))
        request = job.get('repack_request')
        if job.get('repack'):
            validate_metainfo_size(len(create_job_variant(job, source, spec['announce'])), spec)
            return job
        same_source = status.get('root') == location['root'] and status.get('src') == location['path']
        if request and (request.get('path') != location['path'] or request.get('root') != location['root']):
            raise UploadBlocked('OnlyOne source location changed during rebuilding')
        if request and status.get('state') in ('running', 'done'):
            if not same_source or posixpath.basename(str(status.get('out', ''))) != request['name']:
                # While running, out may not yet be populated. Never accept another completed result.
                if not (same_source and status.get('state') == 'running' and not status.get('out')):
                    raise UploadBlocked('OnlyOne maker result belongs to a different request')
        if same_source and status.get('state') == 'done':
            output = status.get('out')
            if (not isinstance(output, str) or not output.endswith('.torrent') or output.startswith('/')
                    or any(x in output.split('/') for x in ('.', '..')) or '\\' in output or '\x00' in output):
                raise UploadBlocked('OnlyOne maker output path is invalid')
            candidate = maker.request('/fm/download?' + urlencode({'root': location['root'], 'path': output}), binary=True)
            evidence = {'provider': 'onlyone', 'source_hash': job['source_hash'],
                'source_signature': job['content_group_id'], 'candidate_hash': _extract_torrent_info_hash(candidate),
                'candidate_signature': payload_signature(candidate), 'checked_at': stamp()}
            # Validate before writing an artifact or modifying any queue pointer.
            repacked_source({**job, 'repack': evidence}, source, candidate=candidate)
            variant = create_variant(candidate, job['target_site'], job['target_zone'], job['target_category'], spec['announce'])
            validate_metainfo_size(len(variant), spec)
            candidate_path = write_artifact(ledger.artifacts, key, 'repacked', candidate, evidence['candidate_hash'] + '.torrent')
            prepared_path = write_artifact(ledger.artifacts, key, 'prepared', variant, _extract_torrent_info_hash(variant) + '.torrent')
            evidence['artifact'] = candidate_path
            def finish(data):
                current = data['upload_jobs'][key]
                if current['state'] != job['state'] or not ledger.same_configuration(current, job):
                    raise UploadBlocked('Job changed while validating rebuilt metainfo')
                current.update(repack=evidence, prepared_hash=_extract_torrent_info_hash(variant),
                    prepared_artifact=prepared_path, updated_at=stamp(),
                    metainfo_preflight={'status': 'ready', 'max_file_size': spec['max_file_size'],
                                        'variant_bytes': len(variant), 'checked_at': stamp()})
                return dict(current)
            result = ledger.store.update(finish)
            log_event('repack_ready', result, metainfo_bytes=len(variant), max_file_size=spec['max_file_size'])
            return result
        if status.get('state') == 'running':
            raise UploadBlocked('OnlyOne maker is running; poll again later without starting another task')
        if request:
            raise UploadBlocked('OnlyOne maker request is unresolved; inspect the provider before starting another task')
        name = 'uap-' + key[:24] + '.torrent'
        # Do not overwrite an existing file, even from an older attempt.
        existing = maker.request('/api/fm/list')
        if any(e.get('name') == name for e in existing.get('entries', [])):
            raise UploadBlocked('OnlyOne maker output already exists; inspect it before starting')
        claim = {**location, 'name': name, 'claimed_at': stamp()}
        def claim_start(data):
            current = data['upload_jobs'][key]
            if current['state'] != job['state'] or not ledger.same_configuration(current, job) or current.get('repack_request'):
                raise UploadBlocked('Metainfo rebuild was already claimed or job changed')
            current.update(repack_request=claim, updated_at=stamp())
        ledger.store.update(claim_start)
        # Use a credential-free destination URL. The worker inserts the real announce later.
        from upload_sites import SITES
        log_event('repack_start', job)
        maker.request('/api/tenant/maker', {'path': location['path'], 'trackers': [SITES[job['target_site']][0] + '/announce.php'],
            'private': True, 'name': name, 'comment': ''})
        raise UploadBlocked('OnlyOne maker started; poll again later to validate its result')
    finally:
        lock.unlock(handle)
