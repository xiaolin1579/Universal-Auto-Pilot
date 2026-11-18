"""Bounded, isolated HTTP readers for complete qBittorrent storage snapshots.

Only GETs run concurrently. Every file list is still verified, and changes to
inventory membership or paths during the read invalidate the entire snapshot.
"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import threading

import requests

READ_WORKERS = 4


def bounded_read(items, reader, *, workers=READ_WORKERS, stopped=lambda: False):
    items = list(items)
    if not items: return []
    if type(workers) is not int or not 1 <= workers <= READ_WORKERS:
        raise ValueError('Invalid storage read concurrency')
    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        pending = {}; next_index = 0
        try:
            while pending or next_index < len(items):
                if stopped(): raise ValueError('Storage inventory interrupted by shutdown')
                while next_index < len(items) and len(pending) < workers:
                    if stopped(): raise ValueError('Storage inventory interrupted by shutdown')
                    pending[pool.submit(reader, items[next_index])] = next_index
                    next_index += 1
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                # Inspect every completed result before scheduling more work.
                for future in done:
                    index = pending.pop(future)
                    results[index] = future.result()
        finally:
            for future in pending: future.cancel()
    if stopped(): raise ValueError('Storage inventory interrupted by shutdown')
    return results


def _clone_session(source):
    # Sessions/cookie jars are not shared between workers. Preserve the actual
    # node's proxy, certificate, headers and authenticated cookie scope.
    session = requests.Session()
    session.headers.update(source.headers)
    session.headers['Connection'] = 'close'
    session.cookies.update(source.cookies.copy())
    session.auth = source.auth
    session.proxies.update(source.proxies)
    session.cert = source.cert
    session.trust_env = source.trust_env
    return session


class ReadSessions:
    def __init__(self, source):
        self.source = source; self.local = threading.local()
        self.sessions = []; self.lock = threading.Lock()

    def get(self, url):
        session = getattr(self.local, 'session', None)
        if session is None:
            session = _clone_session(self.source)
            self.local.session = session
            with self.lock: self.sessions.append(session)
        return session.get(url, verify=True, allow_redirects=False, timeout=15)

    def close(self):
        for session in self.sessions: session.close()


def _json(response):
    if response is None: raise ValueError('Storage API response unavailable')
    try:
        if response.status_code != 200: raise ValueError('Storage API request failed')
        try:
            return response.json()
        except Exception:
            raise ValueError('Invalid storage API JSON') from None
    finally:
        response.close()


def metadata(node, normalize_path, stopped):
    if stopped(): raise ValueError('Storage inventory interrupted by shutdown')
    rows = _json(node._execute_request('GET', node.url + '/api/v2/torrents/info',
                                     verify=True, allow_redirects=False, timeout=15))
    if not isinstance(rows, list): raise ValueError('Invalid storage inventory')
    import re
    seen = set(); result = []
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Invalid storage inventory row')
        digest = str(row.get('hash', '')).lower()
        if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', digest) or digest in seen:
            raise ValueError('Ambiguous storage inventory')
        seen.add(digest)
        result.append({'hash': digest, 'root': normalize_path(row.get('content_path')),
                       'save_path': normalize_path(row.get('save_path'))})
    return result


def roots_overlap(node, target, normalize_path, stopped):
    """Early refusal only; this metadata can never authorize data deletion."""
    rows = metadata(node, normalize_path, stopped)
    matches = [r for r in rows if r['hash'] == str(target).lower()]
    if len(matches) != 1: raise ValueError('Storage target is absent or ambiguous')
    root = matches[0]['root']
    return any(r is not matches[0] and (r['root'] == root or r['root'].startswith(root + '/')
               or root.startswith(r['root'] + '/')) for r in rows)


def qbit_snapshot(node, normalize_path, normalize_file, stopped, *, workers=READ_WORKERS):
    rows = metadata(node, normalize_path, stopped)
    sessions = ReadSessions(node.s)
    try:
        def read(row):
            if stopped(): raise ValueError('Storage inventory interrupted by shutdown')
            files = _json(sessions.get(node.url + '/api/v2/torrents/files?hash=' + row['hash']))
            if (not isinstance(files, list) or not files
                    or any(not isinstance(f, dict) or not isinstance(f.get('name'), str) for f in files)):
                raise ValueError('Incomplete file inventory')
            paths = {normalize_file(row['save_path'], f['name']) for f in files}
            if any(p != row['root'] and not p.startswith(row['root'] + '/') for p in paths):
                raise ValueError('File lies outside verified content root')
            return {'hash': row['hash'], 'root': row['root'], 'files': paths}
        result = bounded_read(rows, read, workers=workers, stopped=stopped)
        # Never accept a partial/mixed snapshot if another job was added,
        # removed or moved while the parallel file requests were in progress.
        after = metadata(node, normalize_path, stopped)
        key = lambda r: (r['hash'], r['root'], r['save_path'])
        if sorted(map(key, rows)) != sorted(map(key, after)):
            raise ValueError('Storage inventory changed during file verification')
        return result
    finally:
        sessions.close()
