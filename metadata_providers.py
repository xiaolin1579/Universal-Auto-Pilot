"""Opt-in metadata lookup. Queries send only artist, title and year.

Web search supplies references only, never category facts. MusicBrainz is queried
in a bounded child process because its client retries without a public timeout API.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from upload_classifier import clean_text, public_source


class MetadataCache:
    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, key):
        return self.directory / (hashlib.sha256(key.encode()).hexdigest() + '.json')

    def get(self, key):
        path = self._path(key)
        try:
            if path.stat().st_size > 2 * 1024**2: return None
            data = json.loads(path.read_text())
            if data.get('version') == 1 and data.get('expires', 0) > time.time():
                value = data.get('value')
                if isinstance(value, dict) and isinstance(value.get('records'), list) and isinstance(value.get('references'), list):
                    return value
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return None

    def put(self, key, value, ttl=86400):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp = tempfile.mkstemp(dir=self.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump({'version':1,'expires':time.time()+ttl,'value':value}, handle, ensure_ascii=False)
            os.replace(temp, self._path(key))
        finally:
            if os.path.exists(temp): os.unlink(temp)


def _musicbrainz_worker(query):
    import socket
    import musicbrainzngs as mb
    socket.setdefaulttimeout(8)  # Isolated worker only, never the application process.
    mb.set_hostname('musicbrainz.org', use_https=True)
    mb.set_useragent('Universal-Auto-Pilot', '1.0', 'https://github.com/xiaolin1579/Universal-Auto-Pilot')
    mb.set_rate_limit(1.0, 1)
    fields = {'artist':query['artist'],'release':query['title']}
    if query.get('year'): fields['date'] = str(query['year'])
    results = mb.search_releases(limit=5, strict=True, **fields).get('release-list', [])
    records = []
    for result in results[:5]:
        release_id = result.get('id', '')
        if not re.fullmatch(r'[0-9a-f-]{36}', release_id): continue
        release = mb.get_release_by_id(release_id, includes=['artist-credits','release-groups','media']).get('release', {})
        artists = [a.get('artist', {}).get('name', a.get('name', ''))
                   for a in release.get('artist-credit', []) if isinstance(a, dict)]
        group = release.get('release-group', {})
        formats = sorted({m.get('format','') for m in release.get('medium-list', []) if isinstance(m,dict)})
        year = str(release.get('date', ''))[:4]
        records.append({'kind':'music','provider':'musicbrainz','url':'https://musicbrainz.org/release/'+release_id,
            'title':clean_text(release.get('title')), 'artist':clean_text(' & '.join(artists)),
            'year':int(year) if year.isdigit() else None, 'format':' / '.join(formats),
            'release_type':group.get('primary-type', group.get('type','')),
            'secondary_types':group.get('secondary-type-list', [])})
    return {'records':records,'references':[],'status':'ok'}


def _web_worker(query):
    from ddgs import DDGS
    # Never search titles of adult content or fetch an arbitrary result page.
    phrase = ' '.join(str(query.get(k) or '') for k in ('artist','title','year')).strip()
    results = DDGS(timeout=8).text(phrase, max_results=5)
    references = []
    for result in results:
        url = public_source(result.get('href'))
        if url:
            references.append({'title':clean_text(result.get('title'),200),'url':url})
    return {'records':[],'references':references,'status':'references_only'}


def lookup_metadata(identity, *, musicbrainz=False, web=False, cache_dir, timeout=25):
    records, references, diagnostics, providers = [], [], [], []
    if identity['kind'] == 'adult':
        return {'records':[], 'references':[], 'diagnostics':['external_lookup_unavailable_for_adult'],
                'providers':[]}
    if not identity.get('title') or '[REDACTED]' in json.dumps(identity) or '[URL]' in json.dumps(identity):
        return {'records':[], 'references':[], 'diagnostics':['query_identity_unavailable'], 'providers':[]}
    query = {k:identity.get(k) for k in ('artist','title','year')}
    cache = MetadataCache(cache_dir)
    requested = []
    if musicbrainz:
        if identity['kind'] == 'music' and identity.get('artist'):
            requested.append('musicbrainz')
        else:
            diagnostics.append('musicbrainz_requires_artist_and_album')
    if web: requested.append('web')
    for provider in requested:
        key = json.dumps({'version':1,'provider':provider,'query':query}, sort_keys=True, ensure_ascii=False)
        result = cache.get(key)
        cached = result is not None
        if result is None:
            try:
                child = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', provider],
                    input=json.dumps(query, ensure_ascii=False), text=True, capture_output=True,
                    timeout=max(1, min(timeout, 60)), check=True)
                if len(child.stdout) > 2*1024**2: raise ValueError('Oversized metadata response')
                result = json.loads(child.stdout)
                if (not isinstance(result,dict) or not isinstance(result.get('records'),list)
                        or not isinstance(result.get('references'),list)): raise ValueError('Invalid provider response')
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                result = {'records':[], 'references':[], 'status':'unavailable', 'error_type':type(exc).__name__}
            try:
                cache.put(key, result, ttl=300 if result.get('status') == 'unavailable' else 86400)
            except OSError:
                diagnostics.append('metadata_cache_unavailable')
        records.extend(result['records']); references.extend(result['references'])
        report = {'provider':provider,'status':result.get('status'),'cached':cached}
        if result.get('status') == 'unavailable':
            report['error_type'] = result.get('error_type'); diagnostics.append(provider+'_unavailable')
        providers.append(report)
    return {'records':records, 'references':references,'diagnostics':diagnostics,'providers':providers}


def load_catalog_records(path):
    path = Path(path)
    if path.stat().st_size > 2 * 1024**2: raise ValueError('Catalog metadata file exceeds size limit')
    data = json.loads(path.read_text(encoding='utf-8'))
    records = data.get('records') if isinstance(data,dict) else None
    if not isinstance(records,list) or len(records)>100: raise ValueError('Catalog metadata must contain up to 100 records')
    keys = {'kind','scope','provider','url','title','artist','year','release_type','secondary_types','format',
            'music_region','release_code','production_country','media_type','censorship'}
    result = []
    for record in records:
        if not isinstance(record,dict) or set(record)-keys: raise ValueError('Unsupported catalog record fields')
        if record.get('kind') not in ('music','video','adult') or not public_source(record.get('url')):
            raise ValueError('Catalog record needs a kind and public HTTPS evidence URL')
        scope = record.get('scope', 'release')
        if scope not in ('release', 'artist'):
            raise ValueError('Unsupported catalog evidence scope')
        if scope == 'artist' and (record.get('kind') != 'music' or not record.get('artist')
                or record.get('music_region') not in ('ไทยสากล','ไทยลูกทุ่ง','สากล','เอเชีย','เพลงบรรเลง(ไทย)','เพลงบรรเลง(ตปท)')
                or set(record) - {'kind','scope','provider','url','artist','music_region'}):
            raise ValueError('Artist evidence must declare only the artist and music region')
        if any(not isinstance(v,str) or len(v)>500 for k,v in record.items() if k not in ('year','secondary_types')):
            raise ValueError('Catalog metadata text values must be short strings')
        if 'year' in record and (type(record['year']) is not int or not 1900<=record['year']<=2100):
            raise ValueError('Catalog year is invalid')
        if 'secondary_types' in record and (not isinstance(record['secondary_types'],list)
                or len(record['secondary_types'])>10 or any(not isinstance(v,str) or len(v)>50 for v in record['secondary_types'])):
            raise ValueError('Catalog secondary types are invalid')
        result.append(record)
    return result


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--worker' or sys.argv[2] not in ('musicbrainz','web'):
        raise SystemExit(2)
    try:
        data = json.loads(sys.stdin.read(10000))
        output = _musicbrainz_worker(data) if sys.argv[2] == 'musicbrainz' else _web_worker(data)
        print(json.dumps(output, ensure_ascii=False))
    except Exception:
        # Provider exception text may contain URLs or credentials. Parent reports only the failure type.
        raise SystemExit(1)
