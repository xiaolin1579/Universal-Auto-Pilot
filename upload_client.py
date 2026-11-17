"""Client capabilities used by the upload worker: qBittorrent and rTorrent."""
from auto_upload import UploadBlocked, upload_filename
from torrent_store import infohash
from torrent_metainfo import _extract_torrent_info_hash
from upload_logging import log_event


class QbitUploadClient:
    def __init__(self, node):
        self.node, self.name = node, node.name

    def request(self, method, endpoint, *, expected_statuses=(200,), **kwargs):
        response = self.node._execute_request(method, self.node.url.rstrip('/') + '/api/v2/' + endpoint,
                                              verify=True, allow_redirects=False, timeout=30, **kwargs)
        if response is None or response.status_code not in expected_statuses:
            raise RuntimeError('qBittorrent upload capability request failed: ' + endpoint)
        return response

    def inventory(self, target, missing_ok=False):
        target = infohash(target)
        if not target: raise UploadBlocked('Invalid infohash')
        rows = self.request('GET', 'torrents/info', params={'hashes': target}).json()
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise UploadBlocked('Invalid client inventory')
        if not rows and missing_ok: return None
        if len(rows) != 1 or str(rows[0].get('hash', '')).lower() != target:
            raise UploadBlocked('Torrent missing or ambiguous')
        row = rows[0]
        return {'node': self.name, 'hash': target, 'name': row.get('name'), 'progress': row.get('progress'),
                'amount_left': row.get('amount_left'), 'size_bytes': row.get('total_size'),
                'state': row.get('state'), 'save_path': row.get('save_path')}

    def files(self, target):
        rows = self.request('GET', 'torrents/files', params={'hash': target}).json()
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise UploadBlocked('Invalid file inventory')
        return [{'path': r['name'], 'size_bytes': r['size'], 'progress': r.get('progress')} for r in rows]

    def add_paused(self, content, job):
        target = _extract_torrent_info_hash(content)
        if target != job.get('target_hash'):
            raise UploadBlocked('qBittorrent target artifact hash changed')
        response = self.request('POST', 'torrents/add', expected_statuses=(200, 202),
            files={'torrents': (upload_filename(job), content, 'application/x-bittorrent')},
            data={'paused': 'true', 'stopped': 'true', 'skip_checking': 'false', 'seedMode': 'false', 'autoTMM': 'false',
                  'savepath': job['save_path'], 'contentLayout': 'Original',
                  'category': job['target_site'], 'tags': 'AutoPilot,Cross-Seed'})
        # WebAPI 2.14+ returns a JSON batch summary instead of legacy Ok.
        confirmed = response.status_code == 200 and response.text.strip().lower() in ('ok.', 'ok')
        counts = {}
        if not confirmed:
            try:
                result = response.json()
            except ValueError:
                result = None
            if isinstance(result, dict):
                keys = ('success_count', 'pending_count', 'failure_count')
                if all(type(result.get(k)) is int and result[k] >= 0 for k in keys):
                    counts = {k: result[k] for k in keys}
                    ids = result.get('added_torrent_ids')
                    confirmed = (response.status_code == 200
                        and [counts[k] for k in keys] == [1, 0, 0]
                        and isinstance(ids, list) and len(ids) == 1 and infohash(ids[0]) == target)
        log_event('stage', job, stage='qbit_add_response', status=response.status_code, counts=counts,
                  reason='add_confirmed' if confirmed else 'add_unconfirmed')
        return confirmed

    def recheck(self, target):
        self.request('POST', 'torrents/recheck', expected_statuses=(200, 204), data={'hashes': target})
        return True

    def recheck_complete(self, target):
        props = self.request('GET', 'torrents/properties', params={'hash': target}).json()
        return (type(props.get('pieces_num')) is int and props['pieces_num'] > 0
                and props.get('pieces_have') == props['pieces_num'])

    def resume(self, target):
        # start is qBittorrent 5; resume is the older API. Retry only on explicit 404.
        for endpoint in ('torrents/start', 'torrents/resume'):
            response = self.node._execute_request('POST', self.node.url.rstrip('/') + '/api/v2/' + endpoint,
                data={'hashes': target}, verify=True, allow_redirects=False, timeout=30)
            if response is not None and response.status_code == 404: continue
            return response is not None and response.status_code in (200, 204)
        return False


class RtorrentUploadClient:
    """Strict XML-RPC adapter; never use the node's auto-starting add fallback."""
    COLUMNS = ('d.hash', 'd.name', 'd.size_bytes', 'd.left_bytes', 'd.complete',
               'd.state', 'd.is_active', 'd.hashing', 'd.is_hash_checked',
               'd.hashing_failed', 'd.directory', 'd.is_multi_file',
               'd.size_chunks', 'd.completed_chunks')
    FILE_COLUMNS = ('f.path', 'f.size_bytes', 'f.size_chunks', 'f.completed_chunks')

    def __init__(self, node):
        self.node, self.name = node, node.name
        self._methods = None

    def rpc(self, method, *params):
        import xmlrpc.client
        try:
            response = self.node.s.post(self.node.url,
                data=xmlrpc.client.dumps(params, methodname=method).encode('utf-8'),
                auth=self.node.auth, headers=self.node.headers,
                verify=True, allow_redirects=False, timeout=30)
            if response is None or response.status_code != 200:
                raise UploadBlocked('rTorrent XML-RPC request failed: ' + method)
            values, _ = xmlrpc.client.loads(response.content)
            if len(values) != 1:
                raise UploadBlocked('Invalid rTorrent XML-RPC response: ' + method)
            return values[0]
        except UploadBlocked:
            raise
        except xmlrpc.client.Fault:
            raise UploadBlocked('rTorrent XML-RPC rejected operation: ' + method) from None
        except Exception as exc:
            raise UploadBlocked('rTorrent XML-RPC transport or response failed: ' + type(exc).__name__) from None

    def capabilities(self):
        if self._methods is None:
            methods = self.rpc('system.listMethods')
            if not isinstance(methods, (list, tuple)) or any(not isinstance(m, str) for m in methods):
                raise UploadBlocked('Invalid rTorrent method inventory')
            required = set(self.COLUMNS + self.FILE_COLUMNS) | {
                'd.multicall2', 'f.multicall', 'load.raw', 'd.check_hash',
                'd.stop', 'd.start', 'd.state.set', 'd.custom1.set'}
            available = set(methods)
            setters = available & {'d.directory_base.set', 'd.directory.base.set'}
            if not required <= available or not setters:
                raise UploadBlocked('rTorrent lacks required Auto Upload XML-RPC capabilities')
            self._methods = available
        return self._methods

    @staticmethod
    def _hash(value):
        value = infohash(value)
        if not value or len(value) != 40:
            raise UploadBlocked('rTorrent Auto Upload requires a v1 infohash')
        return value

    @staticmethod
    def _path(value, relative=False):
        import posixpath
        if (not isinstance(value, str) or not value or (relative and value.startswith('/'))
                or (not relative and not value.startswith('/'))
                or any(c in value for c in ('\x00', '\\'))
                or any(p in ('.', '..') for p in value.split('/'))):
            raise UploadBlocked('Unverifiable rTorrent file or directory path')
        return posixpath.normpath(value)

    @staticmethod
    def _number(value):
        if type(value) is not int or value < 0:
            raise UploadBlocked('Invalid rTorrent inventory number')
        return value

    @classmethod
    def _directory(cls, value):
        import re
        directory = cls._path(value)
        # Directory values are embedded in rTorrent's command language at load.
        if re.search(r'["\\$;,`\x00-\x1f\x7f]', directory):
            raise UploadBlocked('rTorrent destination directory contains unsupported command characters')
        return directory

    def inventory(self, target, missing_ok=False):
        self.capabilities()
        target = self._hash(target)
        rows = self.rpc('d.multicall2', '', 'main', *(name + '=' for name in self.COLUMNS))
        if not isinstance(rows, (list, tuple)):
            raise UploadBlocked('Invalid rTorrent torrent inventory')
        found = None
        seen = set()
        for values in rows:
            if not isinstance(values, (list, tuple)) or len(values) != len(self.COLUMNS):
                raise UploadBlocked('Incomplete rTorrent torrent inventory')
            row = dict(zip(self.COLUMNS, values))
            current = self._hash(row['d.hash'])
            if current in seen:
                raise UploadBlocked('Duplicate hash in rTorrent inventory')
            seen.add(current)
            if current != target:
                continue
            for name in self.COLUMNS[2:10] + self.COLUMNS[11:]:
                self._number(row[name])
            if any(row[name] not in (0, 1) for name in ('d.complete', 'd.state', 'd.is_active',
                    'd.is_hash_checked', 'd.hashing_failed', 'd.is_multi_file')):
                raise UploadBlocked('Invalid rTorrent inventory flags')
            size, left = row['d.size_bytes'], row['d.left_bytes']
            chunks, have = row['d.size_chunks'], row['d.completed_chunks']
            name = self._path(row['d.name'], relative=True)
            if '/' in name or name != row['d.name'] or size <= 0 or left > size or chunks <= 0 or have > chunks:
                raise UploadBlocked('Invalid rTorrent torrent layout or completion')
            # Keep rTorrent's actual d.directory, including the multi-file root.
            # The load adapter sets this exact base directory without appending name.
            directory = self._directory(row['d.directory'])
            if row['d.is_multi_file']:
                import posixpath
                if posixpath.basename(directory) != name:
                    raise UploadBlocked('Renamed rTorrent content root is not supported for Auto Upload')
            checking = row['d.hashing'] != 0 or not row['d.is_hash_checked']
            complete = row['d.complete'] == 1 and left == 0 and have == chunks
            state = ('error' if row['d.hashing_failed'] else 'checkingUP' if checking else
                     'uploading' if complete and row['d.is_active'] and row['d.state'] else
                     'pausedUP' if complete and not row['d.is_active'] and not row['d.state'] else
                     'startingUP' if complete and row['d.state'] else
                     'error' if row['d.is_active'] and not row['d.state'] else 'downloading')
            if current == target:
                found = {'node': self.name, 'hash': current, 'name': name,
                    'size_bytes': size, 'amount_left': left,
                    'progress': 1 if complete else min((size - left) / size, have / chunks, 0.999999),
                    'state': state, 'save_path': directory, 'multi_file': bool(row['d.is_multi_file']),
                    'hashing': row['d.hashing'], 'hash_checked': bool(row['d.is_hash_checked']),
                    'hashing_failed': bool(row['d.hashing_failed']), 'pieces_num': chunks, 'pieces_have': have,
                    'rt_state': row['d.state'], 'rt_active': row['d.is_active']}
        if found is None and not missing_ok:
            raise UploadBlocked('Torrent missing from verified rTorrent inventory')
        return found

    def files(self, target):
        target = self._hash(target)
        torrent = self.inventory(target)
        rows = self.rpc('f.multicall', target.upper(), '', *(name + '=' for name in self.FILE_COLUMNS))
        if not isinstance(rows, (list, tuple)) or not rows:
            raise UploadBlocked('Invalid rTorrent file inventory')
        result, seen = [], set()
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != len(self.FILE_COLUMNS):
                raise UploadBlocked('Incomplete rTorrent file inventory')
            path = self._path(row[0], relative=True)
            size, chunks, have = map(self._number, row[1:])
            if have > chunks or (size > 0 and chunks == 0):
                raise UploadBlocked('Invalid rTorrent file completion')
            path = torrent['name'] + '/' + path if torrent['multi_file'] else path
            if path in seen:
                raise UploadBlocked('Duplicate path in rTorrent file inventory')
            seen.add(path)
            completed = (chunks == have and torrent['hash_checked'] and not torrent['hashing']
                         and not torrent['hashing_failed'])
            result.append({'path': path, 'size_bytes': size, 'progress': 1 if completed else 0})
        return result

    def _command(self, method, *params):
        result = self.rpc(method, *params)
        if type(result) is not int or result != 0:
            raise UploadBlocked('rTorrent did not acknowledge operation: ' + method)

    @staticmethod
    def _stopped(row):
        return row['rt_state'] == 0 and row['rt_active'] == 0

    def _wait_inventory(self, target, stopped=False):
        import time
        for attempt in range(5):
            row = self.inventory(target, missing_ok=True)
            if row is not None and (not stopped or self._stopped(row)):
                return row
            if attempt < 4:
                time.sleep(0.25)
        raise UploadBlocked('rTorrent target visibility or stopped state remains unconfirmed; reconcile it explicitly')

    def add_paused(self, content, job):
        import xmlrpc.client
        from torrent_metainfo import _extract_torrent_manifest, payload_signature
        self.capabilities()
        payload_signature(content)
        manifest = _extract_torrent_manifest(content)
        target = self._hash(job.get('target_hash'))
        if manifest['info_hash'] != target:
            raise UploadBlocked('rTorrent target artifact hash changed')
        directory = self._directory(job['save_path'])
        site = job['target_site']
        if site not in ('DEDBIT', 'BITSUSE', 'TORRENTDD'):
            raise UploadBlocked('Unsupported rTorrent upload destination')
        if self.inventory(target, missing_ok=True) is not None:
            raise UploadBlocked('Target hash already exists; reconcile it explicitly')
        setter = 'd.directory_base.set' if 'd.directory_base.set' in self._methods else 'd.directory.base.set'
        self._command('load.raw', '', xmlrpc.client.Binary(content),
                      setter + '="' + directory + '"', 'd.custom1.set=' + site, 'd.state.set=0')
        # Loading can be asynchronous. Retry only reads, never load.raw itself.
        self._wait_inventory(target)
        # Stop only this newly loaded hash, including installations with load hooks.
        self._command('d.stop', target.upper())
        row = self._wait_inventory(target, stopped=True)
        if (not self._stopped(row) or row['save_path'] != directory
                or row['name'] != manifest['root_name'] or row['multi_file'] != manifest['multi_file']):
            raise UploadBlocked('rTorrent paused add layout or stopped state could not be verified')
        return True

    def recheck(self, target):
        target = self._hash(target)
        self.inventory(target)
        self._command('d.stop', target.upper())
        self._wait_inventory(target, stopped=True)
        self._command('d.check_hash', target.upper())
        if not self._stopped(self.inventory(target)):
            raise UploadBlocked('rTorrent started unexpectedly during recheck')
        return True

    def recheck_complete(self, target):
        row = self.inventory(target)
        if row['hashing']:
            return False
        if row['hashing_failed']:
            raise UploadBlocked('rTorrent hash check failed; inspect source data')
        if not row['hash_checked']:
            return False
        if row['progress'] != 1 or row['amount_left'] != 0 or row['pieces_have'] != row['pieces_num']:
            raise UploadBlocked('rTorrent hash check finished with missing or invalid data')
        return True

    def resume(self, target):
        target = self._hash(target)
        row = self.inventory(target)
        if (row['hashing'] or not row['hash_checked'] or row['hashing_failed']
                or row['progress'] != 1 or row['amount_left'] != 0
                or row['pieces_have'] != row['pieces_num']):
            raise UploadBlocked('rTorrent cannot start before a complete verified hash check')
        if row['rt_active'] and row['rt_state']:
            return True
        self._command('d.start', target.upper())
        return True
