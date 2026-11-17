"""Capability-detected remote torrent creators. Never add or start a torrent."""
import base64
import posixpath
import re
from urllib.parse import urlsplit

from auto_upload import UploadBlocked

READ_LIMIT = 16 * 1024**2


def remote_path(value):
    if (not isinstance(value, str) or not value.startswith('/') or '\\' in value
            or '\x00' in value or any(p in ('.', '..') for p in value.split('/'))):
        raise UploadBlocked('Torrent creator source path is invalid')
    return value.rstrip('/') or '/'


def response_data(response, *, binary=False):
    try:
        if binary:
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > READ_LIMIT:
                    raise UploadBlocked('Torrent creator result exceeds read limit')
            return bytes(content)
        return response.json()
    except (ValueError, TypeError):
        raise UploadBlocked('Torrent creator returned an invalid response') from None
    finally:
        response.close()


class QbitCreator:
    provider = 'qbittorrent'

    def __init__(self, client):
        self.node = client.node

    def request(self, route, *, data=None, binary=False, probe=False):
        try:
            response = self.node._execute_request('GET' if data is None else 'POST',
                self.node.url.rstrip('/') + '/api/v2/torrentcreator/' + route,
                **({'data': data} if data is not None else {}),
                verify=True, allow_redirects=False, timeout=30, stream=binary)
        except Exception:
            raise UploadBlocked('qBittorrent creator request was not confirmed') from None
        if response is not None and probe and response.status_code in (404, 501):
            response.close()
            return None
        if response is None or response.status_code != 200:
            if response is not None: response.close()
            raise UploadBlocked('qBittorrent creator request was not confirmed')
        return response_data(response, binary=binary)

    def tasks(self, probe=False):
        rows = self.request('status', probe=probe)
        if rows is None: return None
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise UploadBlocked('Invalid qBittorrent creator task inventory')
        return rows

    def start(self, claim, announce):
        self.request('addTask', data={'sourcePath': claim['source_path'], 'pieceSize': claim['piece_size'],
            'format': 'v1', 'private': 'true', 'startSeeding': 'false',
            'optimizeAlignment': 'false', 'paddedFileSizeLimit': 0, 'ignoreDotfiles': 'false',
            'trackers': announce, 'comment': claim['marker'], 'source': claim['marker']})

    def result(self, claim):
        rows = [r for r in self.tasks() if r.get('comment') == claim['marker']]
        if len(rows) != 1:
            raise UploadBlocked('qBittorrent creator request unresolved; do not repeat creation')
        row = rows[0]
        if (row.get('sourcePath') != claim['source_path'] or row.get('pieceSize') != claim['piece_size']
                or row.get('private') is not True or row.get('format', 'v1') != 'v1'):
            raise UploadBlocked('qBittorrent creator task differs from the recorded request')
        if row.get('status') != 'Finished':
            raise UploadBlocked('qBittorrent creator failed' if row.get('status') == 'Failed'
                                else 'qBittorrent creator is still hashing; poll again later')
        task = row.get('taskID')
        if not isinstance(task, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', task):
            raise UploadBlocked('Invalid qBittorrent creator task identifier')
        return self.request('torrentFile?taskID=' + task, binary=True)


class RutorrentCreator:
    provider = 'rutorrent'

    def __init__(self, client):
        self.node = client.node
        rpc = urlsplit(self.node.url)
        explicit = getattr(self.node, 'rutorrent_url', None)
        prefix = rpc.path.split('/rutorrent', 1)[0] + '/rutorrent' if '/rutorrent' in rpc.path else '/rutorrent'
        self.base = (explicit or f'{rpc.scheme}://{rpc.netloc}{prefix}').rstrip('/')
        parsed = urlsplit(self.base)
        if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
                or parsed.query or parsed.fragment or (parsed.scheme, parsed.hostname, parsed.port)
                != (rpc.scheme, rpc.hostname, rpc.port)):
            raise UploadBlocked('ruTorrent creator must use the authenticated RPC origin')

    def request(self, route, *, data=None, binary=False, probe=False):
        try:
            response = self.node.s.request('GET' if data is None else 'POST', self.base + route,
                **({'data': data} if data is not None else {}), auth=self.node.auth,
                verify=True, allow_redirects=False, timeout=30, stream=binary)
        except Exception:
            raise UploadBlocked('ruTorrent creator request was not confirmed') from None
        if response is not None and probe and response.status_code in (404, 501):
            response.close()
            return None
        if response is None or response.status_code != 200:
            if response is not None: response.close()
            raise UploadBlocked('ruTorrent creator request was not confirmed')
        return response_data(response, binary=binary)

    def available(self):
        data = self.request('/plugins/create/action.php?cmd=rtget', probe=True)
        return isinstance(data, dict) and {'piece_size', 'start_seeding', 'private_torrent'} <= data.keys()

    def start(self, claim, announce):
        self.request('/plugins/create/action.php', data={'cmd': 'create', 'path_edit': claim['source_path'],
            'piece_size': claim['piece_size'] // 1024, 'trackers': announce, 'comment': claim['marker'],
            # Some Create versions enable hybrid by parameter presence, even
            # for "0". Omission selects v1 with both inner and torrenttools.
            'source': claim['marker'], 'start_seeding': 0, 'private': 1})

    def result(self, claim):
        tasks = self.request('/plugins/_task/action.php?cmd=list')
        if not isinstance(tasks, dict):
            raise UploadBlocked('Invalid ruTorrent creator task inventory')
        matches = []
        for task_id, row in tasks.items():
            if isinstance(row, dict) and isinstance(row.get('params'), dict):
                if row['params'].get('comment') == claim['marker']: matches.append((task_id, row))
        if len(matches) != 1:
            raise UploadBlocked('ruTorrent creator request unresolved; do not repeat creation')
        task_id, row = matches[0]
        params = row['params']
        if (params.get('path_edit', '').rstrip('/') != claim['source_path'].rstrip('/')
                or str(params.get('piece_size')) != str(claim['piece_size'] // 1024)
                or str(params.get('start_seeding', '')) != '0'
                or str(params.get('private', '')) != '1'
                or params.get('hybrid') not in (None, '', 0, '0', False)):
            raise UploadBlocked('ruTorrent creator task differs from the recorded request')
        if type(row.get('status')) is not int or row['status'] != 0:
            raise UploadBlocked('ruTorrent creator failed' if type(row.get('status')) is int and row['status'] > 0
                                else 'ruTorrent creator is still hashing; poll again later')
        if not isinstance(task_id, str) or not re.fullmatch(r'[0-9a-f]+\.[0-9]+', task_id):
            raise UploadBlocked('Invalid ruTorrent creator task identifier')
        return self.request('/plugins/create/action.php?cmd=getfile&no=' + task_id, binary=True)


class DelugeCreator:
    provider = 'deluge'

    def __init__(self, client):
        self.node = client.node

    def available(self):
        version = re.match(r'^(\d+)\.(\d+)(?:\.|$)', self.node.version)
        return (bool(version) and tuple(map(int, version.groups())) >= (2, 2)
                and {'core.create_torrent', 'core.get_path_size'} <= self.node.methods)

    def start(self, claim, announce):
        # Deluge 2.2 returns bytes but also writes a file: never overwrite or place
        # that file inside the directory being hashed. Older releases return None.
        output = posixpath.join(posixpath.dirname(claim['source_path']), claim['marker'] + '.torrent')
        if self.node.rpc('core.get_path_size', [output]) != -1:
            raise UploadBlocked('Deluge creator output already exists')
        result = self.node.rpc('core.create_torrent', [claim['source_path'], announce, claim['piece_size'],
            claim['marker'], output, None, True, 'Universal-Auto-Pilot', None, False, 'v1'], mutation=True)
        if (not isinstance(result, (list, tuple)) or len(result) != 2
                or not isinstance(result[1], (str, bytes)) or len(result[1]) > (READ_LIMIT * 4 // 3 + 4)):
            raise UploadBlocked('Deluge creator result unconfirmed; do not repeat creation')
        try:
            content = base64.b64decode(result[1], validate=True)
        except (ValueError, TypeError):
            raise UploadBlocked('Invalid Deluge creator result') from None
        if not content or len(content) > READ_LIMIT:
            raise UploadBlocked('Deluge creator result exceeds read limit')
        return content


def detect_creator(client, provider=None):
    """Probe read-only APIs; authentication/transport failures never mean absent."""
    from upload_client import QbitUploadClient, RtorrentUploadClient
    from deluge_client import DelugeUploadClient
    from onlyone_repack import OnlyOneMaker
    if provider == 'onlyone':
        OnlyOneMaker(client)
        return None
    if isinstance(client, QbitUploadClient) and provider in (None, 'qbittorrent'):
        creator = QbitCreator(client)
        if creator.tasks(probe=True) is not None: return creator
    elif isinstance(client, RtorrentUploadClient) and provider in (None, 'rutorrent'):
        creator = RutorrentCreator(client)
        if creator.available(): return creator
    elif isinstance(client, DelugeUploadClient) and provider in (None, 'deluge'):
        creator = DelugeCreator(client)
        if creator.available(): return creator
    if provider is None:
        try:
            OnlyOneMaker(client)
            return None
        except (UploadBlocked, AttributeError):
            pass
    raise UploadBlocked('No supported torrent creator detected (qBittorrent Creator API, ruTorrent Create, Deluge 2.2+, or OnlyOne required)')
