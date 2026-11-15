"""Bounded search planning. Search terms discover candidates, never prove identity."""
import html
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit

_MEDIA_EXTENSION = re.compile(r'\.(?:torrent|mp4|mkv|avi|mov|wmv|m4v|mpg|mpeg|ts|m2ts|webm|flv|iso|zip|rar|7z)$', re.I)
_TECH = re.compile(r'\b(?:\d{3,4}p|[248]k|uhd|hdr10\+?|hdr|dv|blu[ -]?ray|b[dr]rip|remux|web[ -]?dl|web[ -]?rip|hdtv|dvd[ -]?rip|x26[45]|h[ -]?26[45]|hevc|avc|aac|dts(?:[ -]?hd)?|ddp?\d|truehd)\b', re.I)
_EPISODE = re.compile(r'\b(?:s\d{1,2}(?:e\d{1,3}(?:[ -]?e?\d{1,3})?)?|\d{1,2}x\d{1,3}|season\s+\d{1,2})\b', re.I)
_YEAR = re.compile(r'\b(?:19|20)\d{2}\b')
_CODE = re.compile(r'^\s*([A-Za-z]{2,12}-\d{2,6})(?=$|[^A-Za-z0-9])', re.I)


def search_keywords(value, limit=3):
    """Full release -> title/year or episode -> title; preserve disambiguating numbers."""
    try:
        limit = max(1, min(4, int(limit)))
    except (TypeError, ValueError, OverflowError):
        limit = 3
    value = unicodedata.normalize('NFKC', html.unescape(str(value or '')))
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', value).strip()
    # A real path may be supplied; only its final filename is a useful search term.
    if value.startswith('/') or re.match(r'^[A-Za-z]:[\\/]', value):
        value = value.replace('\\', '/').rsplit('/', 1)[-1].strip()
    else:
        value = value.replace('\\', ' ').replace('/', ' ')
    for _ in range(2):
        value = _MEDIA_EXTENSION.sub('', value)
    value = re.sub(r'\s+', ' ', value).strip()
    if not value or value.casefold() in ('unknown', 'unknown title', 'unknown file'):
        return []
    terms = []
    seen = set()

    def add(term):
        term = re.sub(r'\s+', ' ', term).strip(' ._-[]()')
        if not term or not any(c.isalpha() or c.isdigit() for c in term):
            return
        # Never fall back to just a year, resolution or other technical label.
        if _YEAR.fullmatch(term) or _TECH.fullmatch(term):
            return
        key = term.casefold()
        if key not in seen:
            seen.add(key); terms.append(term)

    add(value)
    code = _CODE.match(value)
    if code:
        add(code.group(1))
    normalized = re.sub(r'[._]+', ' ', value)
    technical = _TECH.search(normalized)
    title = normalized[:technical.start()] if technical else normalized
    title = re.sub(r'[\[\]()]', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip(' -')
    episode = _EPISODE.search(normalized)
    years = [m for m in _YEAR.finditer(title) if title[:m.start()].strip(' -')]
    if episode:
        # Quality tags can precede the episode; retain it after stripping those tags.
        prefix = title[:episode.start()] if episode.start() < len(title) else title
        prefix = re.sub(r'\s+', ' ', prefix).strip(' -')
        add(prefix + ' ' + episode.group())
        if years:
            add(prefix[:years[-1].start()].strip(' -') + ' ' + episode.group())
    elif years:
        year = years[-1]
        add(title[:year.end()])
        add(title[:year.start()])
    else:
        add(title)
    add(normalized)
    return terms[:limit]


def search_zones(site_config, targets, zone_metadata, max_queries=3):
    """Expand enabled zones into bounded ordered attempts without changing filters."""
    if isinstance(targets, dict):
        targets = [targets]
    if not isinstance(targets, (list, tuple)):
        return []
    configured = site_config.get('target_urls') or []
    if not isinstance(configured, (list, tuple)):
        return []
    zones = []
    for zone in configured:
        if isinstance(zone, str) and zone.strip():
            zone = {'name': 'Browse', 'url': zone}
        if isinstance(zone, dict) and zone.get('enable', True) and str(zone.get('url') or '').strip():
            zones.append(zone)
    site = str(site_config.get('name') or '').strip().casefold()
    # Explicitly disabled zones must never be re-enabled by a default.
    if not configured:
        if 'bearbit' in site or 'siambit' in site:
            zones = [{'name':'โซนปกติ','url':'viewno18sbx.php'}, {'name':'โซนพิเศษ','url':'viewbrsb.php'}]
        elif 'bitsuse' in site:
            zones = [{'name':'Browse','url':'bs_browse.php'}]
        elif 'dedbit' in site:
            zones = [{'name':'Browse','url':'browse.php'}]
    result = []
    for index, target in enumerate(targets):
        if not isinstance(target, dict):
            continue
        for zone_index, zone in enumerate(zones):
            zone_params = zone.get('search_params') or {}
            target_params = target.get('search_params') or {}
            if not isinstance(zone_params, dict) or not isinstance(target_params, dict):
                continue
            controls = parse_qs(urlsplit(zone['url']).query)
            url_filters = {key: controls[key][-1] for key in ('incldead', 'searchin', 'sortby') if key in controls}
            cats = [key[1:] for key, values in controls.items() if re.fullmatch(r'c\d+', key) and values[-1] == '1']
            if site == 'torrentdd' and 'cate' in controls:
                cats = controls['cate'][-1].split(',')
            if site == 'unlimitz':
                if 'cat' in controls and controls['cat'][-1] != '0':
                    cats = [controls['cat'][-1]]
                if 'blah' in controls:
                    url_filters['searchin'] = controls['blah'][-1]
            if cats:
                url_filters['selected_cats'] = cats
            if 'freeload' in controls:
                url_filters['freeload'] = controls['freeload'][-1] == ('yes' if site == 'torrentdd' else '1')
            params = {**url_filters, **zone_params, **target_params}
            raw_keyword = params.get('search_keyword')
            if not str(raw_keyword or '').strip():
                raw_keyword = target.get('keyword')
            keywords = search_keywords(raw_keyword, max_queries)
            group = (str(target.get('source_hash') or index).upper(), zone_index)
            for attempt, keyword in enumerate(keywords, 1):
                query = {**params, 'search_keyword':keyword, 'target_path':zone['url']}
                result.append({'name':f"Cross-Seed [{keyword}] — {zone.get('name', 'Zone')} ({attempt}/{len(keywords)})",
                               'search_params':query, 'cross_seed_meta':target,
                               'search_group':group, 'search_attempt':attempt,
                               'target_zone_meta':zone_metadata(site_config, zone)})
    return result
