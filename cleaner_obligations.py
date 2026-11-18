"""Read-only, complete obligation snapshots before Cleaner considers removal.

Missing endpoints, login redirects, pagination failures and unresolved hashes
retain protection. This collector never downloads payloads or changes tasks.
"""
import asyncio
import os
from urllib.parse import urljoin, urlsplit, parse_qs

from bs4 import BeautifulSoup

from auto_upload import UploadBlocked
from torrent_metainfo import _extract_torrent_info_hash
from torrent_store import KINDS, fresh
from upload_sites import authenticated, listing_identity, trusted_url, SITES

PATHS = {'hr': '/myhr.php', 'quest_seed': '/mybonus.php'}


def parse_snapshot(site, kind, html, current):
    if site != 'BEARBIT' or kind not in PATHS:
        raise UploadBlocked('No verified obligation page adapter for this site')
    trusted_url(site, current)
    if urlsplit(current).path != PATHS[kind]:
        raise UploadBlocked('Obligation page identity changed')
    soup = BeautifulSoup(html, 'html.parser')
    authenticated(soup, site, current)
    if any(text in soup.get_text(' ', strip=True) for text in ('ระบบกำลัง อัพเดทข้อมูล', 'กรุณารอสักครู่')):
        raise UploadBlocked('Obligation page is not ready')
    ids = set()
    if kind == 'hr':
        containers = soup.select('table.t')
        empty = soup.select_one('td.empty, div.vipbox') is not None
        rows = [row for table in containers for row in table.select('tr')
                if not row.find('th') and not row.select_one('td.empty')]
        if not containers and not empty:
            raise UploadBlocked('H&R page structure is unverified')
    else:
        containers = soup.select('ul.bbx-qlist')
        if not containers:
            raise UploadBlocked('Seed Quest page structure is unverified')
        rows = [row for container in containers for row in container.select('li')]
        empty = not rows
    for row in rows:
        links = [a for a in row.select('a[href]') if urlsplit(urljoin(current, a['href'])).path == '/details.php']
        if not links:
            if kind == 'quest_seed':
                raise UploadBlocked('Seed Quest listing could not be identified')
            continue
        row_ids = {listing_identity(site, urljoin(current, a['href']))['torrent_id'] for a in links}
        if len(row_ids) != 1:
            raise UploadBlocked('Obligation listing identity is ambiguous')
        chip = row.select_one('span.bbx-chip') if kind == 'quest_seed' else None
        if not (chip and 'ครบแล้ว' in chip.get_text(' ', strip=True)):
            ids.update(row_ids)
    if kind == 'hr' and not ids and not empty:
        raise UploadBlocked('H&R page has no verified rows or empty marker')
    pages = set()
    for a in soup.select('a[href]'):
        url = urljoin(current, a['href']); parsed = urlsplit(url)
        if parsed.path != PATHS[kind]: continue
        query = parse_qs(parsed.query)
        if any(k in query for k in ('page', 'p', 'offset')):
            trusted_url(site, url)
            if not set(query) <= {'page', 'p', 'offset'} or any(len(v) != 1 or not v[0].isdigit() for v in query.values()):
                raise UploadBlocked('Obligation pagination is unverified')
            pages.add(url)
    return ids, pages


async def sync_site(app, adapter, max_pages=20, max_resolve=30):
    site = adapter.site
    result = {}
    def stopping():
        stop = getattr(app, 'stop_event', None)
        return stop is not None and stop.is_set()
    for kind, path in PATHS.items():
        if stopping(): return result
        await app._store_call('record_failure', site, kind)
        try:
            if site != 'BEARBIT':
                raise UploadBlocked('No verified obligation page adapter for this site')
            pending = [adapter.origin + path]; visited = set(); required = set()
            while pending:
                if stopping(): raise UploadBlocked('Obligation read interrupted by shutdown')
                if len(visited) >= max_pages:
                    raise UploadBlocked('Obligation pagination exceeds the read limit')
                url = pending.pop(0)
                if url in visited: continue
                html, current = await adapter.open(url)
                ids, more = parse_snapshot(site, kind, html, current)
                visited.add(url); required.update(ids)
                pending.extend(sorted(more - visited - set(pending)))
            await app._store_call('record_required_snapshot', site, kind, required)
            data = await asyncio.to_thread(app._mapping_store().read)
            unresolved = [tid for tid in sorted(required)
                          if not data['site_records'][site + ':' + tid].get('infohash')]
            for tid in unresolved[:max_resolve]:
                if stopping(): raise UploadBlocked('Obligation read interrupted by shutdown')
                content = await adapter.download_listing(adapter.origin + '/details.php?id=' + tid)
                await app._store_call('bind_obligation_listing', site, tid, _extract_torrent_info_hash(content))
            complete = await app._store_call('finalize_required_snapshot', site, kind)
            result[kind] = {'status': 'ok' if complete else 'unresolved_hashes',
                            'required': len(required), 'pages': len(visited)}
        except Exception as exc:
            await app._store_call('record_failure', site, kind)
            result[kind] = {'status': 'failed', 'error_type': type(exc).__name__}
        print(f'[Cleaner Sync] site={site} kind={kind} status={result[kind]["status"]}', flush=True)
    return result


async def refresh_before_cleaner(app, *, session=None):
    global_cfg = app.CFG.get('GLOBAL_CLEAN', {})
    if not any(n.get('enable') is True and app.NodeCleaner(None, n, global_cfg).enabled()
               for n in app.CFG.get('NODES', [])):
        return {}
    repaired = await app._store_call('reconcile_verified_listings')
    print('[Cleaner Ledger] created={created} bound={bound} conflicts={conflicts}'.format(**repaired))
    data = await asyncio.to_thread(app._mapping_store().read)
    configs = {s.get('name', '').upper(): s for s in app.CFG.get('SITE', []) if s.get('enable') is True}
    result = {}
    # A missing website implementation is not evidence that obligations are clear.
    for site in ('DEDBIT', 'BITSUSE', 'TORRENTDD'):
        if site in configs:
            mode = global_cfg.get('site_obligations', {}).get(site)
            result[site] = {'status': 'configured_cleaner_policy' if mode == 'cleaner_policy'
                            and site in ('DEDBIT', 'BITSUSE') else 'unsupported_obligations_retained'}
    if 'BEARBIT' not in configs:
        return result
    proofs = data['site_sync'].get('BEARBIT', {})
    if all(proofs.get(k, {}).get('status') == 'ok' and fresh(proofs[k])
           and proofs[k].get('verification_method') == 'authenticated_complete_site_page_hashes' for k in KINDS):
        result['BEARBIT'] = {'status': 'fresh'}
        return result
    from upload_runner import UploadSession
    owns_session = session is None
    if owns_session: session = UploadSession(app, 'cleaner_' + str(os.getpid()))
    try:
        adapter = await session.adapter('BEARBIT')
        result['BEARBIT'] = await sync_site(app, adapter)
    except Exception as exc:
        for kind in KINDS: await app._store_call('record_failure', 'BEARBIT', kind)
        result['BEARBIT'] = {'status': 'failed', 'error_type': type(exc).__name__}
        print('[Cleaner Sync] site=BEARBIT status=failed error_type=' + type(exc).__name__)
    finally:
        if owns_session:
            try:
                await session.close()
            except Exception as exc:
                print('[Cleaner Sync] browser_cleanup=failed error_type=' + type(exc).__name__)
    return result


async def refresh_cleaner_evidence(app):
    """Share one browser for obligation and upload-peer checks in this cycle."""
    stop = getattr(app, 'stop_event', None)
    if stop is not None and stop.is_set():
        return {'obligations': {}, 'uploads': []}
    from upload_runner import UploadSession
    from auto_upload_retention import refresh_upload_retention
    session = UploadSession(app, 'cleaner_' + str(os.getpid()))
    try:
        obligations = await refresh_before_cleaner(app, session=session)
        uploads = [] if stop is not None and stop.is_set() else await refresh_upload_retention(app, session=session)
        return {'obligations': obligations, 'uploads': uploads}
    finally:
        try:
            await session.close()
        except Exception as exc:
            raise UploadBlocked('Cleaner browser cleanup failed: ' + type(exc).__name__) from None
