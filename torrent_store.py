"""Shared JSON mapping repository. No client SDK or application imports.

Atomic replacements protect readers. A sidecar OS lock serializes read/modify/write
transactions across processes; callers must keep the lock for the whole transaction.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from torrent_metainfo import upload_route_supported

SCHEMA_VERSION = 2
KINDS = ('hr', 'quest_seed')
UPLOAD_STATES = {'draft', 'prepared', 'submitting', 'publication_uncertain', 'published',
                 'verification_failed', 'verified', 'adding', 'seed_uncertain',
                 'rechecking', 'seeding', 'cancelled'}
UPLOAD_HOLD_STATES = UPLOAD_STATES - {'seeding', 'cancelled'}


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or utcnow()).isoformat(timespec='seconds')


def infohash(value):
    value = str(value or '').strip().lower()
    return value if re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value) else None


def fresh(observation, now=None):
    try:
        checked = datetime.fromisoformat(observation['checked_at'])
        expires = datetime.fromisoformat(observation['expires_at'])
        now = now or utcnow()
        return checked <= now < expires
    except (KeyError, TypeError, ValueError):
        return False


class SiteView(dict):
    """Legacy parser view with a baseline, so concurrent kinds merge field changes."""
    def __init__(self, records):
        super().__init__(records)
        self.baseline = deepcopy(records)


class MappingStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.lock_path = self.path.with_name(self.path.name + '.lock')

    def try_lock(self):
        handle = open(self.lock_path, 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.lock_path.stat().st_size == 0:
                    handle.write(b'0'); handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except (BlockingIOError, OSError) as exc:
            handle.close()
            if getattr(exc, 'errno', None) not in (11, 13, 35, 36):
                raise
            return None

    @staticmethod
    def unlock(handle):
        # Closing releases flock/msvcrt locks, including on exceptional exits.
        handle.close()

    @contextmanager
    def transaction(self):
        handle = None
        while handle is None:
            handle = self.try_lock()
            if handle is None:
                time.sleep(0.02)
        try:
            yield
        finally:
            self.unlock(handle)

    @staticmethod
    def normalize(data):
        if not isinstance(data, dict) or not isinstance(data.get('torrents'), list):
            raise ValueError('Invalid mapping root')
        version = data.get('schema_version', 1)
        if type(version) is not int or version not in (1, SCHEMA_VERSION):
            raise ValueError('Unsupported mapping schema version')
        for key in ('site_records', 'site_sync', 'clients', 'migrations', 'upload_jobs', 'content_groups'):
            if key not in data:
                data[key] = {}
            if not isinstance(data[key], dict):
                raise ValueError('Invalid mapping ' + key)
        hashes = []
        for torrent in data['torrents']:
            if not isinstance(torrent, dict) or not isinstance(torrent.get('client_info'), dict):
                raise ValueError('Invalid torrent record')
            value = infohash(torrent['client_info'].get('hash'))
            if not value or value in hashes:
                raise ValueError('Invalid or duplicate torrent hash')
            hashes.append(value)
        for key, row in data['site_records'].items():
            if not isinstance(row, dict) or not isinstance(row.get('legacy_state'), dict):
                raise ValueError('Invalid site record: ' + str(key))
            if not isinstance(row.get('obligations'), dict):
                raise ValueError('Invalid obligations: ' + str(key))
            if (not isinstance(row.get('site'), str) or not isinstance(row.get('torrent_id'), str)
                    or key != row['site'] + ':' + row['torrent_id']
                    or (row.get('infohash') is not None and not infohash(row['infohash']))):
                raise ValueError('Invalid site identity')
            for kind, observation in row['obligations'].items():
                if (kind not in KINDS or not isinstance(observation, dict)
                        or observation.get('status') not in ('required', 'clear', 'unknown')):
                    raise ValueError('Invalid obligation evidence')
        for site, kinds in data['site_sync'].items():
            if not isinstance(kinds, dict) or any(not isinstance(v, dict) for v in kinds.values()):
                raise ValueError('Invalid site synchronization evidence')
            for proof in kinds.values():
                if proof.get('verification_method') == 'authenticated_complete_site_page_hashes':
                    hashes, ids = proof.get('required_hashes'), proof.get('required_ids')
                    if (not isinstance(hashes, list) or any(not isinstance(h, str) or infohash(h) != h for h in hashes)
                            or not isinstance(ids, list) or any(not isinstance(tid, str) or not tid.isdigit() for tid in ids)
                            or len(set(ids)) != len(ids) or len(set(hashes)) != len(hashes)):
                        raise ValueError('Invalid complete obligation hash evidence')
        MappingStore._validate_uploads(data)
        data['schema_version'] = SCHEMA_VERSION
        return data

    @staticmethod
    def _validate_uploads(data):
        for key, job in data['upload_jobs'].items():
            if (not isinstance(job, dict) or job.get('id') != key
                    or not re.fullmatch(r'[0-9a-f]{64}', key)
                    or job.get('state') not in UPLOAD_STATES
                    or not infohash(job.get('source_hash'))
                    or job['source_hash'] != infohash(job['source_hash'])
                    or not isinstance(job.get('source_node'), str)
                    or not str(job.get('save_path') or '').startswith('/')
                    or not upload_route_supported(job.get('target_site'), job.get('source_zone'), job.get('target_category'))
                    or job.get('target_zone') != job.get('source_zone')
                    or job.get('content_group_id') not in data['content_groups']
                    or not isinstance(job.get('rule'), dict)):
                raise ValueError('Invalid upload job')
            target = job.get('target_hash')
            if target is not None and (not infohash(target) or target != infohash(target) or target == job['source_hash']):
                raise ValueError('Invalid upload variant hash')
            classification = job.get('classification')
            if classification is not None and (not isinstance(classification, dict)
                    or classification.get('version') != 1 or classification.get('job_id') != key
                    or classification.get('applied') is not False
                    or classification.get('source_hash') != job['source_hash']
                    or not isinstance(classification.get('suggestions'), list)
                    or not isinstance(classification.get('job_fingerprint'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', classification['job_fingerprint'])):
                raise ValueError('Invalid classification suggestions')
            signature = job.get('submission_signature')
            resolution = job.get('duplicate_resolution')
            if resolution is not None and (not isinstance(resolution, dict)
                    or resolution.get('method') != 'existing_destination_torrent'
                    or resolution.get('target_hash') != target
                    or resolution.get('listing') != job.get('listing') or not target):
                raise ValueError('Invalid duplicate resolution')
            category = job.get('category_review')
            if category is not None and (not isinstance(category, dict) or category.get('version') != 1
                    or category.get('status') not in ('ready','review_required')
                    or not isinstance(category.get('issues'), list)
                    or any(not isinstance(v, str) for v in category['issues'])
                    or not isinstance(category.get('applied_fields'), dict)
                    or type(category.get('auto_fix')) is not bool
                    or not isinstance(category.get('job_fingerprint'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', category['job_fingerprint'])):
                raise ValueError('Invalid category review')
            if signature is not None and (not isinstance(signature, str) or not re.fullmatch(r'[0-9a-f]{64}', signature)):
                raise ValueError('Invalid upload submission signature')
            repack = job.get('repack')
            if repack is not None and (not isinstance(repack, dict)
                    or repack.get('provider') not in ('onlyone', 'qbittorrent', 'rutorrent', 'deluge')
                    or repack.get('source_hash') != job['source_hash']
                    or repack.get('source_signature') != job['content_group_id']
                    or not infohash(repack.get('candidate_hash'))
                    or not isinstance(repack.get('candidate_signature'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', repack['candidate_signature'])
                    or not isinstance(repack.get('artifact'), str) or not repack['artifact'].startswith('/')):
                raise ValueError('Invalid rebuilt metainfo evidence')
            request = job.get('repack_request')
            if request is not None and isinstance(request, dict) and request.get('provider') in ('qbittorrent', 'rutorrent', 'deluge'):
                path = request.get('source_path')
                piece = request.get('piece_size')
                if (not isinstance(path, str) or not path.startswith('/') or '\\' in path or '\x00' in path
                        or any(p in ('.', '..') for p in path.split('/'))
                        or type(piece) is not int or not 32768 <= piece <= 64 * 1024**2 or piece & (piece - 1)
                        or not isinstance(request.get('marker'), str)
                        or not re.fullmatch(r'uap-[0-9a-f]{32}', request['marker'])
                        or not isinstance(request.get('claimed_at'), str)):
                    raise ValueError('Invalid torrent creator request')
            elif request is not None and (not isinstance(request, dict) or request.get('provider') not in (None, 'onlyone')
                    or request.get('root') != 'files'
                    or not isinstance(request.get('path'), str) or not request['path']
                    or '/' in request['path'] or request['path'] in ('.', '..')
                    or not isinstance(request.get('name'), str)
                    or not re.fullmatch(r'uap-[0-9a-f]{24}\.torrent', request['name'])):
                raise ValueError('Invalid OnlyOne maker request')
            if 'publication_retry' in job and (
                    not isinstance(job['publication_retry'], dict)
                    or type(job['publication_retry'].get('manual_publish_required')) is not bool):
                raise ValueError('Invalid publication retry guard')
            if 'publication_history' in job and (
                    not isinstance(job['publication_history'], list)
                    or any(not isinstance(item, dict) for item in job['publication_history'])):
                raise ValueError('Invalid publication history')
        for key, group in data['content_groups'].items():
            if (not isinstance(group, dict) or group.get('id') != key
                    or not re.fullmatch(r'[0-9a-f]{64}', key)
                    or not isinstance(group.get('hashes'), list)
                    or any(not infohash(h) or h != infohash(h) for h in group['hashes'])
                    or len(set(group['hashes'])) != len(group['hashes'])):
                raise ValueError('Invalid upload content group')

    def read(self):
        try:
            with self.path.open(encoding='utf-8') as handle:
                return self.normalize(json.load(handle))
        except (OSError, ValueError) as exc:
            raise RuntimeError('Mapping unavailable or invalid; refusing empty fallback') from exc

    def write(self, data):
        """Caller holds transaction lock. Never replace malformed/future schema data."""
        self.normalize(data)
        original = self.path.stat() if self.path.exists() else None
        fd, temp = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, ensure_ascii=False, indent=4)
                handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
            os.chmod(temp, stat.S_IMODE(original.st_mode) if original else 0o600)
            if original and hasattr(os, 'chown'):
                current = os.stat(temp)
                if (current.st_uid, current.st_gid) != (original.st_uid, original.st_gid):
                    os.chown(temp, original.st_uid, original.st_gid)
            os.replace(temp, self.path)
            if os.name != 'nt':
                directory = os.open(self.path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def update(self, mutation):
        with self.transaction():
            data = self.read()
            result = mutation(data)
            self.write(data)
            return result

    def initialize(self, legacy_dir=None, clients=()):
        with self.transaction():
            if not self.path.exists():
                self.write({'torrents': []})
            data = self.read()
            if 'central_site_records_v1' not in data['migrations']:
                legacy = {}
                if legacy_dir and Path(legacy_dir).is_dir():
                    # Read and validate ALL files before writing any imported state.
                    for path in sorted(Path(legacy_dir).glob('*_link_db.json')):
                        rows = json.loads(path.read_text(encoding='utf-8'))
                        if not isinstance(rows, dict) or any(not isinstance(v, dict) for v in rows.values()):
                            raise ValueError('Invalid legacy DB: ' + path.name)
                        legacy[path.name[:-len('_link_db.json')]] = rows
                if self.path.exists():
                    backup = self.path.with_name(self.path.name + '.pre-central.bak')
                    if not backup.exists():
                        # Exclusive creation preserves the first recoverable snapshot.
                        with backup.open('xb') as handle:
                            os.chmod(backup, 0o600)
                            handle.write(self.path.read_bytes()); handle.flush(); os.fsync(handle.fileno())
                for site, rows in legacy.items():
                    self._merge_view(data, site, rows)
                data['migrations']['central_site_records_v1'] = {'completed_at': stamp(), 'legacy_sites': sorted(legacy)}
            for client in clients:
                name = str(client.get('name') or '').strip()
                if name:
                    # No passwords, session cookies, URL credentials or tokens.
                    data['clients'][name] = {'client_type': client.get('type'),
                                             'storage_id': client.get('storage_id'),
                                             'config_ref': name}
                    if client.get('id'):
                        data['clients'][name]['client_id'] = client['id']
            self.write(data)

    def site_view(self, site):
        site = str(site).upper()
        return SiteView({r['torrent_id']: deepcopy(r['legacy_state'])
                         for r in self.read()['site_records'].values() if r['site'] == site})

    def reconcile_verified_listings(self):
        """Bind exact, already verified listing identities; never match titles.

        Adding a binding cannot itself release protection. New records need a
        fresh authenticated obligation observation after this transaction.
        """
        from upload_sites import listing_identity
        def apply(data):
            candidates = {}
            def collect(site, tid, url, target):
                if not infohash(target): return
                try:
                    identity = listing_identity(site, url)
                except (ValueError, KeyError, TypeError):
                    return
                if str(tid) != identity['torrent_id']: return
                key = site + ':' + identity['torrent_id']
                candidates.setdefault(key, {}).setdefault(infohash(target), identity)
            for row in data['torrents']:
                for listing in row.get('site_listings', []):
                    if isinstance(listing, dict) and listing.get('verified') is True:
                        collect(str(listing.get('site_key', '')).upper(), listing.get('torrent_id'),
                                listing.get('details_url'), row['client_info'].get('hash'))
            for job in data['upload_jobs'].values():
                metadata = job.get('metadata', {})
                if metadata.get('site') == 'BEARBIT' and infohash(metadata.get('infohash')) == job['source_hash']:
                    url = metadata.get('details_url')
                    try:
                        identity = listing_identity('BEARBIT', url)
                    except (ValueError, KeyError, TypeError):
                        continue
                    collect('BEARBIT', identity['torrent_id'], url, job['source_hash'])
                if job.get('target_hash') and job.get('listing') and job.get('canonical_artifact'):
                    collect(job['target_site'], job['listing'].get('torrent_id'),
                            job['listing'].get('details_url'), job['target_hash'])
            result = {'created': 0, 'bound': 0, 'conflicts': 0}
            for key, hashes in candidates.items():
                if len(hashes) != 1:
                    result['conflicts'] += 1
                    continue
                target, identity = next(iter(hashes.items()))
                previous = data['site_records'].get(key)
                if previous and previous.get('infohash') not in (None, target):
                    result['conflicts'] += 1
                    continue
                if previous:
                    if previous.get('infohash') is None:
                        previous['infohash'] = target
                        previous['legacy_state']['hash'] = target
                        result['bound'] += 1
                    continue  # Preserve all H&R, Quest and manual-hold evidence.
                site, tid = key.split(':', 1)
                data['site_records'][key] = {'site': site, 'torrent_id': tid, 'infohash': target,
                    'legacy_state': {'hash': target, 'url': identity['details_url']},
                    'obligations': {kind: {'status': 'unknown'} for kind in KINDS},
                    'binding_evidence': {'method': 'verified_mapping_listing', 'checked_at': stamp()}}
                result['created'] += 1
            return result
        return self.update(apply)

    def record_required_snapshot(self, site, kind, required_ids, ttl_seconds=900):
        """Persist a complete read-only obligation page without maintenance actions."""
        if kind not in KINDS or ttl_seconds <= 0:
            raise ValueError('Invalid obligation snapshot')
        site = str(site).upper(); ids = {str(tid) for tid in required_ids}
        if any(not tid.isdigit() for tid in ids):
            raise ValueError('Invalid required listing identity')
        now = utcnow()
        def apply(data):
            proof = {'checked_at': stamp(now), 'expires_at': stamp(now + timedelta(seconds=ttl_seconds)),
                     'verification_method': 'authenticated_complete_site_page', 'status': 'ok'}
            data['site_sync'].setdefault(site, {})[kind] = proof
            for tid in ids:
                data['site_records'].setdefault(site + ':' + tid,
                    {'site': site, 'torrent_id': tid, 'infohash': None, 'legacy_state': {}, 'obligations': {}})
            field = 'hr' if kind == 'hr' else 'seed_quest'
            for record in data['site_records'].values():
                if record['site'] != site: continue
                required = record['torrent_id'] in ids
                record['obligations'][kind] = {**proof, 'status': 'required' if required else 'clear'}
                record['legacy_state'][field] = required
            data['site_sync'][site][kind]['required_ids'] = sorted(ids)
            return {'site': site, 'kind': kind, 'required': len(ids)}
        return self.update(apply)

    def bind_obligation_listing(self, site, torrent_id, target):
        """Bind a hash read from this exact authenticated listing's metainfo."""
        target = infohash(target)
        if not target: raise ValueError('Invalid obligation listing hash')
        key = str(site).upper() + ':' + str(torrent_id)
        def apply(data):
            record = data['site_records'][key]
            if record.get('infohash') not in (None, target):
                raise ValueError('Obligation listing hash conflicts with verified mapping')
            record['infohash'] = target
            record['legacy_state']['hash'] = target
        self.update(apply)

    def finalize_required_snapshot(self, site, kind):
        """Hash membership is usable only when every required listing is bound."""
        site = str(site).upper()
        if kind not in KINDS: raise ValueError('Invalid obligation kind')
        def apply(data):
            proof = data['site_sync'].get(site, {}).get(kind, {})
            ids = proof.get('required_ids')
            if proof.get('status') != 'ok' or not fresh(proof) or not isinstance(ids, list):
                return False
            hashes = [infohash(data['site_records'].get(site + ':' + tid, {}).get('infohash')) for tid in ids]
            if any(h is None for h in hashes): return False
            proof['required_hashes'] = sorted(set(hashes))
            proof['verification_method'] = 'authenticated_complete_site_page_hashes'
            return True
        return self.update(apply)

    @staticmethod
    def _merge_view(data, site, records):
        site = str(site).upper()
        baseline = getattr(records, 'baseline', {})
        if not isinstance(records, dict):
            raise ValueError('Invalid site state')
        for tid, state in records.items():
            if not isinstance(state, dict):
                raise ValueError('Invalid site entry')
            tid = str(tid)
            key = site + ':' + tid
            record = data['site_records'].setdefault(key, {'site': site, 'torrent_id': tid,
                       'infohash': None, 'legacy_state': {}, 'obligations': {}})
            previous = baseline.get(tid, {})
            changes = {k: deepcopy(v) for k, v in state.items() if k not in previous or previous[k] != v}
            scope = getattr(records, 'scope', None)
            if scope == 'hr':
                changes = {k: v for k, v in changes.items() if not k.startswith('seed_quest') and k != 'seed_state'}
            elif scope == 'quest_seed':
                changes = {k: v for k, v in changes.items() if not k.startswith('hr')}

            new_hash = infohash(changes.get('hash'))
            if new_hash:
                if record['infohash'] and record['infohash'] != new_hash:
                    raise ValueError('Conflicting hash for site listing ' + key)
                record['infohash'] = new_hash
            record['legacy_state'].update(changes)
            # A partial parser write may add protection, but never release it.
            for kind, field in (('hr', 'hr'), ('quest_seed', 'seed_quest')):
                if record['legacy_state'].get(field) is True:
                    old = record['obligations'].get(kind, {})
                    if old.get('status') != 'required':
                        record['obligations'][kind] = {'status': 'required', 'checked_at': None,
                            'expires_at': None, 'verification_method': 'legacy_protection'}
            if state.get('status') == 'PROTECTED' and not state.get('hr') and not state.get('seed_quest'):
                record['manual_hold'] = True

    def save_site_view(self, site, records):
        self.update(lambda data: self._merge_view(data, site, records))

    def site_views(self):
        result = {}
        for record in self.read()['site_records'].values():
            result.setdefault(record['site'], {})[record['torrent_id']] = deepcopy(record['legacy_state'])
        return result

    def record_observation(self, site, kind, current_ids, ttl_seconds=900):
        """Only call after a complete authenticated page parse, including empty pages."""
        if kind not in KINDS or ttl_seconds <= 0:
            raise ValueError('Invalid obligation observation')
        site = str(site).upper(); ids = {str(v) for v in current_ids}; now = utcnow()
        def apply(data):
            existing_ids = {r['torrent_id'] for r in data['site_records'].values() if r['site'] == site}
            if not ids <= existing_ids:
                raise ValueError('Observation has unrecorded site listings')
            evidence = {'checked_at': stamp(now), 'expires_at': stamp(now + timedelta(seconds=ttl_seconds)),
                        'verification_method': 'authenticated_complete_site_page', 'status': 'ok'}
            data['site_sync'].setdefault(site, {})[kind] = evidence
            field = 'hr' if kind == 'hr' else 'seed_quest'
            for r in data['site_records'].values():
                if r['site'] != site:
                    continue
                required = r['torrent_id'] in ids and r['legacy_state'].get(field) is True
                r['obligations'][kind] = {**evidence, 'status': 'required' if required else 'clear'}
            required_rows = [r for r in data['site_records'].values()
                             if r['site'] == site and r['obligations'][kind]['status'] == 'required']
            evidence['required_ids'] = sorted(r['torrent_id'] for r in required_rows)
            if all(infohash(r.get('infohash')) for r in required_rows):
                evidence['required_hashes'] = sorted({infohash(r['infohash']) for r in required_rows})
                evidence['verification_method'] = 'authenticated_complete_site_page_hashes'
        self.update(apply)

    def record_failure(self, site, kind):
        if kind not in KINDS:
            raise ValueError('Invalid obligation kind')
        def apply(data):
            previous = data['site_sync'].setdefault(str(site).upper(), {}).setdefault(kind, {})
            previous.update(status='failed', last_failure_at=stamp())
        self.update(apply)

    def set_manual_hold(self, site, torrent_id, enabled, reason=''):
        if type(enabled) is not bool:
            raise ValueError('Manual hold must be Boolean')
        key = str(site).upper() + ':' + str(torrent_id)
        def apply(data):
            record = data['site_records'][key]
            record.update(manual_hold=enabled, manual_hold_reason=str(reason),
                          manual_hold_updated_at=stamp())
        self.update(apply)

    def protection_status(self, torrent_hash, required_sites=(), *, policy_sites=()):
        target = infohash(torrent_hash)
        if not target:
            return 'UNKNOWN'
        data = self.read()
        if any(j['state'] in UPLOAD_HOLD_STATES and target in (j['source_hash'], j.get('target_hash'))
               for j in data['upload_jobs'].values()):
            return 'PROTECTED'
        rows = [r for r in data['site_records'].values() if r.get('infohash') == target]
        sites = {str(s).upper() for s in required_sites}
        policy_sites = set(policy_sites) & {'DEDBIT', 'BITSUSE'}
        sites.update(r['site'] for r in rows)
        if any(r.get('manual_hold') or any(o.get('status') == 'required' for o in r['obligations'].values()) for r in rows):
            return 'PROTECTED'
        if not rows and not sites:
            return 'NOT_FOUND'
        for site in sites:
            linked = [r for r in rows if r['site'] == site]
            # Unresolved protected site listings may refer to this hash.
            if any(r['site'] == site and not r.get('infohash') and
                   (r.get('manual_hold') or any(o.get('status') == 'required' for o in r['obligations'].values()))
                   for r in data['site_records'].values()):
                return 'UNKNOWN'
            if site in policy_sites:
                continue  # Explicit operator policy; never fabricate H&R clear evidence.
            for kind in KINDS:
                sync = data['site_sync'].get(site, {}).get(kind, {})
                if sync.get('status') != 'ok' or not fresh(sync):
                    return 'UNKNOWN'
                # A complete authenticated page with every required listing's
                # hash verified proves absence without guessing a listing ID.
                hashes = sync.get('required_hashes')
                if (sync.get('verification_method') == 'authenticated_complete_site_page_hashes'
                        and isinstance(hashes, list) and all(infohash(h) == h for h in hashes)):
                    if target in hashes: return 'PROTECTED'
                    continue
                if not linked:
                    return 'UNKNOWN'
                if any(r['obligations'].get(kind, {}).get('status') != 'clear' or
                       not fresh(r['obligations'][kind]) for r in linked):
                    return 'UNKNOWN'
        return 'NORMAL'

    def record_removal(self, torrent_hash, node_name, deleted_files):
        target = infohash(torrent_hash)
        def apply(data):
            for record in data['torrents']:
                if infohash(record.get('client_info', {}).get('hash')) != target:
                    continue
                for loc in record.get('node_locations', []):
                    if loc.get('seedbox_host') == node_name:
                        loc.update(present=False, inventory_verified=False, removed_at=stamp())
                record['last_client_action'] = {'node': node_name, 'action': 'removed',
                    'deleted_files': bool(deleted_files), 'confirmed_at': stamp()}
        self.update(apply)
