"""Shared config editor for the Linux and Windows launchers.

Edits raw documents; normalized runtime views are validated but never persisted.
No command in this module contacts websites, torrent clients, or notification APIs.
"""
from contextlib import contextmanager
from copy import deepcopy
import argparse
import getpass
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit

from node_config import ConfigError, deluge_rpc_settings, normalize_config, uses_shared_nodes


class ConfigEditorError(ValueError):
    """Operator-readable errors which never include submitted values."""


REMOVE = object()
_SECRET = re.compile(r'pass|token|secret|cookie|authorization|access.?key|api.?key|webhook', re.I)
_RUNTIME_FIELDS = {'qb_user', 'qb_pass', 'rt_user', 'rt_pass', 'deluge_user', 'deluge_pass', 'http_basic_user',
                   'http_basic_pass', 'nginx', 'provider_sso', 'provider_user',
                   'provider_pass', 'provider_auth_origin', 'quota_gb', 'disk_type',
                   'storage_api', 'storage_mount', 'storage_safety_buffer_gb',
                   'allow_space_reclaim', 'clean_settings', '_storage_reader', 'storage_reader'}
_CLEAN_FIELDS = {'enable', 'policy', 'allow_space_reclaim', 'min_ratio',
                 'min_time_minutes', 'max_time_minutes'}
_LEGACY_CLEAN_FIELDS = {'enable', 'min_ratio', 'min_time', 'max_time', 'max_idle_hours'}
_SHARED_NODE_FIELDS = {'id', 'name', 'type', 'url', 'rutorrent_url', 'enable', 'storage_id', 'auth',
                       'cleaner', 'space_accounting', 'allow_sweeper_auto_resume', 'daemon_id', 'rpc'}
_LEGACY_NODE_FIELDS = {'name', 'type', 'url', 'rutorrent_url', 'enable', 'qb_user', 'qb_pass', 'rt_user',
                       'rt_pass', 'nginx', 'quota_gb', 'disk_type', 'clean_settings',
                       'allow_space_reclaim', 'provider_sso', 'provider_user', 'provider_pass',
                       'provider_auth_origin', 'storage_api', 'storage_mount',
                       'storage_safety_buffer_gb', 'storage_id', 'allow_sweeper_auto_resume',
                       'preallocated_downloads_accounted_in_free_space', 'deluge_user', 'deluge_pass',
                       'http_basic_user', 'http_basic_pass', 'daemon_id', 'rpc'}


def _object(value):
    if not isinstance(value, dict):
        raise ConfigEditorError('Expected a JSON object')
    return value


def _keys(value, allowed):
    _object(value)
    if set(value) - allowed:
        raise ConfigEditorError('Unsupported field; use the canonical config fields')


def _number(value):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ConfigEditorError('Numeric settings must be finite and non-negative')


def _bool(value):
    if type(value) is not bool:
        raise ConfigEditorError('Switches must be true or false')


def _ref(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigEditorError('References must be non-empty strings without surrounding whitespace')


def _url(value):
    try:
        parsed = urlsplit(value) if isinstance(value, str) else None
        valid = (parsed is not None and parsed.scheme in ('http', 'https') and parsed.hostname
                 and not parsed.username and not parsed.password and not parsed.fragment)
        if parsed is not None:
            parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ConfigEditorError('URL must use HTTP(S), without embedded credentials or fragment')


def _merge(base, changes):
    result = deepcopy(base)
    for key, value in _object(changes).items():
        if value is REMOVE:
            result.pop(key, None)
        elif isinstance(value, dict):
            result[key] = _merge(result.get(key, {}) if isinstance(result.get(key), dict) else {}, value)
        else:
            result[key] = deepcopy(value)
    return result


def _validate_clean(rule, shared=False):
    _object(rule)
    for key, value in rule.items():
        if key in ('enable', 'ENABLE', 'allow_space_reclaim'):
            _bool(value)
        elif key in ('min_ratio', 'min_time', 'max_time', 'max_idle_hours',
                     'min_time_minutes', 'max_time_minutes'):
            _number(value)
    low, high = ('min_time_minutes', 'max_time_minutes') if shared else ('min_time', 'max_time')
    if low in rule and high in rule and rule[low] > rule[high]:
        raise ConfigEditorError('Maximum time must be at least the minimum time')


def _validate(doc):
    _object(doc)
    nodes = doc.get('NODES', [])
    if not isinstance(nodes, list) or any(not isinstance(n, dict) for n in nodes):
        raise ConfigEditorError('NODES must be a list of objects')
    shared = uses_shared_nodes(doc)
    if shared:
        if 'GLOBAL_CLEAN' in doc or any(set(node) & _RUNTIME_FIELDS for node in nodes):
            raise ConfigEditorError('Runtime compatibility fields must not be saved in shared config')
        for key in ('CREDENTIALS', 'STORAGES'):
            for ref, item in _object(doc.get(key, {})).items():
                _ref(ref)
                _object(item)
                if key == 'CREDENTIALS':
                    _keys(item, {'username', 'password'})
                    if not isinstance(item.get('username'), str) or not isinstance(item.get('password'), str):
                        raise ConfigEditorError('Credentials require username and password strings')
                else:
                    _keys(item, {'disk_type', 'quota_gib', 'safety_buffer_gib', 'telemetry'})
                    if item.get('disk_type', 'HDD') not in ('HDD', 'SSD', 'NVME', 'HYBRID'):
                        raise ConfigEditorError('Unsupported disk type')
                    for field in ('quota_gib', 'safety_buffer_gib'):
                        if field in item:
                            _number(item[field])
                    telemetry = _object(item.get('telemetry', {}))
                    _keys(telemetry, {'type', 'via_node', 'mount'})
                    if telemetry.get('type', 'client') not in ('client', 'swizzin', 'onlyone'):
                        raise ConfigEditorError('Unsupported storage telemetry type')
                    if 'via_node' in telemetry:
                        _ref(telemetry['via_node'])
                        sources = [n for n in nodes if n.get('id') == telemetry['via_node']]
                        if len(sources) != 1 or sources[0].get('storage_id') != ref:
                            raise ConfigEditorError('Storage source must reference a node on the same storage')
                    if 'mount' in telemetry:
                        mount = telemetry['mount']
                        if (not isinstance(mount, str) or not mount.startswith('/') or '\x00' in mount
                                or '\\' in mount or any(p in ('.', '..') for p in mount.split('/'))):
                            raise ConfigEditorError('Mount must be a safe absolute path')
        cleaner = _object(doc.get('CLEANER', {}))
        _validate_clean(cleaner.get('defaults', {}), True)
        policies = _object(cleaner.get('policies', {}))
        for ref, rule in policies.items():
            _ref(ref)
            _validate_clean(rule, True)
        default_policy = cleaner.get('defaults', {}).get('policy')
        if default_policy is not None and default_policy not in policies:
            raise ConfigEditorError('Unknown default Cleaner policy')
    else:
        names = set()
        for n in nodes:
            name = n.get('name')
            _ref(name)
            if name.casefold() in names:
                raise ConfigEditorError('Duplicate node name')
            names.add(name.casefold())
            for flag in ('enable', 'nginx', 'provider_sso', 'allow_space_reclaim',
                         'allow_sweeper_auto_resume', 'preallocated_downloads_accounted_in_free_space'):
                if flag in n:
                    _bool(n[flag])
            for field in ('quota_gb', 'storage_safety_buffer_gb'):
                if field in n:
                    _number(n[field])
            if 'url' in n and not deluge_rpc_settings(n):
                _url(n['url'])
            if n.get('disk_type', 'HDD') not in ('HDD', 'SSD', 'NVME', 'HYBRID'):
                raise ConfigEditorError('Unsupported disk type')
            if 'clean_settings' in n:
                _validate_clean(n['clean_settings'])
        if 'GLOBAL_CLEAN' in doc:
            _validate_clean(doc['GLOBAL_CLEAN'])
    sites = doc.get('SITE', [])
    if not isinstance(sites, list) or any(not isinstance(s, dict) for s in sites):
        raise ConfigEditorError('SITE must be a list of objects')
    for site in sites:
        if 'enable' in site:
            _bool(site['enable'])
        zones = site.get('target_urls', [])
        if not isinstance(zones, list):
            raise ConfigEditorError('target_urls must be a list of objects')
        for zone in zones:
            _object(zone)
            if 'enable' in zone:
                _bool(zone['enable'])
    for key in ('TELEGRAM_CONFIG', 'DISCORD_CONFIG', 'LINE_CONFIG'):
        if key in doc:
            for field, value in _object(doc[key]).items():
                if field in ('enable', 'notify_enable', 'remote_enable'):
                    _bool(value)
    if 'SETTING' in doc:
        for field, value in _object(doc['SETTING']).items():
            if field in ('FREELOAD_ENABLE', 'Xvfb_enable'):
                _bool(value)
            elif field in ('MIN_WAIT_MINUTES', 'MAX_WAIT_MINUTES', 'MIN_SIZE_GB', 'MAX_SIZE_GB',
                           'MIN_FREE_PERCENT', 'MAX_NEW_PER_ZONE', 'MAX_ACTIVE_DOWNLOADS'):
                _number(value)
    normalize_config(doc)
    return doc


def load_document(path):
    try:
        with Path(path).open(encoding='utf-8') as handle:
            doc = json.load(handle)
    except (ValueError, OSError) as exc:
        raise ConfigEditorError('Cannot read config JSON; check the file and its JSON syntax') from exc
    return _validate(doc)


@contextmanager
def _lock(path):
    fd = os.open(str(path) + '.editor.lock', os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'r+b') as handle:
        if os.name == 'nt':
            import msvcrt
            if not handle.read(1):
                handle.write(b'\0'); handle.flush()
            def acquire():
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        deadline = time.monotonic() + 5
        while True:
            try:
                acquire(); break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise ConfigEditorError('Another config editor is saving; try again') from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            release()


def _atomic(path, data):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
        if os.name != 'nt':
            os.chmod(path, 0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_document(path, doc, expected=None):
    """Validate and save raw config; keep a private backup of the previous version."""
    _validate(doc)
    path = Path(path).resolve()
    if not path.parent.is_dir():
        raise ConfigEditorError('Config directory does not exist')
    payload = (json.dumps(doc, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    with _lock(path):
        previous = path.read_bytes() if path.exists() else None
        if expected is not None:
            if previous is None:
                if expected != {}:
                    raise ConfigEditorError('Config changed concurrently; reload before saving')
            else:
                try:
                    current = json.loads(previous)
                except ValueError as exc:
                    raise ConfigEditorError('Config changed concurrently; reload before saving') from exc
                if current != expected:
                    raise ConfigEditorError('Config changed concurrently; reload before saving')
        if previous is not None:
            try:
                if json.loads(previous) == doc:
                    return
            except ValueError:
                pass
            _atomic(Path(str(path) + '.editor.bak'), previous)
        _atomic(path, payload)


def _finish(raw):
    return _validate(raw)


def _shared(raw):
    if not uses_shared_nodes(raw):
        raise ConfigEditorError('This operation requires shared-node config')


def _node(raw, ident):
    nodes = raw.get('NODES', [])
    matches = [n for n in nodes if n.get('id') == ident] if uses_shared_nodes(raw) else []
    if not matches:
        matches = [n for n in nodes if n.get('name') == ident]
    if len(matches) != 1:
        raise ConfigEditorError('Unknown or ambiguous node')
    return matches[0]


def update_node(raw, node_id_or_name, changes):
    shared = uses_shared_nodes(raw)
    _keys(changes, _SHARED_NODE_FIELDS if shared else _LEGACY_NODE_FIELDS)
    new = deepcopy(raw); node = _node(new, node_id_or_name)
    for key in ('id', 'name', 'type'):
        if key in changes and changes[key] != node.get(key):
            raise ConfigEditorError('Changing node identity requires mapping migration; keep its id, name and type')
    merged = _merge(node, changes)
    node.clear(); node.update(merged)
    return _finish(new)


def add_node(raw, node):
    shared = uses_shared_nodes(raw)
    _keys(node, _SHARED_NODE_FIELDS if shared else _LEGACY_NODE_FIELDS)
    if node.get('type') not in ('qbit', 'rtorrent', 'deluge'):
        raise ConfigEditorError('New nodes must use qbit, rtorrent or deluge')
    new = deepcopy(raw); candidate = deepcopy(node)
    candidate.setdefault('enable', False)
    if not shared:
        if not deluge_rpc_settings(candidate):
            _url(candidate.get('url'))
        prefix = {'rtorrent': 'rt', 'deluge': 'deluge', 'qbit': 'qb'}[candidate['type']]
        if any(not isinstance(candidate.get(prefix + suffix), str) for suffix in ('_user', '_pass')):
            raise ConfigEditorError('New legacy nodes require username and password strings')
        candidate.setdefault('clean_settings', {'enable': False})
        candidate.setdefault('allow_space_reclaim', False)
    new.setdefault('NODES', []).append(candidate)
    return _finish(new)


def references_users(raw, kind, ref):
    users = set()
    nodes = raw.get('NODES', [])
    for n in nodes:
        name = n.get('name', n.get('id', '?'))
        if kind == 'credential' and any(isinstance(a, dict) and a.get('credential_ref') == ref
                                        for a in n.get('auth', {}).values()):
            users.add(name)
        elif kind == 'storage' and n.get('storage_id') == ref:
            users.add(name)
        elif kind == 'policy' and n.get('cleaner', {}).get('policy',
                raw.get('CLEANER', {}).get('defaults', {}).get('policy')) == ref:
            users.add(name)
        elif kind == 'node':
            telemetry = raw.get('STORAGES', {}).get(n.get('storage_id'), {}).get('telemetry', {})
            if telemetry.get('via_node') == ref:
                users.add(name)
    if kind == 'credential':
        ids = {n.get('id') for n in nodes if n.get('name') in users}
        for storage in raw.get('STORAGES', {}).values():
            if storage.get('telemetry', {}).get('via_node') in ids:
                for storage_id, item in raw.get('STORAGES', {}).items():
                    if item is storage:
                        users.update(n.get('name', n.get('id', '?')) for n in nodes if n.get('storage_id') == storage_id)
    return sorted(users)


def delete_node(raw, node_id_or_name):
    new = deepcopy(raw); node = _node(new, node_id_or_name)
    if any(s.get('telemetry', {}).get('via_node') == node.get('id')
           for s in new.get('STORAGES', {}).values()):
        raise ConfigEditorError('Node is a storage telemetry source; clear or change via_node first')
    new['NODES'].remove(node)
    return _finish(new)


def update_credentials(raw, ref, changes):
    _shared(raw); _ref(ref); _keys(changes, {'username', 'password'})
    new = deepcopy(raw)
    new.setdefault('CREDENTIALS', {})[ref] = _merge(new.get('CREDENTIALS', {}).get(ref, {}), changes)
    return _finish(new)


def delete_credentials(raw, ref):
    _shared(raw)
    if ref not in raw.get('CREDENTIALS', {}):
        raise ConfigEditorError('Unknown credential reference')
    if references_users(raw, 'credential', ref):
        raise ConfigEditorError('Credential is referenced by nodes; change their auth first')
    new = deepcopy(raw); del new['CREDENTIALS'][ref]
    return _finish(new)


def update_storage(raw, ref, changes):
    _shared(raw); _ref(ref)
    _keys(changes, {'disk_type', 'quota_gib', 'safety_buffer_gib', 'telemetry'})
    new = deepcopy(raw)
    new.setdefault('STORAGES', {})[ref] = _merge(new.get('STORAGES', {}).get(ref, {}), changes)
    return _finish(new)


def delete_storage(raw, ref):
    _shared(raw)
    if ref not in raw.get('STORAGES', {}):
        raise ConfigEditorError('Unknown storage reference')
    if references_users(raw, 'storage', ref):
        raise ConfigEditorError('Storage is referenced by nodes; change their storage_id first')
    new = deepcopy(raw); del new['STORAGES'][ref]
    return _finish(new)


def update_cleaner(raw, changes, node_id=None):
    new = deepcopy(raw)
    if uses_shared_nodes(raw):
        if node_id is not None:
            _keys(changes, _CLEAN_FIELDS)
            node = _node(new, node_id)
            node['cleaner'] = _merge(node.get('cleaner', {}), changes)
        else:
            _keys(changes, {'enable', 'defaults', 'policies', 'site_obligations'})
            new['CLEANER'] = _merge(new.get('CLEANER', {}), changes)
    elif node_id is not None:
        _keys(changes, _LEGACY_CLEAN_FIELDS | {'allow_space_reclaim'})
        node = _node(new, node_id)
        settings = {k: v for k, v in changes.items() if k != 'allow_space_reclaim'}
        node['clean_settings'] = _merge(node.get('clean_settings', {}), settings)
        if 'allow_space_reclaim' in changes:
            if changes['allow_space_reclaim'] is REMOVE:
                node.pop('allow_space_reclaim', None)
            else:
                node['allow_space_reclaim'] = changes['allow_space_reclaim']
    else:
        _keys(changes, _LEGACY_CLEAN_FIELDS)
        new['GLOBAL_CLEAN'] = _merge(new.get('GLOBAL_CLEAN', {}), changes)
    return _finish(new)


def update_policy(raw, ref, changes):
    _shared(raw); _ref(ref)
    _keys(changes, {'min_ratio', 'min_time_minutes', 'max_time_minutes'})
    new = deepcopy(raw)
    policies = new.setdefault('CLEANER', {}).setdefault('policies', {})
    policies[ref] = _merge(policies.get(ref, {}), changes)
    return _finish(new)


def delete_policy(raw, ref):
    _shared(raw)
    if ref not in raw.get('CLEANER', {}).get('policies', {}):
        raise ConfigEditorError('Unknown Cleaner policy')
    if (references_users(raw, 'policy', ref)
            or raw.get('CLEANER', {}).get('defaults', {}).get('policy') == ref):
        raise ConfigEditorError('Cleaner policy is referenced; change its users first')
    new = deepcopy(raw); del new['CLEANER']['policies'][ref]
    return _finish(new)


def redact(value, key=''):
    if _SECRET.search(key):
        return '***' if value else value
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and value.startswith(('http://', 'https://')):
        u = urlsplit(value)
        return urlunsplit((u.scheme, u.netloc.rsplit('@', 1)[-1], u.path,
                           '[hidden]' if u.query else '', '[hidden]' if u.fragment else ''))
    return value


def _answer(prompt, current=None, kind=str, secret=False):
    default = '' if secret or current is None else ' [' + str(redact(current)) + ']'
    value = getpass.getpass(prompt + default + ': ') if secret else input(prompt + default + ': ')
    if value == '':
        return REMOVE
    if value == '-' and not secret:
        return None
    try:
        if kind is bool:
            if value.lower() in ('true', 'y', 'yes', '1'):
                return True
            if value.lower() in ('false', 'n', 'no', '0'):
                return False
            raise ValueError()
        parsed = kind(value)
        if kind in (int, float):
            _number(parsed)
        return parsed
    except (TypeError, ValueError):
        raise ConfigEditorError('Invalid input; switches use true/false and numbers must be non-negative')


def _form(current, fields):
    changes = {}
    for field, kind, secret in fields:
        value = _answer(field, current.get(field), kind, secret)
        if value is not REMOVE:
            changes[field] = REMOVE if value is None else value
    return changes


def _pick(items, label):
    keys = list(items)
    if not keys:
        raise ConfigEditorError('No entries configured yet')
    for i, key in enumerate(keys, 1):
        print(str(i) + ') ' + str(key))
    value = input(label + ' (0=back): ').strip()
    if value in ('0', ''):
        return None
    try:
        return keys[int(value) - 1] if 1 <= int(value) <= len(keys) else None
    except ValueError:
        raise ConfigEditorError('Choose an entry number')


class Editor:
    def __init__(self, path):
        self.path = Path(path)
        self.doc = load_document(path) if self.path.exists() else {
            'SITE': [], 'SETTING': {}, 'NODES': [], 'GLOBAL_CLEAN': {'enable': False}}
        self.exists = self.path.exists()

    def commit(self, candidate, affected=None):
        _validate(candidate)
        if candidate == self.doc:
            print('ไม่มีการเปลี่ยนแปลง'); return
        print('รายการเปลี่ยนแปลง (ซ่อนรหัสผ่านและ token):')
        for key in sorted(set(candidate) | set(self.doc)):
            if candidate.get(key) != self.doc.get(key):
                print(key + ': ' + json.dumps(redact(candidate.get(key)), ensure_ascii=False, indent=2))
        if affected:
            print('มีผลกับ: ' + ', '.join(affected))
        if input('บันทึกการเปลี่ยนแปลงนี้? [y/N]: ').strip().lower() not in ('y', 'yes'):
            print('ยกเลิก'); return
        save_document(self.path, candidate, expected=self.doc if self.exists else {})
        self.doc, self.exists = candidate, True
        print('บันทึกแล้ว พร้อมสำรองไฟล์เดิมเป็น .editor.bak')

    def sites(self):
        sites = self.doc.get('SITE', [])
        choices = {str(i + 1) + ': ' + s.get('name', '?'): i for i, s in enumerate(sites)}
        selected = _pick({**choices, 'เพิ่มเว็บไซต์': None}, 'เลือกเว็บไซต์')
        if selected is None: return
        new = deepcopy(self.doc)
        if selected == 'เพิ่มเว็บไซต์':
            name = input('ชื่อเว็บไซต์: ').strip()
            if not name or any(s.get('name', '').casefold() == name.casefold() for s in sites):
                raise ConfigEditorError('Website name must be unique')
            url = input('base_url: ').strip()
            site = {'name': name, 'base_url': url, 'enable': False, 'username': '', 'password': '', 'target_urls': []}
            new.setdefault('SITE', []).append(site)
        else:
            site = new['SITE'][choices[selected]]
        action = input('1) บัญชี/เปิดใช้  2) โซน  0) กลับ: ').strip()
        if action == '1':
            merged = _merge(site, _form(site, [('enable', bool, False), ('base_url', str, False),
                                              ('username', str, False), ('password', str, True)]))
            site.clear(); site.update(merged)
        elif action == '2':
            zones = site.setdefault('target_urls', [])
            options = {str(i + 1) + ': ' + z.get('name', '?'): i for i, z in enumerate(zones)}
            choice = _pick({**options, 'เพิ่มโซน': None}, 'เลือกโซน')
            if choice is None: return
            if choice == 'เพิ่มโซน':
                zone = {'name': input('ชื่อโซน: ').strip(), 'url': input('URL: ').strip(), 'enable': False}
                zones.append(zone)
            else:
                zone = zones[options[choice]]
            merged = _merge(zone, _form(zone, [('name', str, False), ('url', str, False), ('enable', bool, False)]))
            zone.clear(); zone.update(merged)
        else: return
        self.commit(new)

    def notifications(self):
        key = _pick({'TELEGRAM_CONFIG': None, 'DISCORD_CONFIG': None, 'LINE_CONFIG': None}, 'เลือกช่องทาง')
        if key is None: return
        fields = {
            'TELEGRAM_CONFIG': [('notify_enable', bool, False), ('remote_enable', bool, False),
                                ('main_bot_token', str, True), ('remote_bot_token', str, True), ('chat_id', str, False)],
            'DISCORD_CONFIG': [('notify_enable', bool, False), ('remote_enable', bool, False),
                               ('remote_bot_token', str, True), ('admin_id', str, False), ('webhook_url', str, True)],
            'LINE_CONFIG': [('enable', bool, False), ('access_token', str, True), ('user_id', str, False)],
        }
        new = deepcopy(self.doc); current = new.setdefault(key, {})
        new[key] = _merge(current, _form(current, fields[key]))
        self.commit(new)

    def filters(self):
        fields = [(k, int, False) for k in ('MIN_WAIT_MINUTES', 'MAX_WAIT_MINUTES', 'MAX_NEW_PER_ZONE', 'MAX_ACTIVE_DOWNLOADS')]
        fields += [(k, float, False) for k in ('MIN_SIZE_GB', 'MAX_SIZE_GB', 'MIN_FREE_PERCENT')]
        fields += [('FREELOAD_ENABLE', bool, False), ('Xvfb_enable', bool, False)]
        new = deepcopy(self.doc)
        new['SETTING'] = _merge(new.get('SETTING', {}), _form(new.get('SETTING', {}), fields))
        self.commit(new)

    def cleaner(self):
        if not uses_shared_nodes(self.doc):
            fields = [('enable', bool, False), ('min_ratio', float, False), ('min_time', float, False), ('max_time', float, False)]
            self.commit(update_cleaner(self.doc, _form(self.doc.get('GLOBAL_CLEAN', {}), fields)))
            return
        action = input('1) สวิตช์กลาง  2) ค่าเริ่มต้น node  3) นโยบาย  4) Cleaner ของ node  5) ภาระรายเว็บ  0) กลับ: ').strip()
        cleaner = self.doc.get('CLEANER', {})
        if action == '1':
            self.commit(update_cleaner(self.doc, _form(cleaner, [('enable', bool, False)])))
        elif action == '2':
            changes = _form(cleaner.get('defaults', {}), [('enable', bool, False), ('allow_space_reclaim', bool, False), ('policy', str, False)])
            self.commit(update_cleaner(self.doc, {'defaults': changes}), [n.get('name', '?') for n in self.doc.get('NODES', [])])
        elif action == '3':
            self.resources('policy')
        elif action == '4':
            selected = self.pick_node()
            if selected:
                self.edit_node_cleaner(selected)
        elif action == '5':
            print('verified_obligations = ตรวจ H&R/Quest; cleaner_policy = ใช้เกณฑ์ ratio/เวลาของ node')
            changes = _form(cleaner.get('site_obligations', {}), [('DEDBIT', str, False), ('BITSUSE', str, False)])
            self.commit(update_cleaner(self.doc, {'site_obligations': changes}))

    def pick_node(self):
        return _pick({n.get('id', n.get('name')): None for n in self.doc.get('NODES', [])}, 'เลือก node')

    def edit_node_cleaner(self, ident):
        node = _node(self.doc, ident)
        shared = uses_shared_nodes(self.doc)
        current = node.get('cleaner' if shared else 'clean_settings', {})
        fields = [('enable', bool, False), ('allow_space_reclaim', bool, False)]
        fields += ([('policy', str, False), ('min_ratio', float, False), ('min_time_minutes', float, False), ('max_time_minutes', float, False)]
                   if shared else [('min_ratio', float, False), ('min_time', float, False), ('max_time', float, False)])
        self.commit(update_cleaner(self.doc, _form(current, fields), ident), [node.get('name', ident)])

    def nodes(self):
        for n in self.doc.get('NODES', []):
            print(json.dumps(redact(n), ensure_ascii=False, indent=2))
        action = input('1) แก้ไข  2) เพิ่ม  3) ลบ config node  4) Cleaner ของ node  0) กลับ: ').strip()
        if action not in ('1', '2', '3', '4'): return
        if action == '2':
            self.add_node(); return
        ident = self.pick_node()
        if not ident: return
        if action == '3':
            print('ลบเฉพาะ config; ไม่ลบ torrent หรือไฟล์บน seedbox')
            self.commit(delete_node(self.doc, ident)); return
        if action == '4':
            self.edit_node_cleaner(ident); return
        node = _node(self.doc, ident)
        changes = _form(node, [('url', str, False), ('enable', bool, False), ('allow_sweeper_auto_resume', bool, False)])
        if node.get('type') == 'rtorrent':
            changes.update(_form(node, [('rutorrent_url', str, False)]))
        if node.get('type') == 'deluge':
            if urlsplit(changes.get('url', node['url'])).scheme == 'deluge+ssl':
                changes['daemon_id'] = REMOVE
                changes['rpc'] = self.rpc_form(node.get('rpc', {}))
            else:
                changes['rpc'] = REMOVE
                changes.update(_form(node, [('daemon_id', str, False)]))
        if uses_shared_nodes(self.doc):
            changes.update(_form(node, [('storage_id', str, False)]))
            if input('แก้ auth references? [y/N]: ').strip().lower() == 'y':
                changes['auth'] = self.auth_form(node.get('type'), node.get('auth', {}))
            if input('แก้ space_accounting? [y/N]: ').strip().lower() == 'y':
                changes['space_accounting'] = _form(node.get('space_accounting', {}), [('safety_buffer_gib', float, False), ('preallocated_in_free_space', bool, False)])
        else:
            prefix = {'rtorrent': 'rt', 'deluge': 'deluge'}.get(node.get('type'), 'qb')
            changes.update(_form(node, [(prefix + '_user', str, False), (prefix + '_pass', str, True),
                                       ('quota_gb', float, False), ('disk_type', str, False), ('nginx', bool, False),
                                       ('storage_api', str, False), ('storage_mount', str, False),
                                       ('storage_safety_buffer_gb', float, False), ('allow_space_reclaim', bool, False)]))
        self.commit(update_node(self.doc, ident, changes))

    def auth_form(self, kind, current):
        print('บัญชีที่มี: ' + ', '.join(self.doc.get('CREDENTIALS', {})))
        patch = {}
        if kind == 'qbit':
            print('session.type ใช้ qbit หรือ onlyone_sso; OnlyOne SSO ไม่ใช้ HTTP Basic')
            session = current.get('session') or {'type': 'qbit'}
            fields = [('type', str, False), ('credential_ref', str, False), ('origin', str, False)]
            patch['session'] = _form(session, fields)
            if not current.get('session'):
                patch['session'].setdefault('type', 'qbit')
        elif kind == 'deluge':
            print('session.type: deluge_web = Web UI; deluge_rpc = username/password จากไฟล์ auth ของ daemon')
            session = current.get('session') or {'type': 'deluge_web'}
            patch['session'] = _form(session, [('type', str, False), ('credential_ref', str, False)])
            if not current.get('session'):
                patch['session'].setdefault('type', 'deluge_web')
        basic = current.get('http_basic') or {}
        value = _answer('HTTP Basic credential_ref (-=ไม่ใช้)', basic.get('credential_ref'))
        if value is None:
            patch['http_basic'] = REMOVE
        elif value is not REMOVE:
            patch['http_basic'] = {'credential_ref': value}
        return patch

    def rpc_form(self, current):
        print('RPC protocol: 2 สำหรับ Deluge 2.x, 1.3 สำหรับ 1.3.15; TLS ใช้ CA หรือ SHA-256 certificate pin อย่างใดอย่างหนึ่ง')
        return _form(current, [('protocol', str, False), ('tls_ca_file', str, False),
                               ('tls_cert_sha256', str, False)])

    def add_node(self):
        shared = uses_shared_nodes(self.doc)
        name = input('ชื่อ node (ต้องคงชื่อไว้หลังเริ่มใช้งาน): ').strip()
        kind = input('type [qbit/rtorrent/deluge]: ').strip()
        node = {'name': name, 'type': kind, 'url': input('URL: ').strip(), 'enable': False}
        if kind == 'deluge':
            if urlsplit(node['url']).scheme == 'deluge+ssl':
                node['rpc'] = _merge({}, self.rpc_form({}))
            else:
                daemon = input('daemon_id (ว่างได้เมื่อมี daemon เดียว): ').strip()
                if daemon: node['daemon_id'] = daemon
        if shared:
            node['id'] = input('id: ').strip()
            node['storage_id'] = input('storage_id: ').strip()
            initial = ({'session': {'type': 'deluge_rpc'}}
                       if kind == 'deluge' and urlsplit(node['url']).scheme == 'deluge+ssl' else {})
            node['auth'] = _merge(initial, self.auth_form(kind, initial))
        else:
            prefix = {'rtorrent': 'rt', 'deluge': 'deluge'}.get(kind, 'qb')
            node[prefix + '_user'] = input('Username: ')
            node[prefix + '_pass'] = getpass.getpass('Password: ')
            node['quota_gb'] = 0
            node['nginx'] = kind == 'rtorrent'
        self.commit(add_node(self.doc, node))

    def resources(self, kind):
        _shared(self.doc)
        section = {'credential': 'CREDENTIALS', 'storage': 'STORAGES', 'policy': 'CLEANER'}[kind]
        entries = self.doc.get(section, {}).get('policies', {}) if kind == 'policy' else self.doc.get(section, {})
        for ref, value in entries.items():
            print(ref + ': ' + json.dumps(redact(value), ensure_ascii=False))
        action = input('1) เพิ่ม/แก้ไข  2) ลบ  0) กลับ: ').strip()
        if action not in ('1', '2'): return
        ref = input('ชื่อ reference: ').strip()
        users = references_users(self.doc, kind, ref)
        print('ใช้งานโดย: ' + (', '.join(users) if users else '(ยังไม่มี node ใช้)'))
        if action == '2':
            method = {'credential': delete_credentials, 'storage': delete_storage, 'policy': delete_policy}[kind]
            self.commit(method(self.doc, ref), users); return
        current = entries.get(ref, {})
        if kind == 'credential':
            fields = [('username', str, False), ('password', str, True)]
            method = update_credentials
        elif kind == 'policy':
            fields = [('min_ratio', float, False), ('min_time_minutes', float, False), ('max_time_minutes', float, False)]
            method = update_policy
        else:
            fields = [('disk_type', str, False), ('quota_gib', float, False), ('safety_buffer_gib', float, False)]
            method = update_storage
        changes = _form(current, fields)
        if kind == 'storage':
            changes['telemetry'] = _form(current.get('telemetry', {}), [('type', str, False), ('via_node', str, False), ('mount', str, False)])
        self.commit(method(self.doc, ref, changes), users)

    def run(self):
        while True:
            print('\nUniversal Config Manager (' + ('shared references' if uses_shared_nodes(self.doc) else 'legacy') + ')')
            print('Enter=คงค่าเดิม, -=ลบค่า optional; รหัสผ่านใช้ช่องซ่อนและ Enter=คงเดิม')
            print('1) เว็บไซต์/โซน  2) แจ้งเตือน  3) ตัวกรอง  4) Cleaner  5) Nodes')
            print('6) Credentials  7) Storages  8) ตรวจสอบ config  0) ออก')
            action = input('เลือก: ').strip()
            if action in ('0', 'q', 'exit'): return
            try:
                handlers = {'1': self.sites, '2': self.notifications, '3': self.filters,
                            '4': self.cleaner, '5': self.nodes,
                            '6': lambda: self.resources('credential'), '7': lambda: self.resources('storage')}
                if action == '8':
                    _validate(self.doc); print('Config ถูกต้อง')
                elif action in handlers:
                    handlers[action]()
            except (ConfigEditorError, ConfigError) as exc:
                print('แก้ไขไม่สำเร็จ: ' + str(exc))
            except (OSError, ValueError, TypeError) as exc:
                print('แก้ไขไม่สำเร็จ: ' + type(exc).__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Manage legacy/shared node config without contacting services')
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.json')))
    parser.add_argument('--validate', action='store_true', help='Validate without changing the file')
    args = parser.parse_args(argv)
    try:
        if args.validate:
            doc = load_document(args.config)
            print('Configuration OK: ' + str(len(doc.get('NODES', []))) + ' nodes')
        else:
            Editor(args.config).run()
        return 0
    except (EOFError, KeyboardInterrupt):
        print('\nออกจากตัวแก้ไขโดยไม่บันทึกการแก้ไขที่ยังไม่ยืนยัน')
        return 0
    except (ConfigEditorError, ConfigError) as exc:
        print('Configuration error: ' + str(exc)); return 1
    except (OSError, ValueError, TypeError) as exc:
        print('Configuration error: ' + type(exc).__name__); return 1


if __name__ == '__main__':
    raise SystemExit(main())
