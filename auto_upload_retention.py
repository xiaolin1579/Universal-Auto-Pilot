"""Authenticated website evidence for retiring a published Auto Upload torrent.

Never infer other seeders from the total count or persist account IDs/IPs.
An unreadable, anonymous or stale peer list keeps the uploaded torrent held.
"""
import asyncio
from datetime import timedelta
import os
import re
from urllib.parse import urljoin, urlsplit, parse_qs

from bs4 import BeautifulSoup

from auto_upload import UploadBlocked
from torrent_metainfo import _extract_torrent_info_hash
from torrent_store import fresh, stamp, utcnow
from upload_sites import authenticated, listing_identity, trusted_url

TTL_SECONDS = 300
NUMBER = r'(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)'


class RetentionUnverified(UploadBlocked):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Auto Upload retention: ' + reason)


def listing_stats(site, html, current, expected):
    if site not in ('DEDBIT', 'BITSUSE'):
        raise RetentionUnverified('unsupported_site')
    if listing_identity(site, current) != {k: expected.get(k) for k in ('torrent_id', 'details_url')}:
        raise RetentionUnverified('page_unverified')
    if not isinstance(html, str) or len(html) > 4 * 1024**2:
        raise RetentionUnverified('page_unverified')
    soup = BeautifulSoup(html, 'html.parser')
    authenticated(soup, site, current)
    values = {}
    for label in ('Snatched', 'Peers'):
        fields = [dt for dt in soup.select('dt') if dt.get_text(' ', strip=True) == label]
        if len(fields) != 1 or fields[0].find_next_sibling('dd') is None:
            raise RetentionUnverified('page_unverified')
        values[label] = fields[0].find_next_sibling('dd')
    # The "last 20 snatched" navigation is not the completed counter.
    direct = ' '.join(values['Snatched'].find_all(string=True, recursive=False)).strip()
    match = re.fullmatch(r'(' + NUMBER + r')\s+time\(s\)', direct)
    if not match: raise RetentionUnverified('page_unverified')
    completed = int(match[1].replace(',', ''))
    peers = ' '.join(values['Peers'].get_text(' ', strip=True).split())
    match = re.fullmatch(r'(' + NUMBER + r') seed, (' + NUMBER + r') leech\s*\((' + NUMBER
                         + r') peers total\)(?:\s*\[See full list\])?', peers)
    if not match: raise RetentionUnverified('page_unverified')
    seeders, leechers, total = [int(v.replace(',', '')) for v in match.groups()]
    if seeders + leechers != total: raise RetentionUnverified('page_unverified')
    tid = expected['torrent_id']
    # Only follow the endpoint actually advertised by this listing. Bitsuse
    # currently hides the identities; its aggregate count cannot pass this guard.
    endpoint = None
    pattern = r'["\'](ajaxpeers\.php\?id=' + re.escape(tid) + r'&type=seed)["\']'
    for script in soup.find_all('script', src=False):
        found = re.search(pattern, script.get_text())
        if found:
            endpoint = urljoin(current, found[1])
            trusted_url(site, endpoint)
            break
    return {'completed': completed, 'seeders': seeders, 'peer_url': endpoint}


def other_seeders(fragment, site, current, own_uid, expected_total):
    if not isinstance(own_uid, str) or not re.fullmatch(r'[1-9][0-9]*', own_uid):
        raise RetentionUnverified('peer_identity_unverified')
    if not isinstance(fragment, bytes) or len(fragment) > 2 * 1024**2:
        raise RetentionUnverified('peer_identity_unverified')
    try:
        text = fragment.decode('utf-8')
    except UnicodeDecodeError:
        raise RetentionUnverified('peer_identity_unverified') from None
    lines = text.lstrip().splitlines()
    if not lines or lines[0].strip() != 'success':
        raise RetentionUnverified('peer_identity_unverified')
    soup = BeautifulSoup('\n'.join(lines[1:]), 'html.parser')
    recognized = []
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if not rows: continue
        headings = [c.get_text(' ', strip=True).casefold()
                    for c in rows[0].find_all(['th', 'td'], recursive=False)]
        if headings.count('user/ip') == 1 and headings.count('complete') == 1:
            recognized.append((rows, headings))
    if len(recognized) != 1: raise RetentionUnverified('peer_identity_unverified')
    rows, headings = recognized[0]
    if len(rows) - 1 != expected_total: raise RetentionUnverified('peer_identity_unverified')
    verified = set()
    for row in rows[1:]:
        cells = row.find_all(['th', 'td'], recursive=False)
        if len(cells) != len(headings): raise RetentionUnverified('peer_identity_unverified')
        complete = re.sub(r'\s+', '', cells[headings.index('complete')].get_text())
        if not re.fullmatch(r'100(?:\.0+)?%', complete):
            raise RetentionUnverified('peer_identity_unverified')
        identities = set()
        for anchor in cells[headings.index('user/ip')].select('a[href]'):
            url = urljoin(current, anchor['href']); parsed = urlsplit(url)
            if parsed.path != '/userdetails.php': continue
            trusted_url(site, url)
            ids = parse_qs(parsed.query).get('id', [])
            if len(ids) == 1 and re.fullmatch(r'[1-9][0-9]*', ids[0]): identities.add(ids[0])
        if len(identities) != 1: raise RetentionUnverified('peer_identity_unverified')
        uid = identities.pop()
        if uid != own_uid: verified.add(uid)
    return len(verified)


def observation(job, status='checking', reason='checking'):
    checked = utcnow()
    return {'version': 1, 'job_id': job['id'], 'target_hash': job['target_hash'],
            'site': job['target_site'], 'torrent_id': job['listing']['torrent_id'],
            'method': 'authenticated_listing_and_peers', 'status': status, 'reason': reason,
            'checked_at': stamp(checked), 'expires_at': stamp(checked + timedelta(seconds=TTL_SECONDS)),
            'completed': None, 'other_seeders': None, 'own_peers_excluded': False,
            'canonical_hash_verified': False}


async def inspect_upload(adapter, job):
    proof = observation(job)
    try:
        html, current = await adapter.open(job['listing']['details_url'])
        stats = listing_stats(adapter.site, html, current, job['listing'])
        proof['completed'] = stats['completed']
        if stats['seeders'] == 0:
            proof.update(other_seeders=0, own_peers_excluded=True)
        else:
            if not stats['peer_url']: raise RetentionUnverified('peer_identity_unverified')
            import nodriver as uc
            cookies = await adapter.tab.send(uc.cdp.network.get_cookies(urls=[adapter.origin + '/']))
            ids = {c.value for c in cookies if c.name == 'uid'}
            if len(ids) != 1: raise RetentionUnverified('peer_identity_unverified')
            fragment = await adapter._download_bytes(stats['peer_url'])
            proof['other_seeders'] = other_seeders(fragment, adapter.site, current, ids.pop(), stats['seeders'])
            proof['own_peers_excluded'] = True
        if proof['completed'] < 1:
            proof.update(status='held', reason='completed_required')
        elif proof['other_seeders'] < 1:
            proof.update(status='held', reason='other_seeders_required')
        else:
            canonical = await adapter.download_listing(job['listing']['details_url'])
            if _extract_torrent_info_hash(canonical) != job['target_hash']:
                raise RetentionUnverified('canonical_hash_mismatch')
            proof.update(canonical_hash_verified=True, status='ready', reason='ready')
    except RetentionUnverified as exc:
        proof.update(status='held', reason=exc.reason)
    except Exception:
        proof.update(status='failed', reason='fetch_failed')
    return proof


async def refresh_upload_retention(app, *, session=None):
    global_cfg = app.CFG.get('GLOBAL_CLEAN', {})
    stopping = lambda: getattr(app, 'stop_event', None) is not None and app.stop_event.is_set()
    if stopping(): return []
    if not any(n.get('enable') is True and app.NodeCleaner(None, n, global_cfg).enabled()
               for n in app.CFG.get('NODES', [])):
        return []
    data = await asyncio.to_thread(app._mapping_store().read)
    jobs = [j for j in data['upload_jobs'].values() if j.get('target_hash') and j.get('listing')
            and j.get('state') == 'seeding'
            and not ((j.get('retention_check') or {}).get('status') in ('ready', 'held', 'failed')
                     and fresh(j['retention_check']))]
    if not jobs: return []
    from upload_runner import UploadSession
    owns_session = session is None
    if owns_session: session = UploadSession(app, 'upload_retention_' + str(os.getpid()))
    result = []
    try:
        groups = {}
        for job in jobs:
            await app._store_call('record_upload_retention', job['id'], job, observation(job))
            groups.setdefault(job['target_site'], []).append(job)
        for site, batch in groups.items():
            if stopping(): break
            adapter = None
            if site in ('DEDBIT', 'BITSUSE'):
                try: adapter = await session.adapter(site)
                except Exception: pass
            for job in batch:
                if stopping(): break
                proof = await inspect_upload(adapter, job) if adapter else observation(job, 'failed', 'fetch_failed')
                await app._store_call('record_upload_retention', job['id'], job, proof)
                result.append(proof)
                print('[Cleaner Upload] site=' + site + ' job=' + job['id'][:12]
                      + ' status=' + proof['status'] + ' reason=' + proof['reason']
                      + ' other_seeders=' + str(proof['other_seeders'])
                      + ' completed=' + str(proof['completed']), flush=True)
    finally:
        if owns_session:
            try: await session.close()
            except Exception as exc:
                print('[Cleaner Upload] browser_cleanup=failed error_type=' + type(exc).__name__)
    return result
