"""Deluge Web JSON-RPC / daemon SSL-RPC adapter for 1.3.x and 2.x."""
import base64
import math
import posixpath
import threading
import time
from urllib.parse import urlsplit

import requests

from auto_upload import UploadBlocked, upload_filename
from deluge_rpc import DaemonRPC, DaemonRPCError
from torrent_metainfo import _extract_torrent_manifest, payload_signature, verify_completed
from torrent_store import infohash


class DelugeError(UploadBlocked):
    """Safe error: never includes credentials, remote response text or torrent bytes."""


class _SessionExpired(DelugeError):
    pass


def _number(value, integer=False):
    try:
        valid = (type(value) in ((int,) if integer else (int, float)) and math.isfinite(value) and value >= 0)
    except OverflowError:
        valid = False
    if not valid:
        raise DelugeError('Invalid Deluge numeric status')
    return value


def _ratio(value):
    return 0 if type(value) in (int, float) and value == -1 else _number(value)


def _path(value, relative=False):
    if (not isinstance(value, str) or not value or '\x00' in value or '\\' in value
            or value.startswith('/') == relative or any(p in ('.', '..') for p in value.split('/'))):
        raise DelugeError('Unverified Deluge file or download path')
    return posixpath.normpath(value)


def _hash(value):
    value = infohash(value)
    if not value:
        raise DelugeError('Invalid Deluge torrent hash')
    return value


class DelugeNode:
    client_type = 'deluge'
    KEYS = ['name', 'total_size', 'total_done', 'progress', 'is_seed', 'paused', 'state',
            'save_path', 'download_location', 'files', 'file_progress', 'is_auto_managed',
            'move_completed', 'stop_at_ratio', 'remove_at_ratio', 'trackers', 'ratio',
            'time_added', 'completed_time', 'seeding_time', 'upload_payload_rate',
            'download_payload_rate', 'num_peers', 'total_uploaded', 'all_time_download', 'label']

    def __init__(self, cfg, *, storage_reader=None, mapper=None):
        self.name = cfg['name']; self.url = cfg['url'].rstrip('/')
        self.client_id = cfg.get('id')
        try:
            parsed = urlsplit(self.url)
            parsed.port
        except ValueError:
            raise DelugeError('Invalid Deluge connection URL') from None
        self._daemon_rpc = None
        if parsed.scheme == 'deluge+ssl':
            if cfg.get('daemon_id') is not None or cfg.get('nginx') or cfg.get('http_basic_user') is not None:
                raise DelugeError('Daemon RPC does not use daemon_id or HTTP Basic authentication')
            try:
                self._daemon_rpc = DaemonRPC(self.url, cfg.get('rpc'))
            except DaemonRPCError as exc:
                raise DelugeError(str(exc)) from None
            if not isinstance(cfg.get('deluge_user'), str) or not cfg['deluge_user'].strip():
                raise DelugeError('Daemon RPC requires a Deluge auth-file username')
        elif (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise DelugeError('Invalid Deluge Web URL')
        elif 'rpc' in cfg:
            raise DelugeError('Deluge RPC settings require a daemon RPC URL')
        self.endpoint = self.url if parsed.path.endswith('/json') else self.url + '/json'
        self.username = cfg.get('deluge_user', '')
        self.password = cfg.get('deluge_pass', '')
        if not isinstance(self.password, str):
            raise DelugeError('Deluge Web password must be text')
        self.daemon_id = cfg.get('daemon_id')
        self.s = requests.Session()
        if cfg.get('nginx') or cfg.get('http_basic_user') is not None:
            self.s.auth = (cfg.get('http_basic_user', ''), cfg.get('http_basic_pass', ''))
        self._lock = threading.RLock(); self._request_id = 0
        self.methods = set(); self.version = 'unknown'; self.is_connected = False
        self.quota_gb = cfg.get('quota_gb', 0); self.storage_api = cfg.get('storage_api', '')
        self.storage_mount = cfg.get('storage_mount', '/')
        self.storage_safety_buffer_gb = cfg.get('storage_safety_buffer_gb', 15)
        self.storage_reader = cfg.get('_storage_reader'); self._read_shared_storage = storage_reader
        self._mapper = mapper
        self.allow_space_reclaim = cfg.get('allow_space_reclaim', False) is True
        self.preallocated_downloads_accounted_in_free_space = cfg.get('preallocated_downloads_accounted_in_free_space') is True
        self.free_gb = 0; self.jobs = 0; self.pending_gb = 0; self.stat_msg = 'Not authenticated'
        self._clear_storage()

    def _clear_storage(self):
        self.storage_source = None
        for prefix in ('storage', 'quota', 'disk'):
            for field in ('used', 'total', 'free'):
                setattr(self, prefix + '_' + field + '_gb', None)

    def _raw_rpc(self, method, params=()):
        with self._lock:
            if self._daemon_rpc is not None:
                try:
                    return self._daemon_rpc.call(method, params)
                except DaemonRPCError as exc:
                    self.is_connected = False
                    raise DelugeError(str(exc)) from None
            self._request_id += 1
            ident = self._request_id
            try:
                response = self.s.post(self.endpoint, json={'method': method, 'params': list(params), 'id': ident},
                                       timeout=30, verify=True, allow_redirects=False)
                if response is None or response.status_code != 200:
                    raise DelugeError('Deluge Web request failed: ' + method)
                data = response.json()
                if not isinstance(data, dict) or type(data.get('id')) is not int or data['id'] != ident:
                    raise DelugeError('Invalid Deluge JSON-RPC response identity')
                error = data.get('error')
                if error is not None:
                    if isinstance(error, dict) and error.get('code') == 1:
                        raise _SessionExpired('Deluge Web session expired')
                    raise DelugeError('Deluge JSON-RPC rejected operation: ' + method)
                if 'result' not in data:
                    raise DelugeError('Deluge JSON-RPC result missing')
                return data['result']
            except DelugeError:
                raise
            except Exception as exc:
                raise DelugeError('Deluge transport or response failed: ' + type(exc).__name__) from None

    def _check_daemon(self):
        if self._daemon_rpc is not None:
            return  # The TLS socket is bound to the configured daemon, independent of Web UI.
        if self._raw_rpc('web.connected') is not True:
            raise DelugeError('Deluge daemon disconnected')
        status = self._raw_rpc('web.get_host_status', [self.daemon_id])
        # 1.3.x: (id, hostname, port, status, version); 2.x: (id, status, version).
        state = status[1] if isinstance(status, (list, tuple)) and len(status) == 3 else (
            status[3] if isinstance(status, (list, tuple)) and len(status) == 5 else None)
        if (not isinstance(status, (list, tuple)) or not status or status[0] != self.daemon_id
                or state != 'Connected'):
            raise DelugeError('Deluge Web is not connected to the configured daemon')
        self._set_version(status[-1])

    def _set_version(self, version):
        import re
        if isinstance(version, str) and re.fullmatch(r'[0-9][a-zA-Z0-9.+_-]{0,50}', version):
            self.version = version

    def login(self):
        with self._lock:
            self.is_connected = False
            try:
                if self._daemon_rpc is not None:
                    return self._login_rpc()
                if self._raw_rpc('auth.login', [self.password]) is not True:
                    raise DelugeError('Deluge Web authentication rejected')
                hosts = self._raw_rpc('web.get_hosts')
                if (not isinstance(hosts, list) or any(not isinstance(h, (list, tuple)) or len(h) < 3
                        or not isinstance(h[0], str) or not h[0] for h in hosts)):
                    raise DelugeError('Invalid Deluge daemon host list')
                ids = [h[0] for h in hosts]
                if len(ids) != len(set(ids)):
                    raise DelugeError('Ambiguous Deluge daemon host list')
                if self.daemon_id is None:
                    if len(ids) != 1:
                        raise DelugeError('Set daemon_id when Deluge Web has multiple daemon hosts')
                    self.daemon_id = ids[0]
                if self.daemon_id not in ids:
                    raise DelugeError('Configured Deluge daemon host was not found')
                if self._raw_rpc('web.connected') is False:
                    self._raw_rpc('web.connect', [self.daemon_id])
                self._check_daemon()
                methods = self._raw_rpc('system.listMethods')
                if not isinstance(methods, list) or any(not isinstance(m, str) for m in methods):
                    raise DelugeError('Invalid Deluge capability list')
                self.methods = set(methods)
                if 'core.get_torrents_status' not in self.methods:
                    raise DelugeError('Deluge torrent inventory capability unavailable')
                if 'daemon.info' in self.methods:
                    self._set_version(self._raw_rpc('daemon.info'))
                self.is_connected = True
                print(f'✅ [{self.name}] Login successful: Deluge={self.version}, Web JSON-RPC')
                return True
            except DelugeError as exc:
                if self._daemon_rpc is not None:
                    self._daemon_rpc.close()
                self.stat_msg = str(exc)
                print(f'⚠️ [{self.name}] {exc}')
                return False

    def _login_rpc(self):
        try:
            self.methods = set()
            self.version = 'unknown'
            self._daemon_rpc.connect()
            kwargs = {} if self._daemon_rpc.legacy else {'client_version': 'Universal-Auto-Pilot'}
            level = self._daemon_rpc.call('daemon.login', [self.username, self.password], **kwargs)
            if type(level) is not int or level < 5:
                raise DelugeError('Deluge daemon authentication rejected or insufficient permissions')
            self._set_version(self._raw_rpc('daemon.info'))
            if not self.version.startswith(('1.3.', '2.0.', '2.1.', '2.2.')):
                raise DelugeError('Deluge daemon version is outside the supported Swizzin version families')
            if self._daemon_rpc.legacy != self.version.startswith('1.3.'):
                raise DelugeError('Deluge daemon version does not match rpc.protocol')
            methods = self._raw_rpc('daemon.get_method_list')
            if not isinstance(methods, (list, tuple)) or any(not isinstance(m, str) for m in methods):
                raise DelugeError('Invalid Deluge capability list')
            self.methods = set(methods)
            if 'core.get_torrents_status' not in self.methods:
                raise DelugeError('Deluge torrent inventory capability unavailable')
            self.is_connected = True
            print(f'✅ [{self.name}] Login successful: Deluge={self.version}, Daemon SSL-RPC')
            return True
        except DaemonRPCError as exc:
            raise DelugeError(str(exc)) from None

    def close(self):
        with self._lock:
            self.is_connected = False
            if self._daemon_rpc is not None:
                self._daemon_rpc.close()
            self.s.close()

    def rpc(self, method, params=(), *, mutation=False):
        with self._lock:
            if not self.is_connected:
                raise DelugeError('Deluge is not authenticated')
            if method not in self.methods:
                raise DelugeError('Deluge capability unavailable: ' + method)
            try:
                self._check_daemon()
                return self._raw_rpc(method, params)
            except _SessionExpired:
                self.is_connected = False
                # A mutation must never be replayed after an ambiguous response.
                if mutation or not self.login():
                    raise DelugeError('Deluge session expired; operation was not retried') from None
                self._check_daemon()
                return self._raw_rpc(method, params)

    def _statuses(self):
        data = self.rpc('core.get_torrents_status', [{}, self.KEYS])
        if not isinstance(data, dict):
            raise DelugeError('Invalid Deluge torrent inventory')
        result = {}
        for value, row in data.items():
            target = _hash(value)
            if target in result or not isinstance(row, dict):
                raise DelugeError('Ambiguous Deluge torrent inventory')
            result[target] = row
        return result

    @staticmethod
    def normalize(target, row):
        size = _number(row.get('total_size'), True); done = _number(row.get('total_done'), True)
        progress = _number(row.get('progress'))
        if progress > 100 or done > size or any(type(row.get(k)) is not bool for k in ('is_seed', 'paused')):
            raise DelugeError('Invalid Deluge completion status')
        state = row.get('state')
        if state not in ('Allocating', 'Checking', 'Downloading', 'Seeding', 'Paused', 'Queued', 'Error', 'Moving'):
            raise DelugeError('Unrecognized Deluge torrent state')
        complete = (size > 0 and row['is_seed'] and size == done and progress == 100
                    and state in ('Seeding', 'Paused', 'Queued'))
        path = row.get('download_location', row.get('save_path'))
        if row.get('download_location') is not None and row.get('save_path') is not None and row['download_location'] != row['save_path']:
            raise DelugeError('Conflicting Deluge download path aliases')
        name = row.get('name')
        if not isinstance(name, str) or not name:
            raise DelugeError('Deluge torrent name missing')
        normalized = ('checkingUP' if state == 'Checking' else 'moving' if state == 'Moving' else
                      'error' if state == 'Error' else 'metaDL' if not size else
                      'pausedUP' if complete and row['paused'] else
                      'uploading' if complete and state == 'Seeding' else
                      'queuedUP' if complete else 'pausedDL' if row['paused'] else 'downloading')
        return {'hash': target, 'name': name, 'size_bytes': size, 'size': size / 1024 ** 3,
                'progress': 1 if complete else min(progress / 100, 0.999999),
                'amount_left': size - done, 'state': normalized, 'save_path': _path(path),
                'paused': row['paused'], 'is_seed': row['is_seed']}

    @staticmethod
    def file_rows(row):
        files, progress = row.get('files'), row.get('file_progress')
        if (not isinstance(files, list) or not files or not isinstance(progress, list)
                or len(files) != len(progress)):
            raise DelugeError('Incomplete Deluge file inventory')
        result, paths, indices = [], set(), set()
        for file in files:
            if not isinstance(file, dict):
                raise DelugeError('Invalid Deluge file row')
            index = _number(file.get('index'), True)
            path = _path(file.get('path'), relative=True)
            if index >= len(progress) or index in indices or path in paths:
                raise DelugeError('Ambiguous Deluge file inventory')
            part = _number(progress[index])
            if part > 1:
                raise DelugeError('Invalid Deluge file progress')
            result.append({'path': path, 'size_bytes': _number(file.get('size'), True), 'progress': part})
            paths.add(path); indices.add(index)
        return result

    def get_all_torrents_info(self):
        try:
            result = []
            for target, row in self._statuses().items():
                item = self.normalize(target, row)
                item.update(ratio=_ratio(row.get('ratio', 0)),
                    up_speed=_number(row.get('upload_payload_rate', 0)), dlspeed=_number(row.get('download_payload_rate', 0)),
                    leechers=_number(row.get('num_peers', 0)), added_on=_number(row.get('time_added', 0)),
                    ts_finished=_number(row.get('completed_time', 0)), is_rt_complete=item['progress'] == 1)
                result.append(item)
            return result
        except DelugeError as exc:
            print(f'⚠️ [{self.name}] Inventory unavailable: {exc}')
            return None

    def get_torrent_by_hash(self, target):
        try:
            target = _hash(target); row = self._statuses().get(target)
            return self.normalize(target, row) if row is not None else False
        except DelugeError:
            return None

    def is_torrent_exists(self, target):
        row = self.get_torrent_by_hash(target)
        return None if row is None else row is not False

    def get_torrent_file_manifest(self, target):
        try:
            row = self._statuses().get(_hash(target))
            return self.file_rows(row) if row is not None else None
        except DelugeError:
            return None

    def refresh_status(self):
        self.free_gb = 0; self._clear_storage()
        try:
            rows = self.get_all_torrents_info()
            if rows is None:
                raise DelugeError('Deluge inventory could not be verified')
            physical = None
            if 'core.get_free_space' in self.methods:
                directories = sorted({r['save_path'] for r in rows}) or [None]
                physical = min(_number(self.rpc('core.get_free_space', [p]), True) / 1024 ** 3 for p in directories)
            buffer = _number(self.storage_safety_buffer_gb)
            selected_label = ''
            if self.storage_api == 'swizzin':
                if self.storage_reader is None or self._read_shared_storage is None:
                    raise DelugeError('Deluge Swizzin telemetry needs an explicit configured reader')
                storage = self._read_shared_storage(self.storage_reader, self.storage_mount)
                self.storage_source = storage['source']
                for prefix in ('storage', 'quota', 'disk'):
                    for field in ('used', 'total', 'free'):
                        key = field + '_gb'
                        setattr(self, prefix + '_' + key, storage.get(key) if prefix == 'storage' else storage.get(prefix + '_' + key))
                available = storage['free_gb'] if physical is None else min(storage['free_gb'], physical)
                selected_label = f'FREE({"Q" if storage["source"] == "quota" else "M"}): {storage["free_gb"]:.2f}GiB | '
            else:
                if physical is None:
                    raise DelugeError('Deluge storage telemetry unavailable')
                available = physical
                if self.quota_gb > 0:
                    available = min(available, max(0, self.quota_gb - sum(r['size_bytes'] - r['amount_left'] for r in rows) / 1024 ** 3))
            self.free_gb = max(0, available - buffer)
            self.jobs = len(rows); self.pending_gb = sum(r['amount_left'] for r in rows) / 1024 ** 3
            native = f'{physical:.2f}GiB' if physical is not None else 'N/A'
            self.stat_msg = (f'Deluge {self.version} | Source: {self.storage_source or "client"} | '
                f'{selected_label}FREE(D): {native} | Buffer: {buffer:.2f}GiB | Safe: {self.free_gb:.2f}GiB | Total: {len(rows)}')
            return True
        except (DelugeError, ValueError, TypeError) as exc:
            self._clear_storage(); self.free_gb = 0
            self.stat_msg = 'API ERROR: Deluge storage refresh failed (' + type(exc).__name__ + ')'
            return False

    def _targets(self, targets):
        values = [targets] if isinstance(targets, str) else targets
        if not isinstance(values, (list, tuple)) or not values:
            raise DelugeError('A nonempty explicit Deluge torrent list is required')
        return list(dict.fromkeys(_hash(value) for value in values))

    def _action(self, action, targets):
        ids = self._targets(targets)
        plural = 'core.' + action + '_torrents'
        method = plural if plural in self.methods else 'core.' + action + '_torrent'
        result = self.rpc(method, [ids], mutation=True)
        if result is not None and result is not True:
            raise DelugeError('Deluge did not acknowledge torrent action')
        return True

    def stop_torrent(self, targets):
        return self._action('pause', targets)

    def resume_torrent(self, targets):
        return self._action('resume', targets)

    def reannounce_torrent(self, targets):
        result = self.rpc('core.force_reannounce', [self._targets(targets)], mutation=True)
        return result is None or result is True

    def reannounce_all(self):
        ids = list(self._statuses())
        return self.reannounce_torrent(ids) if ids else True

    def delete_torrent(self, target, delete_files=False):
        target = _hash(target)
        return self.rpc('core.remove_torrent', [target, delete_files is True], mutation=True) is True

    def get_purge_snapshot(self, target):
        try:
            target = _hash(target); row = self._statuses().get(target)
            if row is None:
                return [], []
            trackers = row.get('trackers')
            if not isinstance(trackers, list):
                raise DelugeError('Deluge tracker inventory unavailable')
            return [self.normalize(target, row)], trackers
        except DelugeError:
            return None, None

    def storage_inventory(self):
        result = []
        for target, row in self._statuses().items():
            item = self.normalize(target, row); files = self.file_rows(row)
            first = files[0]['path'].split('/')[0]
            if any(f['path'].split('/')[0] != first for f in files):
                raise DelugeError('Deluge content root is ambiguous')
            root = _path(posixpath.join(item['save_path'], first))
            result.append({'hash': target, 'root': root,
                           'files': {_path(posixpath.join(item['save_path'], f['path'])) for f in files}})
        return result

    def get_active_downloads(self):
        rows = self.get_all_torrents_info()
        if rows is None:
            raise DelugeError('Deluge active inventory unverified')
        return [r for r in rows if r['progress'] < 1 and r['state'] not in ('pausedDL', 'error')]

    def get_downloading_size(self):
        if self.preallocated_downloads_accounted_in_free_space:
            return 0
        return sum(r['amount_left'] for r in self.get_active_downloads()) / 1024 ** 3

    async def sync_to_mapping(self, web_data_list=None, mapper_func=None):
        try:
            raw = self._statuses()
            items = {h: {**self.normalize(h, row), 'ratio': _ratio(row.get('ratio', 0))} for h, row in raw.items()}
            requests_ = [(h, {}) for h in items]
            if web_data_list is not None:
                if not isinstance(web_data_list, (list, tuple)):
                    return False
                requests_ = []
                for web in web_data_list:
                    if not isinstance(web, dict) or _hash(web.get('hash')) not in items:
                        return False
                    requests_.append((_hash(web['hash']), web))
            mapper = mapper_func or self._mapper
            if not callable(mapper):
                return False
            for target, web in requests_:
                row = items[target]
                client = {'client_type': 'deluge', 'hash': target.upper(), 'name': row['name'],
                          'size_bytes': row['size_bytes'], 'state': row['state'],
                          'ratio': row['ratio']}
                location = {'seedbox_host': self.name, 'save_path': row['save_path'],
                            'is_active': not row['paused'], 'verification_method': 'live_client_inventory',
                            'label': raw[target].get('label', '')}
                if await mapper(client_data=client, web_data=web, node_data=location) is not True:
                    return False
            return True
        except DelugeError:
            return False

    def get_stats_by_site(self):
        result = {}
        for row in self._statuses().values():
            site = row.get('label') or 'Uncategorized'
            stats = result.setdefault(site, {'total_up_bytes': 0, 'total_dl_bytes': 0, 'current_speed_bytes': 0, 'count': 0})
            stats['total_up_bytes'] += _number(row.get('total_uploaded', 0))
            stats['total_dl_bytes'] += _number(row.get('all_time_download', 0))
            stats['current_speed_bytes'] += _number(row.get('upload_payload_rate', 0)); stats['count'] += 1
        return result

    def add_file(self, filename, content, options):
        # Deluge 2.x exposes the asynchronous method; 1.3.x uses the original name.
        method = next((m for m in ('core.add_torrent_file_async', 'core.add_torrent_file') if m in self.methods), None)
        if method is None:
            raise DelugeError('Deluge add torrent capability unavailable')
        return self.rpc(method, [filename, base64.b64encode(content).decode('ascii'), options], mutation=True)

    def add(self, content, site_name='Universal', size=None, n_cfg=None, save_path=None,
            skip_checking=False, paused=False, **kwargs):
        if skip_checking:
            raise DelugeError('Deluge add never skips hash checking')
        manifest = _extract_torrent_manifest(content); target = manifest['info_hash']
        if self.get_torrent_by_hash(target) is not False:
            return False
        options = {'add_paused': True, 'auto_managed': False, 'move_completed': False,
                   'stop_at_ratio': False, 'remove_at_ratio': False, 'seed_mode': False}
        if save_path:
            options['download_location'] = _path(save_path)
        result = self.add_file('download.torrent', content, options)
        if result != target:
            raise DelugeError('Deluge add result unconfirmed; verify inventory before retry')
        if not paused:
            self.resume_torrent(target)
        return True


class DelugeUploadClient:
    def __init__(self, node):
        self.node, self.name = node, node.name

    def _capabilities(self):
        needed = {'core.get_torrents_status', 'core.force_recheck', 'core.set_torrent_options'}
        if (not needed <= self.node.methods or not any(m in self.node.methods for m in ('core.pause_torrents', 'core.pause_torrent'))
                or not any(m in self.node.methods for m in ('core.add_torrent_file_async', 'core.add_torrent_file'))
                or not any(m in self.node.methods for m in ('core.resume_torrents', 'core.resume_torrent'))):
            raise DelugeError('Deluge Auto Upload capabilities are incomplete')

    def inventory(self, target, missing_ok=False):
        self._capabilities(); target = _hash(target)
        row = self.node._statuses().get(target)
        if row is None:
            if missing_ok: return None
            raise DelugeError('Torrent missing from verified Deluge inventory')
        if any(type(row.get(key)) is not bool for key in ('is_auto_managed', 'move_completed', 'stop_at_ratio', 'remove_at_ratio')):
            raise DelugeError('Deluge Auto Upload safety options cannot be verified')
        return {**self.node.normalize(target, row), 'node': self.name}

    def files(self, target):
        row = self.node._statuses().get(_hash(target))
        if row is None: raise DelugeError('Deluge source file inventory unavailable')
        return self.node.file_rows(row)

    def _wait_inventory(self, target, paused=False):
        for attempt in range(5):
            row = self.inventory(target, missing_ok=True)
            if row is not None and (not paused or row['paused']):
                return row
            if attempt < 4: time.sleep(.1)
        raise DelugeError('Deluge target visibility or pause remains unconfirmed; reconcile explicitly')

    def add_paused(self, content, job):
        self._capabilities(); payload_signature(content)
        manifest = _extract_torrent_manifest(content); target = _hash(job['target_hash'])
        if target != manifest['info_hash'] or self.inventory(target, missing_ok=True) is not None:
            raise DelugeError('Deluge target already exists or artifact hash changed; reconcile explicitly')
        path = _path(job['save_path'])
        options = {'add_paused': True, 'auto_managed': False, 'download_location': path,
                   'move_completed': False, 'stop_at_ratio': False, 'remove_at_ratio': False, 'seed_mode': False}
        result = self.node.add_file(upload_filename(job), content, options)
        if result != target:
            raise DelugeError('Deluge paused add result unconfirmed; reconcile explicitly')
        self._wait_inventory(target)
        self.node.stop_torrent(target)
        raw = self.node._statuses().get(target)
        if raw is None or any(raw.get(key) is not False for key in ('is_auto_managed', 'move_completed', 'stop_at_ratio', 'remove_at_ratio')):
            raise DelugeError('Deluge did not apply Auto Upload safety options')
        row = self._wait_inventory(target, paused=True)
        if not row['paused'] or row['save_path'] != path:
            raise DelugeError('Deluge paused add path or paused state unverified')
        return True

    def recheck(self, target):
        target = _hash(target)
        self.node.rpc('core.set_torrent_options', [[target], {'auto_managed': False, 'move_completed': False,
                      'stop_at_ratio': False, 'remove_at_ratio': False}], mutation=True)
        raw = self.node._statuses().get(target)
        if raw is None or any(raw.get(k) is not False for k in ('is_auto_managed', 'move_completed', 'stop_at_ratio', 'remove_at_ratio')):
            raise DelugeError('Deluge target safety options could not be verified before recheck')
        self.node.stop_torrent(target)
        self._wait_inventory(target, paused=True)
        result = self.node.rpc('core.force_recheck', [[target]], mutation=True)
        if result is not None and result is not True:
            raise DelugeError('Deluge recheck request unconfirmed')
        observed = False
        for attempt in range(10):
            row = self.inventory(target)
            if row['state'].startswith('checking'):
                observed = True; break
            if attempt < 9: time.sleep(.1)
        # Persist this observation in the worker. A seed flag alone is not fresh recheck evidence.
        return {'accepted': True, 'observed': observed}

    def recheck_complete(self, target):
        if self.inventory(target)['state'] == 'error':
            raise DelugeError('Deluge hash check failed; inspect the target data')
        return False

    def resume(self, target):
        target = _hash(target); row = self.inventory(target)
        if row['progress'] != 1 or row['state'].startswith(('checking', 'error', 'moving')):
            raise DelugeError('Deluge cannot resume before complete verified data')
        raw = self.node._statuses().get(target)
        if raw is None or any(raw.get(k) is not False for k in ('is_auto_managed', 'move_completed', 'stop_at_ratio', 'remove_at_ratio')):
            raise DelugeError('Deluge target safety options changed before resume')
        if not row['paused']:
            return True
        return self.node.resume_torrent(target)
