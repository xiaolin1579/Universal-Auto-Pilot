"""Client capabilities used by the upload worker. Initial implementation: qBittorrent."""
from auto_upload import UploadBlocked
from torrent_store import infohash


class QbitUploadClient:
    def __init__(self, node):
        self.node, self.name = node, node.name

    def request(self, method, endpoint, **kwargs):
        response = self.node._execute_request(method, self.node.url.rstrip('/') + '/api/v2/' + endpoint,
                                              verify=True, allow_redirects=False, timeout=30, **kwargs)
        if response is None or response.status_code != 200:
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
        response = self.request('POST', 'torrents/add',
            files={'torrents': ('cross-seed.torrent', content, 'application/x-bittorrent')},
            data={'paused': 'true', 'stopped': 'true', 'skip_checking': 'false', 'autoTMM': 'false',
                  'savepath': job['save_path'], 'contentLayout': 'Original',
                  'category': job['target_site'], 'tags': 'AutoPilot,Cross-Seed'})
        return response.text.strip().lower() in ('ok.', 'ok')

    def recheck(self, target):
        self.request('POST', 'torrents/recheck', data={'hashes': target})
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
            return response is not None and response.status_code == 200
        return False
