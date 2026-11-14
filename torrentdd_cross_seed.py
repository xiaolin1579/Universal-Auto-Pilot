"""TorrentDD search and authenticated metadata readers used by Cross-Seed.

The helpers discover candidates; only the common manifest/recheck pipeline may
decide whether they can seed existing files. Nothing here changes client state.
"""
import asyncio
import math
import re
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup


ORIGIN = 'https://www.torrentdd.com'
HOSTS = {'torrentdd.com', 'www.torrentdd.com'}
TABLE = ('table.table.table-striped.table-hover.table-borderless.bg-white.border.border-info, '
         'table.table.table-striped.table-hover.table-borderless.bg-white.border.border-danger')
MAX_TORRENT_BYTES = 16 * 1024 * 1024


class TorrentDDError(ValueError):
    """A controlled metadata error without request or session values."""

_SIZE = re.compile(r'^([\d,]+(?:\.\d+)?)\s*(bytes?|[kmgt]i?b)$', re.I)
_DOWNLOAD = re.compile(r'^/download\.php/([1-9][0-9]*)/([^/]+\.torrent)$', re.I)
_ONCLICK = re.compile(r'^\s*(?:document|window)\.location(?:\.href)?\s*=\s*([\'\"])(.*?)\1\s*;?\s*$', re.S)


def _id(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'[1-9][0-9]*', value):
        raise TorrentDDError('Invalid TorrentDD torrent ID')
    return value


def _url(value, base=ORIGIN + '/'):
    try:
        parsed = urlsplit(urljoin(base, str(value or '')))
        valid = (parsed.scheme == 'https' and parsed.hostname in HOSTS
                 and parsed.port in (None, 443) and not parsed.username
                 and not parsed.password and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise TorrentDDError('Untrusted TorrentDD URL')
    return parsed


def _details(value, expected=None):
    parsed = _url(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path != '/details.php' or set(query) - {'id', 'hit'} or len(query.get('id', [])) != 1:
        raise TorrentDDError('Invalid TorrentDD details URL')
    tid = _id(query['id'][0])
    if expected is not None and tid != _id(expected):
        raise TorrentDDError('TorrentDD details ID mismatch')
    return tid, ORIGIN + '/details.php?' + urlencode({'id': tid})


def _download(value, expected, base=ORIGIN + '/'):
    parsed = _url(value, base)
    match = _DOWNLOAD.fullmatch(parsed.path)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (not match or match.group(1) != _id(expected) or set(query) - {'r'}
            or ('r' in query and (len(query['r']) != 1 or not query['r'][0].isdigit()))):
        raise TorrentDDError('Invalid TorrentDD download URL or torrent ID')
    filename = unquote(match.group(2))
    if '\x00' in filename or '/' in filename or '\\' in filename or filename in ('.', '..'):
        raise TorrentDDError('Unsafe TorrentDD download filename')
    return ORIGIN + parsed.path + ('?' + urlencode({'r': query['r'][0]}) if 'r' in query else '')


def build_search_url(search_keyword, selected_cats=None, freeload=False, target_path=None):
    """Use the controls observed on the live GET search form, preserving its zone."""
    keyword = re.sub(r'\s+', ' ', str(search_keyword or '')).strip()
    if not keyword:
        return None
    try:
        parsed = _url(target_path or '/browse.php')
    except ValueError:
        return None
    if parsed.path not in ('/browse.php', '/browse18.php'):
        return None
    allowed = set(range(34, 39)) if parsed.path == '/browse18.php' else set(range(1, 34)) | {40}
    if isinstance(selected_cats, str):
        selected_cats = selected_cats.split(',')
    elif isinstance(selected_cats, int):
        selected_cats = [selected_cats]
    elif selected_cats is None:
        selected_cats = []
    if not isinstance(selected_cats, (list, tuple)):
        return None
    cats = []
    for value in selected_cats:
        try:
            cat = int(str(value).strip())
        except (ValueError, TypeError):
            return None
        if cat not in allowed:
            return None
        if cat not in cats:
            cats.append(cat)
    params = [('search', keyword)]
    if cats:
        params.append(('cate', ','.join(map(str, cats))))
    if freeload is True or freeload == 'yes':
        params.append(('freeload', 'yes'))
    return parsed.path.lstrip('/') + '?' + urlencode(params)


def _details_links(row):
    found = []
    for link in row.find_all('a', href=True):
        try:
            tid, url = _details(link['href'])
        except ValueError:
            continue
        found.append((tid, url, link))
    return found


def _downloads(row, expected):
    urls = set()
    for button in row.find_all('button', onclick=True):
        match = _ONCLICK.fullmatch(button['onclick'])
        if match:
            try:
                urls.add(_download(match.group(2), expected))
            except ValueError:
                pass
    return urls


def search_rows(soup):
    """Return only rows inside the recognized result table, excluding account UI."""
    rows = []
    for table in soup.select(TABLE):
        for row in table.find_all('tr'):
            if row.find_parent('table') is not table:
                continue
            links = _details_links(row)
            if links and len({tid for tid, _, _ in links}) == 1:
                rows.append(row)
    return rows


def validate_search_page(soup, current_url, expected_url):
    """Bind search HTML to its requested zone before persisting zone evidence."""
    current, expected = _url(current_url), _url(expected_url)
    if (current.path not in ('/browse.php', '/browse18.php')
            or current.path != expected.path
            or parse_qs(current.query, keep_blank_values=True)
            != parse_qs(expected.query, keep_blank_values=True)):
        raise TorrentDDError('TorrentDD search navigation does not match the requested query')
    logout = False
    for link in soup.find_all('a', href=True):
        try:
            logout = logout or _url(link['href']).path == '/logout.php'
        except ValueError:
            continue
    if not logout or soup.select_one('input#search') is None:
        raise TorrentDDError('TorrentDD authenticated search page unavailable')
    wrong_border = 'border-danger' if current.path == '/browse.php' else 'border-info'
    if any(wrong_border in table.get('class', []) for table in soup.select(TABLE)):
        raise TorrentDDError('TorrentDD result table belongs to a different zone')


def extract_row(row, base_url):
    _url(base_url)
    links = _details_links(row)
    if not links or len({tid for tid, _, _ in links}) != 1:
        return None
    tid, details_url, title_link = max(links, key=lambda x: len(x[2].get_text(' ', strip=True)))
    downloads = _downloads(row, tid)
    if len(downloads) != 1:
        return None
    size_texts = []
    for item in row.select('span.text-info'):
        value = re.sub(r'^\s*ขนาด\s*:\s*', '', item.get_text(' ', strip=True))
        if _SIZE.fullmatch(value):
            size_texts.append(value)
    if len(set(size_texts)) != 1:
        return None
    stats = row.select_one('div.dp-show-2')
    counts = {}
    if stats:
        for label, key in (('ปล่อย', 'seeders'), ('โหลด', 'leechers'), ('เสร็จ', 'completed')):
            match = re.search(re.escape(label) + r'\s*:\s*([\d,]+)', stats.get_text(' ', strip=True))
            if match:
                counts[key] = int(match.group(1).replace(',', ''))
    date = row.select_one('span.text-muted')
    return {'id': tid, 'title': title_link.get_text(' ', strip=True), 'details_url': details_url,
            'download_url': next(iter(downloads)), 'size_str': size_texts[0],
            'raw_date': date.get_text(' ', strip=True) if date else None,
            **{key: counts.get(key, 0) for key in ('seeders', 'leechers', 'completed')}}


def _entry(name, size):
    match = _SIZE.fullmatch(size.strip())
    if not match or not name.strip():
        raise TorrentDDError('Unrecognized TorrentDD File List row')
    text, unit = match.groups()
    number = float(text.replace(',', ''))
    if not math.isfinite(number):
        raise TorrentDDError('Invalid TorrentDD displayed file size')
    precision = len(text.partition('.')[2])
    if unit.casefold() in ('b', 'byte', 'bytes'):
        factors = [1]
    else:
        power = {'k': 1, 'm': 2, 'g': 3, 't': 4}[unit[0].casefold()]
        factors = [1024 ** power] if 'i' in unit.casefold() else [1024 ** power, 1000 ** power]
    estimates = [{'bytes': int(round(number * factor)),
                  'tolerance_bytes': max(1, int(0.5 * 10 ** (-precision) * factor + 0.999999))}
                 for factor in factors]
    return {'name': name.strip(), 'display_size': size.strip(), 'size_estimates': estimates}


def parse_filelist(html, tid, current_url):
    tid, url = _details(current_url, tid)
    soup = BeautifulSoup(html, 'html.parser')
    authenticated = any(_url(a['href']).path == '/logout.php' for a in soup.find_all('a', href=True)
                        if a['href'].split('?', 1)[0].endswith('logout.php'))
    if not authenticated:
        raise TorrentDDError('TorrentDD authenticated details page unavailable')
    if not _downloads(soup, tid):
        raise TorrentDDError('TorrentDD details page lacks its bound download link')
    modals = soup.select('#filelistModal')
    if len(modals) != 1:
        raise TorrentDDError('TorrentDD File List modal missing or ambiguous')
    modal = modals[0]
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    if modal.get_text(' ', strip=True) == 'ไม่เปิดเผย':
        return {'url': url, 'title': title, 'entries': [], 'visibility': 'hidden',
                'hidden_reason': 'explicit_not_disclosed_marker'}
    entries = []
    tables = modal.find_all('table')
    if len(tables) != 1:
        raise TorrentDDError('TorrentDD File List table missing or ambiguous')
    rows = tables[0].find_all('tr')
    if not rows or [c.get_text(' ', strip=True) for c in rows[0].find_all(['th', 'td'], recursive=False)] != ['ชื่อไฟล์', 'ขนาด']:
        raise TorrentDDError('Unrecognized TorrentDD File List headers')
    for row in rows[1:]:
        cells = row.find_all('td', recursive=False)
        if len(cells) != 2:
            raise TorrentDDError('Unrecognized TorrentDD File List row')
        entries.append(_entry(cells[0].get_text(' ', strip=True), cells[1].get_text(' ', strip=True)))
    if not entries or len({e['name'] for e in entries}) != len(entries):
        raise TorrentDDError('TorrentDD File List is empty or has duplicate paths')
    return {'url': url, 'title': title, 'entries': entries, 'visibility': 'visible'}


async def read_filelist(tab, tid, details_url):
    tid, url = _details(details_url, tid)
    await tab.get(url)
    for attempt in range(8):
        current = await tab.evaluate('window.location.href')
        try:
            _details(current, tid)
            html = await tab.get_content()
            result = parse_filelist(html, tid, current)
            after = await tab.evaluate('window.location.href')
            _details(after, tid)
            return result
        except (ValueError, TypeError):
            if attempt == 7:
                raise TorrentDDError('TorrentDD File List did not become available') from None
        await asyncio.sleep(0.5)


def _cookie_header(cookies, url):
    parsed = _url(url)
    found = []
    for cookie in cookies:
        def field(key, default=None):
            return cookie.get(key, default) if isinstance(cookie, dict) else getattr(cookie, key, default)
        domain, path = str(field('domain', '')).lower(), str(field('path', '/'))
        host_match = (parsed.hostname == domain.lstrip('.') or
                      (domain.startswith('.') and parsed.hostname.endswith(domain)))
        path_match = (parsed.path == path or
                      (parsed.path.startswith(path) and (path.endswith('/') or parsed.path[len(path):].startswith('/'))))
        name, value = str(field('name', '')), str(field('value', ''))
        if (not host_match or not path_match or field('secure', False) and parsed.scheme != 'https'
                or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                or any(c in value for c in '\r\n;')):
            continue
        found.append((len(path), name + '=' + value))
    return '; '.join(value for _, value in sorted(found, key=lambda x: -x[0]))


async def _download_candidate(tab, details_url, download_url):
    import aiohttp
    import nodriver as uc
    from torrent_metainfo import _extract_torrent_manifest

    tid, details_url = _details(details_url)
    current = _download(download_url, tid)
    user_agent = await tab.evaluate('navigator.userAgent')
    if not isinstance(user_agent, str) or '\r' in user_agent or '\n' in user_agent:
        raise TorrentDDError('Invalid browser User-Agent')
    async with aiohttp.ClientSession(trust_env=True, cookie_jar=aiohttp.DummyCookieJar(),
                                     timeout=aiohttp.ClientTimeout(total=40)) as session:
        for attempt in range(4):
            cookies = await tab.send(uc.cdp.network.get_cookies(urls=[current]))
            headers = {'User-Agent': user_agent, 'Referer': details_url,
                       'Accept': 'application/x-bittorrent, application/octet-stream'}
            cookie = _cookie_header(cookies, current)
            if cookie:
                headers['Cookie'] = cookie
            async with session.get(current, headers=headers, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    if attempt == 3 or not response.headers.get('Location'):
                        raise TorrentDDError('TorrentDD download redirect limit exceeded')
                    current = _download(response.headers['Location'], tid, current)
                    continue
                if response.status != 200:
                    raise TorrentDDError('TorrentDD download request failed')
                if response.content_length is not None and response.content_length > MAX_TORRENT_BYTES:
                    raise TorrentDDError('TorrentDD torrent exceeds metadata size limit')
                chunks, length = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    length += len(chunk)
                    if length > MAX_TORRENT_BYTES:
                        raise TorrentDDError('TorrentDD torrent exceeds metadata size limit')
                    chunks.append(chunk)
                content = b''.join(chunks)
                try:
                    _extract_torrent_manifest(content)
                except Exception as exc:
                    raise TorrentDDError('TorrentDD response is not valid torrent metainfo') from exc
                return content
    raise TorrentDDError('TorrentDD torrent download unavailable')


async def download_candidate(tab, details_url, download_url):
    try:
        return await _download_candidate(tab, details_url, download_url)
    except TorrentDDError:
        raise
    except Exception:
        # Transport/CDP errors may contain request URLs; expose only a fixed
        # message to the shared preflight logger.
        raise TorrentDDError('TorrentDD metadata transfer failed') from None
