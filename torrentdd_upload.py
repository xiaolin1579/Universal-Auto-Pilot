"""TorrentDD upload form and duplicate checks, using the shared durable worker.

Reading/filling never publishes. Only submit() sends the already-claimed job.
"""
import asyncio
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from auto_upload import UploadBlocked, effective_rule, upload_title
from torrent_metainfo import (payload_signature, duplicate_payload_status,
                              _extract_torrent_info_hash, valid_target_announce)
import torrentdd_cross_seed as tdd


def zone_for_category(category):
    value = str(category)
    if value in {str(i) for i in range(34, 39)}:
        return 'special'
    if value in {str(i) for i in range(1, 34)} | {'40'}:
        return 'normal'
    raise UploadBlocked('Invalid TorrentDD destination category')


def upload_form(html, current_url, category):
    from upload_sites import trusted_url
    trusted_url('TORRENTDD', current_url)
    if urlsplit(current_url).path != '/upload.php':
        raise UploadBlocked('TorrentDD upload form redirected')
    zone_for_category(category)
    soup = BeautifulSoup(html, 'html.parser')
    logout = False
    for anchor in soup.select('a[href]'):
        try:
            logout = logout or tdd._url(anchor['href']).path == '/logout.php'
        except ValueError:
            continue
    if not logout or soup.select_one('input[type=password]'):
        raise UploadBlocked('TorrentDD authenticated upload form unavailable')
    forms = soup.select('form[name="upload"]')
    if len(forms) != 1:
        raise UploadBlocked('TorrentDD upload form missing or ambiguous')
    form = forms[0]
    if (form.get('method', '').lower() != 'post'
            or form.get('enctype', '').lower() != 'multipart/form-data'
            or urljoin(current_url, form.get('action', '')) != tdd.ORIGIN + '/takeupload.php'):
        raise UploadBlocked('Unrecognized TorrentDD upload action')
    expected = {'type': ('select', None), 'file': ('input', 'file'), 'name': ('input', 'text'),
                'poster': ('input', 'text'), 'descr': ('textarea', None),
                'countdown': ('input', 'text'), 'code_id': ('input', 'text'),
                'cf1': ('input', 'checkbox'), 'cf2': ('input', 'checkbox')}
    controls = form.select('input[name], select[name], textarea[name]')
    if {e['name'] for e in controls} != set(expected) or len(controls) != len(expected):
        raise UploadBlocked('TorrentDD upload controls changed')
    for name, (tag, kind) in expected.items():
        e = form.find(attrs={'name': name})
        if e.name != tag or kind and e.get('type', '').lower() != kind or e.has_attr('disabled'):
            raise UploadBlocked('Unrecognized TorrentDD form control')
    if str(category) not in {o.get('value') for o in form.select('select[name=type] option')}:
        raise UploadBlocked('TorrentDD category unavailable on current form')
    announce_nodes = soup.select('#url-copy')
    if len(announce_nodes) != 1:
        raise UploadBlocked('TorrentDD account announce unavailable')
    announce = announce_nodes[0].get_text('', strip=True)
    query = parse_qs(urlsplit(announce).query)
    if (not valid_target_announce('TORRENTDD', announce)
            or len(query.get('passkey', [])) != 1 or not query['passkey'][0]):
        raise UploadBlocked('TorrentDD account announce invalid')
    return {'announce': announce,
            'fields': {'poster': {'options': None, 'format': 'public_url'},
                       'code_id': {'options': None, 'required': False, 'pattern': r'[0-9]{0,10}'}},
            'acknowledgements': ['cf1', 'cf2']}


async def download_listing(adapter, url):
    from upload_sites import listing_identity
    listing = listing_identity('TORRENTDD', url)
    html, current = await adapter.open(listing['details_url'])
    if listing_identity('TORRENTDD', current) != listing:
        raise UploadBlocked('TorrentDD listing identity changed')
    downloads = tdd._downloads(BeautifulSoup(html, 'html.parser'), listing['torrent_id'])
    if len(downloads) != 1:
        raise UploadBlocked('TorrentDD canonical download unavailable or ambiguous')
    try:
        return await tdd.download_candidate(adapter.tab, listing['details_url'], downloads.pop())
    except ValueError:
        raise UploadBlocked('TorrentDD canonical metainfo unavailable') from None


async def check_duplicates(adapter, job, max_pages=10, max_candidates=80):
    from upload_sites import duplicate_search_keywords, listing_identity, candidate_relevant
    zone = zone_for_category(job['target_category'])
    if job['target_zone'] != zone:
        raise UploadBlocked('TorrentDD category and destination zone disagree')
    path = '/browse18.php' if zone == 'special' else '/browse.php'
    if adapter.enabled_paths is not None and path not in adapter.enabled_paths:
        raise UploadBlocked('TorrentDD destination zone is disabled')
    source = Path(job['source_artifact']).read_bytes()
    if _extract_torrent_info_hash(source) != job['source_hash']:
        raise UploadBlocked('Source artifact changed during duplicate search')
    keywords = duplicate_search_keywords(job['metadata'], 'TORRENTDD')
    if not keywords:
        raise UploadBlocked('No usable TorrentDD duplicate search keyword')
    checked = set(); pages = 0; skipped = set()
    def unknown(reason):
        return {'status': 'unknown', 'complete': False, 'reason': reason}
    for keyword in keywords:
        relative = tdd.build_search_url(keyword, target_path=path)
        pending = [adapter.origin + '/' + relative]; seen = set()
        while pending:
            url = pending.pop(0)
            if url in seen:
                continue
            if pages >= max_pages:
                return unknown('page_budget_exceeded')
            seen.add(url); pages += 1
            html, actual = await adapter.open(url)
            soup = BeautifulSoup(html, 'html.parser')
            try:
                tdd.validate_search_page(soup, actual, url)
            except ValueError:
                return unknown('search_page_unverified')
            candidates = []
            # Every body row must be understood, including rows the discovery
            # parser intentionally omits. Partial parsing cannot prove no duplicate.
            for table in soup.select(tdd.TABLE):
                for row in table.find_all('tr'):
                    if row.find_parent('table') is not table or not row.find_all('td', recursive=False):
                        continue
                    data = tdd.extract_row(row, adapter.origin)
                    if data is None:
                        return unknown('unrecognized_result_row')
                    candidates.append(data)
            if not candidates and 'ไม่พบรายการที่ค้นหา' not in soup.get_text(' ', strip=True):
                return unknown('empty_result_section_unverified')
            for anchor in soup.select('a[href]'):
                target = urljoin(actual, anchor['href']); p = urlsplit(target)
                query = parse_qs(p.query, keep_blank_values=True)
                if p.path != path or 'page' not in query:
                    continue
                expected = parse_qs(urlsplit(url).query, keep_blank_values=True)
                expected.pop('page', None)
                filters = dict(query); page_values = filters.pop('page')
                if (p.hostname != urlsplit(adapter.origin).hostname or filters != expected
                        or len(page_values) != 1 or not page_values[0].isdigit()):
                    return unknown('pagination_query_unverified')
                if target not in seen and target not in pending:
                    pending.append(target)
            for data in candidates:
                details = data['details_url']
                if details in checked:
                    continue
                if not candidate_relevant(job['metadata'], data['title']):
                    skipped.add(details); continue
                if len(checked) >= max_candidates:
                    return unknown('candidate_budget_exceeded')
                canonical = await adapter.download_listing(details)
                checked.add(details)
                signature = payload_signature(canonical)
                status = ('duplicate' if signature == job['content_group_id'] else
                          duplicate_payload_status(source, canonical))
                if status in ('duplicate', 'possible_duplicate'):
                    return {'status': status, 'complete': status == 'duplicate',
                            'listing': listing_identity('TORRENTDD', details),
                            'method': 'authenticated_search_and_payload'}
    return {'status': 'clear', 'complete': True, 'queries': keywords, 'pages': pages,
            'candidates_checked': len(checked), 'unrelated_results_skipped': len(skipped),
            'method': 'authenticated_search_and_payload'}


async def fill(adapter, job, artifact):
    from upload_sites import public_url
    if not adapter.form:
        raise UploadBlocked('TorrentDD form has not been validated')
    adapter._torrentdd_submission = None
    rule = effective_rule(job)
    poster = rule.get('fields', {}).get('poster', '')
    if not public_url(poster):
        raise UploadBlocked('TorrentDD requires a public poster image URL')
    title = upload_title(job); description = job['metadata']['description']
    # Do not let the website's key handler silently truncate the description.
    if len(title.encode('utf-16-le')) // 2 > 200 or len(description.encode('utf-16-le')) // 2 > 9000:
        raise UploadBlocked('TorrentDD title or description exceeds its supported limit')
    code_id = rule.get('fields', {}).get('code_id', '')
    if type(code_id) is not str or re.fullmatch(r'[0-9]{0,10}', code_id) is None:
        raise UploadBlocked('TorrentDD Code ID must be at most ten digits')
    values = {'type': str(job['target_category']), 'name': title, 'poster': poster,
              'descr': description, 'code_id': code_id}
    if any(rule.get('confirmations', {}).get(key) is not True for key in ('cf1', 'cf2')):
        raise UploadBlocked('TorrentDD factual upload confirmations are incomplete')
    artifact = Path(artifact).resolve()
    try:
        content = artifact.read_bytes()
        artifact_hash = _extract_torrent_info_hash(content)
    except (OSError, ValueError):
        raise UploadBlocked('TorrentDD submission artifact unavailable or invalid') from None
    if job.get('prepared_hash') and artifact_hash != job['prepared_hash']:
        raise UploadBlocked('TorrentDD submission artifact no longer matches prepared hash')
    script = """(() => {
        const forms = document.querySelectorAll('form[name=upload]');
        if (location.origin !== 'https://www.torrentdd.com' || location.pathname !== '/upload.php' || forms.length !== 1) return false;
        const form = forms[0], values = VALUES;
        if (new URL(form.action).href !== location.origin + '/takeupload.php') return false;
        const poster = form.querySelector('input[name=poster]');
        if (!poster) return false;
        poster.classList.remove('border-success', 'text-success');
        poster.readOnly = false;
        for (const [name,value] of Object.entries(values)) {
            const controls = Array.from(form.elements).filter(e => e.name === name);
            if (controls.length !== 1 || controls[0].disabled) return false;
            controls[0].value = value;
            controls[0].dispatchEvent(new Event('input', {bubbles:true}));
            controls[0].dispatchEvent(new Event('change', {bubbles:true}));
        }
        form.querySelector('input[name=cf1]').checked = true;
        form.querySelector('input[name=cf2]').checked = true;
        const check = document.querySelector('.check-pic');
        if (!check) return false;
        check.disabled = false;
        check.click();
        return true;
    })()""".replace('VALUES', json.dumps(values, ensure_ascii=True))
    if await adapter.tab.evaluate(script, return_by_value=True) is not True:
        raise UploadBlocked('TorrentDD form could not be filled')
    for _ in range(60):
        valid = await adapter.tab.evaluate("""(() => {
            const input = document.querySelector('form[name=upload] input[name=poster]');
            return !!input && input.classList.contains('border-success') && input.readOnly;
        })()""", return_by_value=True)
        if valid is True:
            break
        await asyncio.sleep(.25)
    else:
        raise UploadBlocked('TorrentDD website could not validate the poster image')
    import nodriver as uc
    document = await adapter.tab.send(uc.cdp.dom.get_document())
    node = await adapter.tab.send(uc.cdp.dom.query_selector(document.node_id, 'form[name=upload] input[type=file][name=file]'))
    if not node:
        raise UploadBlocked('TorrentDD torrent file control unavailable')
    await adapter.tab.send(uc.cdp.dom.set_file_input_files([str(artifact)], node_id=node))
    adapter._torrentdd_submission = {'values': values, 'job_id': job.get('id'), 'prepared_hash': job.get('prepared_hash'), 'file_name': artifact.name,
        'file_size': len(content), 'path': str(artifact), 'sha256': hashlib.sha256(content).hexdigest()}


async def submit(adapter, job):
    from upload_sites import listing_identity, trusted_url, authenticated
    adapter._publication_context = {'stage': 'prepare_submit', 'page_path': '/upload.php'}
    snapshot = getattr(adapter, '_torrentdd_submission', None)
    if (not snapshot or snapshot['job_id'] != job.get('id') or snapshot['prepared_hash'] != job.get('prepared_hash')
            or hashlib.sha256(Path(snapshot['path']).read_bytes()).hexdigest() != snapshot['sha256']):
        raise UploadBlocked('TorrentDD submission artifact changed; reconcile before another POST')
    expected = {key: snapshot[key] for key in ('values', 'file_name', 'file_size')}
    adapter._torrentdd_submission = None
    # The durable worker has already recorded submitting before this call.
    adapter._publication_context['stage'] = 'submit'
    sent = await adapter.tab.evaluate("""(() => {
        if (location.origin !== 'https://www.torrentdd.com' || location.pathname !== '/upload.php') return false;
        const form = document.querySelector('form[name=upload]');
        if (!form || new URL(form.action).href !== location.origin + '/takeupload.php' ||
            form.method.toLowerCase() !== 'post' || form.enctype.toLowerCase() !== 'multipart/form-data') return false;
        const names = ['type','file','name','poster','descr','countdown','code_id','cf1','cf2'];
        const controls = Array.from(form.elements).filter(e => e.name);
        if (controls.length !== names.length || names.some(name => controls.filter(e => e.name === name).length !== 1)) return false;
        const expected = EXPECTED;
        for (const [name,value] of Object.entries(expected.values)) {
            const controls = Array.from(form.elements).filter(e => e.name === name);
            if (controls.length !== 1 || controls[0].disabled || controls[0].value !== value) return false;
        }
        if (!form.checkValidity() || !form.querySelector('input[name=cf1]:checked') ||
            !form.querySelector('input[name=cf2]:checked')) return false;
        const poster = form.querySelector('input[name=poster]');
        const file = form.querySelector('input[name=file]');
        if (!poster || !poster.readOnly || !poster.classList.contains('border-success') ||
            !file || file.files.length !== 1 || file.files[0].name !== expected.file_name ||
            file.files[0].size !== expected.file_size) return false;
        // Avoid validateForm()'s own submit call followed by native submit.
        HTMLFormElement.prototype.submit.call(form);
        return true;
    })()""".replace('EXPECTED', json.dumps(expected, ensure_ascii=True)), return_by_value=True)
    if sent is not True:
        raise UploadBlocked('TorrentDD submission not confirmed; reconcile before another POST')
    adapter._publication_context['stage'] = 'await_result'
    for _ in range(30):
        await asyncio.sleep(1)
        current = await adapter.tab.evaluate('window.location.href')
        trusted_url('TORRENTDD', current)
        adapter._publication_context['page_path'] = urlsplit(current).path
        try:
            listing = listing_identity('TORRENTDD', current)
            authenticated(BeautifulSoup(await adapter.tab.get_content(), 'html.parser'), 'TORRENTDD', current)
            return listing
        except ValueError:
            continue
    raise UploadBlocked('TorrentDD publication result unconfirmed; inspect the destination and reconcile without another POST')
