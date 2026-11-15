"""Authenticated Unlimitz metadata readers; client mutations stay in shared executors."""
import asyncio
import math
import re
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup
import unlimitz_login

ORIGIN = unlimitz_login.ORIGIN
HOSTS = {'www.unlimitz.biz', 'unlimitz.biz'}
MAX_BYTES = 16 * 1024 * 1024
NORMAL_CATS = {84,93,112,101,90,92,91,100,82,83,110,119,81,89,120,114,98,107,
               103,113,88,115,99,97,118,111,94,87,96,108,106,104,116,117,105}
SIZE = re.compile(r'^([\d,]+(?:\.\d+)?)\s*(bytes?|[kmgt]i?b)$', re.I)


class UnlimitzError(ValueError):
    """Controlled errors never include download tokens, passkeys or cookies."""


def _url(value, base=ORIGIN + '/'):
    p = urlsplit(urljoin(base, str(value or '')))
    if (not unlimitz_login._trusted_url(p.geturl()) or p.fragment):
        raise UnlimitzError('Untrusted Unlimitz URL')
    return p


def _details(value, expected=None):
    p = _url(value); q = parse_qs(p.query)
    ids = q.get('id', [])
    if (p.path != '/details.php' or len(ids) != 1 or not re.fullmatch(r'[1-9][0-9]*', ids[0])
            or set(q) - {'id', 'hit', 'filelist', 'dllist', 'tocomm'}
            or expected is not None and ids[0] != str(expected)):
        raise UnlimitzError('Invalid Unlimitz details ID')
    return ids[0], ORIGIN + '/details.php?' + urlencode({'id': ids[0]})


def _download(value, tid, base=ORIGIN + '/'):
    p = _url(value, base); q = parse_qs(p.query, keep_blank_values=True)
    if p.path == '/d.php':
        valid = (set(q) == {'keyalert1', 'keyalert2'} and q['keyalert1'] == [str(tid)]
                 and len(q['keyalert2']) == 1 and re.fullmatch(r'[A-Za-z0-9_.+ =-]{1,128}', q['keyalert2'][0]))
    else:
        valid = (re.fullmatch(r'/dI\.php/' + re.escape(str(tid)) + r'/[A-Za-z0-9_.+% =-]{1,256}', p.path)
                 and set(q) <= {'pkid'} and all(len(v) == 1 and v[0] for v in q.values()))
        token = unquote(p.path.rsplit('/', 1)[-1])
        valid = valid and token not in {'.', '..'} and re.fullmatch(r'[A-Za-z0-9_.+ =-]{1,128}', token)
    if not valid:
        raise UnlimitzError('Invalid Unlimitz download route or ID')
    return p.geturl()


def build_search_url(keyword, selected_cats=None, target_path=None, incldead=1, searchin=0, freeload=False):
    try:
        p = _url(target_path or '/browse.php')
        keyword = re.sub(r'\s+', ' ', str(keyword or '')).strip()
        if not keyword or p.path not in {'/browse.php', '/clip18x.php'} or freeload:
            return None
        cats = selected_cats if isinstance(selected_cats, (list, tuple)) else ([] if selected_cats is None else [selected_cats])
        cats = [int(str(c)) for c in cats]
        allowed = {200} if p.path == '/clip18x.php' else NORMAL_CATS
        if len(cats) > 1 or any(c not in allowed for c in cats):
            return None
        if int(incldead) not in {0,1,2} or int(searchin) not in {0,1,2}:
            return None
        params = {'search': keyword, 'cat': cats[0] if cats else (200 if p.path == '/clip18x.php' else 0),
                  'blah': int(searchin), 'incldead': int(incldead)}
        return p.path.lstrip('/') + '?' + urlencode(params)
    except (ValueError, TypeError):
        return None


def _links(row):
    links = []
    for a in row.find_all('a', href=True):
        try:
            tid, url = _details(a['href'])
            if not parse_qs(urlsplit(a['href']).query).get('filelist'):
                links.append((tid, url, a))
        except ValueError:
            pass
    return links


def extract_row(row, base_url=ORIGIN):
    _url(base_url)
    cells = row.find_all('td', recursive=False)
    if len(cells) != 13:
        return None
    links = _links(cells[1])
    if not links or len({x[0] for x in links}) != 1:
        return None
    tid, details, title = max(links, key=lambda x: len(x[2].get_text(' ', strip=True)))
    downloads = set()
    for a in cells[2].find_all('a', href=True):
        try: downloads.add(_download(a['href'], tid))
        except ValueError: pass
    size = cells[8].get_text(' ', strip=True)
    if len(downloads) != 1 or not SIZE.fullmatch(size):
        return None
    def count(cell):
        text = cell.get_text(' ', strip=True).replace(',', '')
        return int(text) if text.isdigit() else 0
    return {'id': tid, 'title': title.get_text(' ', strip=True), 'details_url': details,
            'download_url': next(iter(downloads)), 'size_str': size,
            'seeders': count(cells[10]), 'leechers': count(cells[11]), 'completed': 0,
            'raw_date': cells[7].get_text(' ', strip=True)}


def search_rows(soup):
    return [r for r in soup.find_all('tr') if extract_row(r) is not None]


def _authenticated(soup):
    if soup.select_one('input[type=password], input[name=captcha]'):
        return False
    for a in soup.find_all('a', href=True):
        try:
            if _url(a['href']).path == '/logout.php': return True
        except ValueError: pass
    return False


def validate_search_page(soup, current_url, expected_url):
    current, expected = _url(current_url), _url(expected_url)
    if (current.path not in {'/browse.php', '/clip18x.php'} or current.path != expected.path
            or parse_qs(current.query, keep_blank_values=True) != parse_qs(expected.query, keep_blank_values=True)
            or not _authenticated(soup)):
        raise UnlimitzError('Unlimitz search page does not match requested zone/query')
    forms = [f for f in soup.find_all('form') if f.select_one('input[name=search]')]
    if not any(_url(f.get('action', ''), current_url).path == current.path for f in forms):
        raise UnlimitzError('Unlimitz search controls unavailable')


def _entry(name, size):
    match = SIZE.fullmatch(size)
    if not name or not match:
        raise UnlimitzError('Invalid Unlimitz file row')
    text, unit = match.groups(); number = float(text.replace(',', ''))
    if not math.isfinite(number): raise UnlimitzError('Invalid Unlimitz file size')
    power = 0 if unit.lower().startswith('b') else 'kmgt'.index(unit[0].lower()) + 1
    factors = [1] if not power else ([1024 ** power] if 'i' in unit.lower() else [1024 ** power, 1000 ** power])
    return {'name': name, 'display_size': size, 'size_estimates': [
        {'bytes': round(number * f), 'tolerance_bytes': max(1, math.ceil(.5 * 10 ** (-len(text.partition('.')[2])) * f))}
        for f in factors]}


def parse_filelist(html, tid, current_url):
    tid, details = _details(current_url, tid)
    soup = BeautifulSoup(html, 'html.parser')
    anchors = soup.select('a[name=filelist]')
    if not _authenticated(soup) or len(anchors) != 1:
        raise UnlimitzError('Unlimitz authenticated File List unavailable')
    table = anchors[0].find_next('table')
    rows = table.find_all('tr') if table else []
    if not rows or [x.get_text(' ', strip=True) for x in rows[0].find_all('td', recursive=False)] != ['Path', 'Size']:
        raise UnlimitzError('Unrecognized Unlimitz File List headers')
    entries = []
    for row in rows[1:]:
        cells = row.find_all('td', recursive=False)
        if len(cells) != 2: raise UnlimitzError('Invalid Unlimitz File List row')
        entries.append(_entry(cells[0].get_text(' ', strip=True), cells[1].get_text(' ', strip=True)))
    if not entries or len({e['name'] for e in entries}) != len(entries):
        raise UnlimitzError('Empty or ambiguous Unlimitz File List')
    return {'url': details + '&filelist=1', 'title': '', 'entries': entries, 'visibility': 'visible'}


async def read_filelist(tab, tid, details_url):
    _, url = _details(details_url, tid)
    state = await unlimitz_login._navigate(tab, url + '&filelist=1')
    return parse_filelist(state.get('html', ''), tid, state.get('url', ''))


async def download_candidate(tab, details_url, download_url):
    """Follow only bound same-site metadata routes, including the confirmation gate."""
    try:
        import nodriver as uc
        import requests
        from torrent_metainfo import _extract_torrent_manifest
        tid, details = _details(details_url)
        current = _download(download_url, tid)
        cookies = await tab.send(uc.cdp.network.get_cookies(urls=[current]))
        agent = await tab.evaluate('navigator.userAgent')
        def transfer():
            with requests.Session() as session:
                session.headers.update({'User-Agent': agent, 'Referer': details})
                for c in cookies:
                    # Domain/path matching is handled by the cookie jar, never flattened.
                    if str(c.domain).lstrip('.') in HOSTS:
                        session.cookies.set(c.name, c.value, domain=c.domain, path=c.path, secure=c.secure)
                url = current
                for _ in range(4):
                    with session.get(url, timeout=(10,30), allow_redirects=False, stream=True) as response:
                        if response.status_code in {301,302,303,307,308}:
                            url = _download(response.headers.get('Location', ''), tid, url)
                            continue
                        if response.status_code != 200: raise UnlimitzError('Unlimitz metadata request failed')
                        chunks, length = [], 0
                        for chunk in response.iter_content(65536):
                            length += len(chunk)
                            if length > MAX_BYTES: raise UnlimitzError('Unlimitz metadata exceeds size limit')
                            chunks.append(chunk)
                        content = b''.join(chunks)
                    if urlsplit(url).path == '/d.php':
                        soup = BeautifulSoup(content, 'html.parser'); links = set()
                        for a in soup.find_all('a', href=True):
                            if '/dI.php/' not in a['href']: continue
                            links.add(_download(a['href'], tid, url))
                        if len(links) != 1: raise UnlimitzError('Unlimitz confirmation link unavailable or ambiguous')
                        url = next(iter(links)); continue
                    _extract_torrent_manifest(content)
                    return content
                raise UnlimitzError('Unlimitz metadata redirect limit exceeded')
        return await asyncio.to_thread(transfer)
    except Exception:
        raise UnlimitzError('Unlimitz candidate metadata unavailable or invalid') from None
