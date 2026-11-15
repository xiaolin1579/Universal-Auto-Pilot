"""Authenticated adapters for the actual BearBit, Dedbit and Bitsuse forms.

HTML is content, never executable configuration. Hidden tokens stay in the live form.
"""
from auto_upload import UploadBlocked, UploadRejected, effective_rule, upload_title
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs, urlencode
from bs4 import BeautifulSoup, NavigableString
from torrent_metainfo import _extract_torrent_info_hash, payload_signature, duplicate_payload_status
from cross_seed_search import search_keywords

SITES = {
    'BEARBIT': ('https://bearbit.org', None, {'normal': '/viewno18sbx.php', 'special': '/viewbrsb.php'}),
    'DEDBIT': ('https://www.dedbit.com', '/upload.php', {'normal': '/browse.php'}),
    'BITSUSE': ('https://www.bitsuse.com', '/bs_upload.php', {'special': '/bs_browse.php'}),
    'TORRENTDD': ('https://www.torrentdd.com', '/upload.php', {'normal': '/browse.php', 'special': '/browse18.php'}),
}


def trusted_url(site, url):
    p = urlsplit(url); base = urlsplit(SITES[site][0])
    if p.scheme != 'https' or p.hostname != base.hostname or p.port not in (None, 443) or p.username or p.password:
        raise UploadBlocked('Untrusted site URL')
    return url


def listing_identity(site, url):
    trusted_url(site, url); p = urlsplit(url)
    allowed = ('/bs_details.php',) if site == 'BITSUSE' else ('/details.php',)
    ids = parse_qs(p.query).get('id', [])
    if p.path not in allowed or len(ids) != 1 or not ids[0].isdigit():
        raise UploadBlocked('Not a canonical site listing URL')
    return {'torrent_id': ids[0], 'details_url': SITES[site][0] + p.path + '?id=' + ids[0]}


def authenticated(soup, site=None, current_url=None):
    logout = soup.select_one('a[href*="logout.php"]')
    # Bitsuse omits the navigation header on its protected upload form.
    form = soup.select_one('form#frmupload')
    token = form.select_one('input[name="csrf"][value]') if form else None
    announce = form.select_one('input[name="passkey"][value]') if form else None
    protected_listing = False
    if site == 'BITSUSE' and current_url and not soup.select_one('input[type="password"]'):
        try:
            tid = listing_identity(site, current_url)['torrent_id']
            rating = next((f for f in soup.find_all('form')
                           if urljoin(current_url, f.get('action', '')) == SITES[site][0] + '/bs_takerate.php'), None)
            csrf = rating.select_one('input[name="csrf"][value]') if rating else None
            identity = rating.select_one('input[name="id"]') if rating else None
            signed = False
            for a in soup.select('a[href]'):
                url = urljoin(current_url, a['href']); parsed = urlsplit(url); query = parse_qs(parsed.query)
                if (parsed.path == '/bs_download.php' and query.get('id') == [tid]
                        and query.get('rand') and parsed.hostname == urlsplit(SITES[site][0]).hostname):
                    signed = True
            thanks = next((f for f in soup.find_all('form')
                           if urljoin(current_url, f.get('action', '')) == SITES[site][0] + '/bs_thanks.php'), None)
            thanks_id = thanks.select_one('input[name="torrentid"]') if thanks else None
            account_form = (csrf and csrf.get('value') and identity and identity.get('value') == tid)
            account_form = account_form or (thanks_id and thanks_id.get('value') == tid)
            protected_listing = bool(account_form and signed)
        except ValueError:
            pass
    if not logout and not protected_listing and not (token and token.get('value') and announce and announce.get('value')):
        raise UploadBlocked('Authenticated page is required')


def known_upload_rejection(html):
    soup = BeautifulSoup(html, 'html.parser')
    headings = soup.find_all(['h1', 'h2', 'h3'])
    if (not soup.title or not soup.title.get_text(' ', strip=True).casefold().startswith('upload failed!')
            or not any(h.get_text(' ', strip=True).casefold() == 'upload failed!' for h in headings)):
        return None
    text = ' '.join(soup.get_text(' ', strip=True).split())
    match = re.search(r'Torrent name.*?รับได้ไม่เกิน\s*(\d+)', text, re.I)
    if match:
        return UploadRejected('title_too_long', int(match[1]))
    return None


def public_url(value):
    p = urlsplit(value)
    if (p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password
            or re.search(r'passkey|auth|token|csrf|password|secret', p.query, re.I)
            or 'announce' in p.path.lower()):
        return None
    return value.replace('[', '%5B').replace(']', '%5D')


def _bbcode_children(element):
    children = list(element.children)
    result = []
    index = 0
    while index < len(children):
        child = children[index]
        if (getattr(child, 'name', None) == 'p' and 'sub' in child.get('class', [])
                and child.get_text(' ', strip=True).casefold() == 'quote:'):
            following = index + 1
            while following < len(children) and isinstance(children[following], NavigableString) and not str(children[following]).strip():
                following += 1
            if following < len(children):
                table = children[following]
                if getattr(table, 'name', None) == 'table' and 'main' in table.get('class', []):
                    result.append('[quote]' + bbcode(table).strip() + '[/quote]\n')
                    index = following + 1
                    continue
        result.append(bbcode(child))
        index += 1
    return ''.join(result)


def bbcode(element):
    if isinstance(element, NavigableString):
        # Prevent source text from injecting BBCode links or image tags.
        return str(element).replace('[', '［').replace(']', '］')
    if element.name in ('script', 'style', 'form', 'iframe', 'input', 'button'):
        return ''
    if element.name == 'br': return '\n'
    if element.name == 'img':
        url = public_url(element.get('src', ''))
        return '[img]' + url + '[/img]\n' if url else ''
    text = _bbcode_children(element)
    if element.name == 'a':
        url = public_url(element.get('href', ''))
        return '[url=' + url + ']' + text + '[/url]' if url else text
    tag = {'strong': 'b', 'b': 'b', 'em': 'i', 'i': 'i', 'u': 'u', 'blockquote': 'quote'}.get(element.name)
    if tag: return '[' + tag + ']' + text + '[/' + tag + ']'
    return text + ('\n' if element.name in ('div', 'p', 'tr', 'li') else '')


def bearbit_metadata(html, details_url):
    identity = listing_identity('BEARBIT', details_url)
    soup = BeautifulSoup(html, 'html.parser'); authenticated(soup)
    rows = {}
    for tr in soup.find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) == 2:
            rows[cells[0].get_text(' ', strip=True).lower()] = cells[1]
    title = soup.find('h1'); desc = rows.get('description'); category = rows.get('category')
    if not title or desc is None or category is None:
        raise UploadBlocked('BearBit details structure is incomplete')
    # Download-status badges belong to the current account, not the source title.
    clean_title = re.sub(r'\s*\((?:ได้รับไปแล้ว|ดาวน์โหลดแล้ว)\)\s*$', '', title.get_text(' ', strip=True)).strip()
    description = bbcode(desc).strip()
    picture_tag = desc.find('img')
    picture = public_url(picture_tag.get('src', '')) if picture_tag else ''
    if not description:
        raise UploadBlocked('BearBit description is empty')
    return {'site': 'BEARBIT', **identity, 'title': clean_title,
            'category': category.get_text(' ', strip=True),
            'description': description,
            'picture': picture or ''}


def upload_form(site, html, current_url, category):
    if site == 'TORRENTDD':
        from torrentdd_upload import upload_form as torrentdd_form
        return torrentdd_form(html, current_url, category)
    trusted_url(site, current_url)
    if urlsplit(current_url).path != SITES[site][1]:
        raise UploadBlocked('Upload form redirected')
    soup = BeautifulSoup(html, 'html.parser'); authenticated(soup)
    form = soup.find('form', id='frmupload')
    if (not form or form.get('method', '').lower() != 'post'
            or form.get('enctype', '').lower() != 'multipart/form-data'
            or urljoin(current_url, form.get('action', '')) != SITES[site][0] + '/takeupload.php'):
        raise UploadBlocked('Unrecognized upload form')
    token = form.select_one('input[name="csrf"][type="hidden"]')
    if not token or not token.get('value') or not form.select_one('input[type="file"][name="file"]'):
        raise UploadBlocked('Upload controls or CSRF token missing')
    cats = form.select_one('select[name="type"]')
    if not cats or str(category) == '0' or str(category) not in {o.get('value') for o in cats.find_all('option')}:
        raise UploadBlocked('Destination category not available on this site')
    dynamic = None
    for script in soup.find_all('script'):
        text = script.string or script.get_text(); m = re.search(r'\bvar\s+subcatHtml\s*=\s*', text)
        if m:
            dynamic, _ = json.JSONDecoder().raw_decode(text[m.end():]); break
    if not isinstance(dynamic, dict):
        raise UploadBlocked('Dynamic category definitions missing')
    extra = BeautifulSoup(dynamic.get(str(category), ''), 'html.parser')
    fields = {}
    for e in extra.find_all(['input', 'select', 'textarea']):
        name = e.get('name')
        if not name or not re.fullmatch(r'c[0-9]+[a-zA-Z0-9_]*', name):
            raise UploadBlocked('Unrecognized category field')
        fields[name] = {'options': [o.get('value', o.get_text()) for o in e.find_all('option')]
                        if e.name == 'select' else None}
    ack = {e['name'] for e in form.select('input[type="checkbox"][name^="ack"]')}
    if ack not in ({'ack2', 'ack3'}, {'ack2', 'ack3', 'ack4'}):
        raise UploadBlocked('Unrecognized upload confirmations')
    announce = form.select_one('input[name="passkey"]')
    if not announce or not announce.get('value'):
        raise UploadBlocked('Destination announce missing')
    return {'announce': announce['value'], 'fields': fields, 'acknowledgements': sorted(ack)}


def form_readiness(spec, rule):
    """Public, token-free description of what is still needed before submission."""
    fields = rule.get('fields', {})
    if not isinstance(fields, dict):
        raise UploadBlocked('Category fields must be a dictionary')
    missing = [name for name, field in spec['fields'].items() if field.get('required', True)
               and (fields.get(name) is None or not str(fields.get(name, '')).strip())]
    invalid = [name for name, value in fields.items() if name in spec['fields']
               and (type(value) is not str or (str(value).strip() and spec['fields'][name]['options'] is not None
               and str(value) not in spec['fields'][name]['options']))]
    unknown = sorted(set(fields) - set(spec['fields']))
    for name, value in fields.items():
        field = spec['fields'].get(name, {})
        if field.get('pattern') and (type(value) is not str or re.fullmatch(field['pattern'], value) is None):
            invalid.append(name)
        if field.get('format') == 'public_url' and value and (type(value) is not str or not public_url(value)):
            invalid.append(name)
    confirmations = rule.get('confirmations', {})
    missing_ack = [a for a in spec['acknowledgements'] if a != 'ack3' and confirmations.get(a) is not True]
    return {'ready': not (missing or invalid or unknown or missing_ack),
            'missing_fields': sorted(missing), 'invalid_fields': sorted(invalid),
            'unknown_fields': unknown, 'missing_confirmations': sorted(missing_ack)}


def _name_aliases(metadata):
    title = re.sub(r'^(?:\[[^\]]+\]\s*[- ]*)+', '', str(metadata.get('title') or ''))
    title = re.sub(r'\([^)]*(?:ได้รับไปแล้ว|ดาวน์โหลดแล้ว)[^)]*\)', '', title)
    title = re.split(r'\((?:19|20)\d{2}\)|\b(?:S\d+E\d+|ตอน\s*\d+)', title, maxsplit=1, flags=re.I)[0]
    return [part.strip(' -[]') for part in re.split(r'\s+-\s+', title) if part.strip(' -[]')]


def duplicate_search_keywords(metadata, site=None):
    root = str(metadata.get('root_name') or '')
    terms = search_keywords(root)
    aliases = _name_aliases(metadata)
    if site == 'DEDBIT':
        normalized = re.sub(r'[._]+', ' ', root)
        prefix = re.split(r'\b(?:19|20)\d{2}\b|\bS\d+E\d+|\b(?:\d{3,4}p|4K|HDR|WEB)\b', normalized, maxsplit=1, flags=re.I)[0].strip(' -')
        names = aliases + ([prefix] if prefix else [])
        phrases = [' '.join(name.replace(chr(34), ' ').split()).strip() for name in names]
        phrases = [chr(34) + name + chr(34) for name in phrases if name]
        if phrases:
            return list(dict.fromkeys(phrases))[:4]
    if site == 'TORRENTDD':
        # A displayed Thai episode title need not contain SxxExx from the file.
        # Check both language aliases before declaring the destination clear.
        return list(dict.fromkeys(terms + aliases))[:6]
    # The primary title can start with a country/quality badge. Searching that
    # badge alone creates unrelated results; prefer the file's release identity.
    if not terms:
        return search_keywords(aliases[0] if aliases else metadata.get('title'))
    code = re.match(r'^([a-z]{2,12})[-_. ]?(\d{2,6})(?=$|[^a-z0-9])', root, re.I)
    if not code:
        for alias in reversed(aliases):
            if alias and not re.search(r'\b(?:WEB|HDR|1080p|2160p)\b', alias, re.I) and alias.casefold() not in {t.casefold() for t in terms}:
                terms.append(alias)
                break
    return list(dict.fromkeys(terms))[:4]


def source_search_keywords(metadata):
    """Keep release identity first, then discover a site's translated display title.

    These terms prove nothing alone: read_source still requires the exact source
    listing ID in its enabled zone and a download matching the live source hash.
    """
    terms = search_keywords(metadata.get('root_name'))
    terms.extend(_name_aliases(metadata))
    if not terms:
        terms = search_keywords(metadata.get('title'))
    return list(dict.fromkeys(term for term in terms if str(term).strip()))[:8]


def candidate_relevant(metadata, candidate_title):
    root = str(metadata.get('root_name') or '')
    candidate_title = str(candidate_title or '')
    if not candidate_title:
        return True
    code = re.match(r'^([a-z]{2,12})[-_. ]?(\d{2,6})(?=$|[^a-z0-9])', root, re.I)
    if code:
        pattern = r'(?<![a-z0-9])' + re.escape(code[1]) + r'[\W_]*' + re.escape(code[2]) + r'(?!\d)'
        return bool(re.search(pattern, candidate_title, re.I))
    normalized = re.sub(r'[._]+', ' ', root)
    prefix = re.split(r'\b(?:19|20)\d{2}\b|\bS\d+E\d+|\b(?:\d{3,4}p|4K|HDR|WEB)\b', normalized, maxsplit=1, flags=re.I)[0]
    aliases = [prefix] + _name_aliases(metadata)
    stop = {'the','a','an','of','and','in','on','to','by','with','from','movie','series'}
    groups = [[w.casefold() for w in re.findall(r'[^\W_]+', alias) if w.casefold() not in stop and len(w) >= 3] for alias in aliases]
    groups = [g for g in groups if g]
    text = re.sub(r'[^\w]+', '', candidate_title).casefold()
    if groups and not any(all(word in text for word in group) for group in groups):
        return False
    episode = re.search(r'S(\d+)E(\d+)', root, re.I)
    other = re.search(r'S(\d+)E(\d+)(.*)', candidate_title, re.I)
    if episode and other and not re.match(r'\s*[-–~]', other[3]):
        if (int(episode[1]),int(episode[2])) != (int(other[1]),int(other[2])):
            return False
    thai_episode = re.search(r'ตอน(?:ที่)?\s*(\d+)(.*)', candidate_title)
    if episode and thai_episode and not re.match(r'\s*[-–~]', thai_episode[2]):
        if int(episode[2]) != int(thai_episode[1]):
            return False
    return True


def navigation_query(url):
    values = parse_qs(urlsplit(url).query)
    # Both upload forms append this cache-busting parameter on navigation.
    # Listing identity, search text, filters and page numbers remain exact.
    values.pop('random', None)
    return values


class BrowserSite:
    def __init__(self, site, tab, enabled_paths=None):
        self.site, self.tab = site, tab
        self.origin = SITES[site][0]
        self.form = None
        self.enabled_paths = enabled_paths

    async def open(self, url):
        trusted_url(self.site, url)
        await asyncio.wait_for(self.tab.get(url), timeout=60)
        expected = urlsplit(url)
        deadline = asyncio.get_running_loop().time() + 60
        while asyncio.get_running_loop().time() < deadline:
            current = await self.tab.evaluate('window.location.href')
            trusted_url(self.site, current)
            actual = urlsplit(current)
            loaded = await self.tab.evaluate('document.readyState')
            if (actual.path == expected.path and navigation_query(current) == navigation_query(url)
                    and loaded in ('interactive', 'complete')):
                html = await self.tab.get_content()
                # Verify navigation did not advance while the DOM was being read.
                if await self.tab.evaluate('window.location.href') == current:
                    authenticated(BeautifulSoup(html, 'html.parser'), self.site, current)
                    return html, current
            if actual.path.endswith(('login.php', 'signin.php')) and loaded == 'complete':
                raise UploadBlocked('Site session expired during navigation')
            await asyncio.sleep(0.25)
        raise UploadBlocked('The requested page did not finish loading; stale content was refused'
                            + ' (document=' + str(loaded)[:12]
                            + ', path_match=' + str(actual.path == expected.path)
                            + ', query_match=' + str(navigation_query(current) == navigation_query(url)) + ')')

    async def download_listing(self, url):
        if self.site == 'TORRENTDD':
            from torrentdd_upload import download_listing
            return await download_listing(self, url)
        listing = listing_identity(self.site, url)
        html, current = await self.open(listing['details_url'])
        if listing_identity(self.site, current) != listing:
            raise UploadBlocked('Listing redirected to a different identity')
        soup = BeautifulSoup(html, 'html.parser'); links = set()
        for a in soup.select('a[href]'):
            link = urljoin(current, a['href']); p = urlsplit(link)
            if p.path.rsplit('/', 1)[-1] in ('download.php', 'downloadnew.php', 'bs_download.php', 'bs_downloadnew.php'):
                trusted_url(self.site, link)
                if parse_qs(p.query).get('id') == [listing['torrent_id']]: links.add(link)
        if len(links) != 1:
            raise UploadBlocked('Canonical torrent download link is ambiguous or absent: ' + self.site + ' #' + listing['torrent_id'])
        return await self.fetch_torrent(links.pop())

    async def _download_bytes(self, url):
        import aiohttp
        import nodriver as uc
        trusted_url(self.site, url)
        # Use the browser's URL-scoped cookies and User-Agent with normal TLS/proxy trust.
        cookies = {c.name: c.value for c in await self.tab.send(uc.cdp.network.get_cookies(urls=[url]))}
        headers = {'User-Agent': await self.tab.evaluate('navigator.userAgent'), 'Referer': self.origin + '/'}
        async with aiohttp.ClientSession(cookies=cookies, headers=headers, trust_env=True,
                                         timeout=aiohttp.ClientTimeout(total=40)) as session:
            for _ in range(4):
                trusted_url(self.site, url)
                async with session.get(url, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get('Location', ''))
                        continue
                    if response.status != 200:
                        raise UploadBlocked('Canonical torrent request failed')
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        content.extend(chunk)
                        if len(content) > 16777216:
                            raise UploadBlocked('Torrent metainfo exceeds size limit')
                    return bytes(content)
        raise UploadBlocked('Canonical torrent redirected too many times')

    async def fetch_torrent(self, url):
        content = await self._download_bytes(url)
        if content.lstrip().startswith(b'<'):
            # BearBit downloadnew.php can present a timed download confirmation.
            await asyncio.wait_for(self.tab.get(url), timeout=60)
            for _ in range(15):
                await asyncio.sleep(1)
                current = await self.tab.evaluate('window.location.href')
                trusted_url(self.site, current)
                ready = await self.tab.evaluate(
                    "(() => { const e=document.getElementById('bbDlBtn'); return e && !e.classList.contains('bb-disabled') ? e.href : null; })()")
                if ready:
                    trusted_url(self.site, ready)
                    content = await self._download_bytes(ready)
                    break
            else:
                raise UploadBlocked('Torrent download confirmation is unavailable')
        _extract_torrent_info_hash(content)
        return content

    async def read_source(self, url, source_hash, zone):
        if self.site != 'BEARBIT' or zone not in SITES['BEARBIT'][2]:
            raise UploadBlocked('Unsupported source')
        html, current = await self.open(url)
        listing = listing_identity('BEARBIT', current)
        if listing != listing_identity('BEARBIT', url): raise UploadBlocked('Source listing redirected')
        metadata = bearbit_metadata(html, current)
        content = await self.download_listing(current)
        if _extract_torrent_info_hash(content) != source_hash:
            raise UploadBlocked('BearBit listing hash differs from the completed client torrent')
        # Zone evidence is actual membership in that browse endpoint, not navigation links.
        path = SITES['BEARBIT'][2][zone]
        if self.enabled_paths is not None and path not in self.enabled_paths:
            raise UploadBlocked('This source zone is disabled in site configuration')
        from torrent_metainfo import _extract_torrent_manifest
        root_name = _extract_torrent_manifest(content)['root_name']
        keywords = source_search_keywords({**metadata, 'root_name': root_name})
        found = False
        for keyword in keywords:
            query = self.origin + path + '?' + urlencode({'search': keyword, 'incldead': 1, 'searchin': 0, 'sortby': 0})
            html, actual = await self.open(query)
            trusted_url('BEARBIT', actual)
            if urlsplit(actual).path != path or navigation_query(actual) != navigation_query(query):
                raise UploadBlocked('BearBit zone search redirected')
            soup = BeautifulSoup(html, 'html.parser')
            if not soup.select_one('input[name="search"]'):
                raise UploadBlocked('BearBit zone search form not recognized')
            category_in_zone = any(a.get_text(' ', strip=True) == metadata['category']
                                   and urlsplit(urljoin(actual, a.get('href', ''))).path == path
                                   for a in soup.select('a.catlink[href]'))
            if not category_in_zone:
                raise UploadBlocked('Source category is not present in this BearBit zone')
            ids = set()
            for a in soup.select('table.bb-btable a[href]'):
                link = urljoin(actual, a['href'])
                try: ids.add(listing_identity('BEARBIT', link)['torrent_id'])
                except ValueError: pass
            if listing['torrent_id'] in ids:
                found = True
                break
        if not found:
            raise UploadBlocked('Source listing membership in this BearBit zone could not be verified')
        metadata['root_name'] = root_name
        metadata.update(infohash=source_hash, zone=zone, zone_verified=True,
                        zone_evidence={'method': 'authenticated_browse_membership', 'url_path': path})
        return metadata, content

    async def load_form(self, job, require_ready=True):
        if self.site != job['target_site']: raise UploadBlocked('Wrong destination adapter')
        if self.site == 'TORRENTDD':
            from torrentdd_upload import zone_for_category
            if job.get('target_zone') != zone_for_category(job['target_category']):
                raise UploadBlocked('TorrentDD category and destination zone disagree')
        if self.enabled_paths is not None and SITES[self.site][2][job.get('target_zone', 'special' if self.site == 'BITSUSE' else 'normal')] not in self.enabled_paths:
            raise UploadBlocked('Destination zone is disabled in site configuration')
        html, current = await self.open(self.origin + SITES[self.site][1])
        self.form = upload_form(self.site, html, current, job['target_category'])
        self.form['readiness'] = form_readiness(self.form, effective_rule(job))
        if require_ready and not self.form['readiness']['ready']:
            missing = self.form['readiness']
            fields = missing['missing_fields'] + missing['invalid_fields'] + missing['unknown_fields']
            names = fields + missing['missing_confirmations']
            raise UploadBlocked('Destination form is incomplete: ' + ', '.join(names))
        return self.form

    async def check_duplicates(self, job, max_pages=10, max_candidates=80):
        if self.site == 'TORRENTDD':
            from torrentdd_upload import check_duplicates
            return await check_duplicates(self, job, max_pages, max_candidates)
        path = SITES[self.site][2][job['target_zone']]
        keywords = duplicate_search_keywords(job['metadata'], self.site)
        if not keywords: raise UploadBlocked('No usable duplicate search keyword')
        source = Path(job['source_artifact']).read_bytes() if job.get('source_artifact') else None
        if source is not None and _extract_torrent_info_hash(source) != job['source_hash']:
            raise UploadBlocked('Source artifact hash changed during duplicate search')
        checked = {}; page_count = 0; skipped = set()
        for keyword in keywords:
            pending = [self.origin + path + '?' + urlencode({'search': keyword, 'incldead': 1})]
            seen = set()
            while pending:
                url = pending.pop(0)
                if url in seen: continue
                if page_count >= max_pages: return {'status': 'unknown', 'complete': False, 'reason': 'page_budget_exceeded'}
                seen.add(url); page_count += 1
                html, actual = await self.open(url); soup = BeautifulSoup(html, 'html.parser')
                if urlsplit(actual).path != path or not soup.select_one('input[name="search"]'):
                    return {'status': 'unknown', 'complete': False}
                candidates = set(); titles = {}
                tables = soup.select('table.torrenttable')
                for table in tables:
                    for a in table.select('a[href]'):
                        link = urljoin(actual, a['href'])
                        try:
                            details = listing_identity(self.site, link)['details_url']
                            candidates.add(details)
                            titles.setdefault(details, []).append(a.get_text(' ', strip=True))
                        except ValueError: pass
                # Account widgets/recommendations outside the result table are unrelated.
                pagination = tables[0].parent if tables else soup.select_one('#content')
                for a in pagination.select('a[href]') if pagination else []:
                    link = urljoin(actual, a['href']); p = urlsplit(link); q = parse_qs(p.query)
                    if p.path == path and 'page' in q and p.hostname == urlsplit(self.origin).hostname:
                        if q.get('search') != [keyword]:
                            return {'status': 'unknown', 'complete': False}
                        trusted_url(self.site, link)
                        if link not in seen and link not in pending: pending.append(link)
                # A search form alone does not prove its result section loaded successfully.
                if not candidates and not re.search(r'ไม่พบ|ไม่มีทอร์เรนต์|ไม่เจอ|No torrents|Nothing found', soup.get_text(' ', strip=True), re.I):
                    return {'status': 'unknown', 'complete': False}
                for url in sorted(candidates):
                    if url in checked: continue
                    if not any(candidate_relevant(job['metadata'], title) for title in titles.get(url, [''])):
                        skipped.add(url); continue
                    if len(checked) >= max_candidates: return {'status': 'unknown', 'complete': False, 'reason': 'candidate_budget_exceeded'}
                    canonical = await self.download_listing(url)
                    signature = payload_signature(canonical); checked[url] = signature
                    status = ('duplicate' if signature == job['content_group_id'] else
                              duplicate_payload_status(source, canonical) if source is not None else 'different')
                    if status in ('duplicate', 'possible_duplicate'):
                        return {'status': status, 'complete': status == 'duplicate',
                                'listing': listing_identity(self.site, url), 'method': 'authenticated_search_and_payload'}
        return {'status': 'clear', 'complete': True, 'queries': keywords,
                'pages': page_count, 'candidates_checked': len(checked), 'unrelated_results_skipped': len(skipped), 'method': 'authenticated_search_and_payload'}

    async def fill(self, job, artifact):
        if self.site == 'TORRENTDD':
            from torrentdd_upload import fill
            return await fill(self, job, artifact)
        if not self.form: raise UploadBlocked('Upload form has not been validated')
        rule = effective_rule(job)
        values = {'type': job['target_category'], 'name': upload_title(job),
                  'descr': job['metadata']['description'], 'picture': job['metadata'].get('picture', ''),
                  'description': rule.get('short_description', ''), **rule.get('fields', {})}
        values.update({a: True for a in self.form['acknowledgements']})
        expr = '''(() => {
          const f = document.getElementById('frmupload'), values = VALUES;
          const named = (key) => {
            const matches = Array.from(f.elements).filter(e => e.name === key);
            if (matches.length !== 1) throw new Error('Ambiguous form control name');
            return matches[0];
          };
          named('type').value = values.type;
          named('type').dispatchEvent(new Event('change', {bubbles:true}));
          if (typeof checkcat === 'function') checkcat();
          for (const [k,v] of Object.entries(values)) {
            const e = named(k);
            if (e.type === 'checkbox') e.checked = v === true; else e.value = String(v);
          }
          return f.checkValidity();
        })()'''.replace('VALUES', json.dumps(values, ensure_ascii=False))
        if await self.tab.evaluate(expr) is not True: raise UploadBlocked('Upload form validation failed')
        import nodriver as uc
        root = await self.tab.send(uc.cdp.dom.get_document())
        node = await self.tab.send(uc.cdp.dom.query_selector(root.node_id, '#frmupload input[type=file][name=file]'))
        await self.tab.send(uc.cdp.dom.set_file_input_files([artifact], node_id=node))

    async def resolve_publication(self, job):
        """Read-only recovery for Bitsuse's successful redirect back to browse."""
        evidence = await self.check_duplicates(job)
        listing = evidence.get('listing')
        if evidence.get('status') != 'duplicate' or not evidence.get('complete') or not listing:
            return None
        canonical = await self.download_listing(listing['details_url'])
        if (_extract_torrent_info_hash(canonical) != job['prepared_hash']
                or payload_signature(canonical) != job['content_group_id']):
            return None
        html, current = await self.open(listing['details_url'])
        soup = BeautifulSoup(html, 'html.parser')
        edit_path = '/bs_edit.php' if self.site == 'BITSUSE' else '/edit.php'
        can_edit = any(urlsplit(urljoin(current, a['href'])).path == edit_path
                       and parse_qs(urlsplit(a['href']).query).get('id') == [listing['torrent_id']]
                       and urlsplit(urljoin(current, a['href'])).hostname == urlsplit(self.origin).hostname
                       for a in soup.select('a[href]'))
        return listing if can_edit else None

    async def submit(self, job):
        if self.site == 'TORRENTDD':
            from torrentdd_upload import submit
            return await submit(self, job)
        # This is the only publication action. Worker has durably claimed submitting.
        await self.tab.evaluate("document.getElementById('frmupload').requestSubmit()")
        for _ in range(30):
            await asyncio.sleep(1)
            current = await self.tab.evaluate('window.location.href')
            trusted_url(self.site, current)
            if urlsplit(current).path == '/takeupload.php':
                rejected = known_upload_rejection(await self.tab.get_content())
                if rejected:
                    raise rejected
            try:
                listing = listing_identity(self.site, current)
                authenticated(BeautifulSoup(await self.tab.get_content(), 'html.parser'), self.site, current)
                return listing
            except ValueError: pass
        if self.site == 'BITSUSE' and urlsplit(current).path == '/bs_browse.php':
            listing = await self.resolve_publication(job)
            if listing:
                return listing
        raise RuntimeError('Publication result uncertain; reconcile this job without another POST')
