"""Listing titles and metainfo basenames; payload names and identity stay intact."""
import re


class TitleError(ValueError):
    pass


LIMITS = {'DEDBIT': 80, 'TORRENTDD': 200}
YEAR = re.compile(r'(?<!\d)((?:19|20)\d{2})(?!\d)')
EPISODE = re.compile(r'\bS\d{1,2}(?:E\d{1,3}(?:[-+]E?\d{1,3})*|[-+]S?\d{1,2})?\b', re.I)
TECH = re.compile(r'\b(?:\d{3,4}p|[248]K|WEB[- .]?DL|WEB[- .]?Rip|Blu[- .]?Ray|BDRip|REMUX|DVD(?:Rip)?|[Hx][ .-]?26[45]|HEVC|AVC|HDR10\+?|HDR|DV|\d{2,3}(?:\.\d+)?fps)\b', re.I)


def units(value):
    return len(value.encode('utf-16-le')) // 2


def _root(value):
    return re.sub(r'\.(?:torrent|mkv|mp4|avi|wmv|mov|iso|ts|m2ts)$', '', str(value or ''), flags=re.I)


def _clean(value, year=''):
    value = re.sub(r'^(?:\[[^\]]+\]\s*[- ]*)+', '', value.strip())
    value = re.sub(r'\[[^\]]*\]', ' ', value)
    cut = re.search(r'\s*ตอน(?:ที่)?\s*\d+|\bS\d{1,2}(?:E\d+)?\b', value, re.I)
    if cut:
        value = value[:cut.start()]
    technical = TECH.search(value)
    if technical:
        value = value[:technical.start()]
    value = re.sub(r'\((?:19|20)\d{2}\)', '', value)
    if year:
        value = re.sub(r'\s+' + re.escape(year) + r'\s*$', '', value)
    return re.sub(r'\s+', ' ', value).strip(' ._-*')


def _quality(text):
    def first(pattern, flags=re.I):
        match = re.search(pattern, text, flags)
        return match.group(1) if match else ''
    resolution = first(r'\b(\d{3,4}p|[248]K)\b')
    resolution = {'2160p': '4K', '4320p': '8K'}.get(resolution.lower(), resolution)
    if resolution.lower().endswith('k'):
        resolution = resolution.upper()
    source = first(r'\b(WEB[- .]?DL|WEB[- .]?Rip|Blu[- .]?Ray|BDRip|REMUX|DVDRip|DVD)\b')
    key = re.sub(r'[- .]', '', source).upper()
    source = {'WEBDL':'WEB-DL','WEBRIP':'WEBRip','BLURAY':'BluRay','BDRIP':'BDRip',
              'REMUX':'REMUX','DVDRIP':'DVDRip','DVD':'DVD'}.get(key, '')
    codec = first(r'\b([Hx][ .-]?26[45]|HEVC|AVC)\b')
    codec = ('H.265' if re.search(r'265|HEVC', codec, re.I) else 'H.264') if codec else ''
    hdr = first(r'\b(HDR10|HDR)\b')
    # '+' has no word boundary; prefer the complete token when present.
    if re.search(r'\bHDR10\+', text, re.I): hdr = 'HDR10+'
    flags = [hdr.upper()] if hdr else []
    if re.search(r'\bDV\b|Dolby[ .-]?Vision', text, re.I): flags.append('DV')
    fps = first(r'\b(\d{2,3}(?:\.\d+)?fps)\b').lower()
    return resolution, source, codec, ' '.join(flags), fps


def format_title(metadata, site, override=None):
    limit = LIMITS.get(site)
    if override is not None:
        if (not isinstance(override, str) or not override.strip()
                or re.search(r'[\x00-\x1f\x7f]', override)):
            raise TitleError('Display title must be nonempty text without control characters')
        override = override.strip()
        if limit and units(override) > limit:
            raise TitleError('Display title exceeds destination limit')
        return override
    original = str(metadata.get('title') or '').strip()
    root = _root(metadata.get('root_name'))
    root_text = root.replace('_', ' ')
    episode_match = EPISODE.search(root_text) or EPISODE.search(original)
    episode = episode_match.group().upper() if episode_match else ''
    if not episode:
        match = re.search(r'ตอน(?:ที่)?\s*(\d+(?:\s*[-–/]\s*\d+)*)', original)
        if match: episode = 'ตอน ' + re.sub(r'\s+', '', match.group(1))
    display_years = re.findall(r'\(((?:19|20)\d{2})\)', original)
    release_code = re.match(r'^[A-Za-z]{2,12}-\d{2,6}(?:-\d+)?', root_text)
    root_years = [m.group(1) for m in YEAR.finditer(root_text)
                  if (not release_code or m.start() >= release_code.end())
                  and root_text[:m.start()].strip(' ._-') and
                  (TECH.search(root_text[m.end():]) or EPISODE.search(root_text[m.end():])
                   or root_text[max(0,m.start()-1):m.start()] in ('.', '(')
                   or re.match(r'^[A-Za-z]{2,12}-\d+', root_text))]
    year = display_years[-1] if display_years else (root_years[-1] if root_years else '')
    if display_years and root_years and year != root_years[-1]:
        raise TitleError('Source years disagree; set an explicit display title')
    clean = _clean(original, year)
    root_title = root
    root_year_match = next((m for m in YEAR.finditer(root_text) if m.group(1) == year
                           and (not release_code or m.start() >= release_code.end())), None)
    boundaries = [m for m in (root_year_match, EPISODE.search(root_text), TECH.search(root_text)) if m]
    boundary = min(boundaries, key=lambda m:m.start()) if boundaries else None
    if boundary: root_title = root[:boundary.start()]
    root_title = re.sub(r'[._]+', ' ', root_title).strip(' -')
    if not clean: clean = root_title
    names = re.split(r'\s+-\s+', clean, maxsplit=1)
    primary = names[0].strip() if names else ''
    secondary = names[1].strip() if len(names) > 1 else ''
    if not primary: raise TitleError('Source title is empty')
    # Keep release identifiers such as ABC-123 intact, including their suffixes.
    quality = list(_quality(root_text + ' ' + original))
    root_resolution, display_resolution = _quality(root_text)[0], _quality(original)[0]
    if root_resolution and display_resolution and root_resolution != display_resolution:
        raise TitleError('Source resolutions disagree; set an explicit display title')
    def present(pattern):
        return any(not re.search(r'(?:ไม่มี|ไม่|no)\s*$', original[:m.start()], re.I)
                   for m in re.finditer(pattern, original, re.I))
    audio = []
    if present(r'พากย์ไทย|เสียงไทย|Thai[ .-]?Dub'): audio.append('พากย์ไทย')
    if present(r'Soundtrack|เสียงต้นฉบับ'): audio.append('เสียงต้นฉบับ')
    subtitle = present(r'บรรยายไทย|ซับไทย|Thai[ .-]?Sub')
    language = '/'.join(audio + (['ซับไทย'] if subtitle else []))
    if site == 'DEDBIT':
        secondary = ''
        quality[4] = quality[2] = ''
        if subtitle and audio == ['เสียงต้นฉบับ']:
            language = 'ซับไทย'

    def render(name, alias, details, lang):
        base = name + (' (' + year + ')' if year else '') + (' ' + episode if episode else '')
        if alias: base += ' - ' + alias
        tags = ' '.join(x for x in details if x)
        return base + (' [' + tags + ']' if tags else '') + (' [' + lang + ']' if lang else '')

    # Remove whole optional details, never truncate the identity mid-word.
    candidates = [render(primary, secondary, quality, language)]
    for index in (4, 2):  # FPS, then codec.
        quality[index] = ''
        candidates.append(render(primary, secondary, quality, language))
    candidates.append(render(primary, '', quality, language))
    if subtitle and audio == ['เสียงต้นฉบับ']:
        language = 'ซับไทย'
        candidates.append(render(primary, '', quality, language))
    for index in (1, 3):  # Source label and HDR are less important than episode/audio.
        quality[index] = ''
        candidates.append(render(primary, '', quality, language))
    if root_title and root_title != primary:
        candidates.append(render(root_title, '', quality, language))
    for candidate in candidates:
        if not limit or units(candidate) <= limit:
            return candidate
    raise TitleError('Source title needs an explicit shorter display title for this website')


def torrent_filename(metadata, site):
    """Readable metainfo basename; callers isolate jobs and stages in directories."""
    if site not in {'DEDBIT', 'BITSUSE', 'TORRENTDD'}:
        raise TitleError('Unknown destination for torrent filename')
    display = format_title(metadata, 'ARTIFACT')
    identity = display.split(' [', 1)[0].split(' - ', 1)[0]
    identity = re.sub(r'\(((?:19|20)\d{2})\)', r'\1', identity)
    # A source's "episode 10/40" is not the range 10-40.
    identity = re.sub(r'ตอน\s*(\d+)/(\d+)', r'ตอน\1จาก\2', identity)
    identity = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '.', identity)
    identity = re.sub(r'[\s.]+', '.', identity).strip(' .')
    if not identity:
        raise TitleError('Torrent filename has no usable identity')
    if re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]', identity.split('.')[0], re.I):
        identity = '_' + identity
    resolution = _quality(str(metadata.get('root_name') or '') + ' ' + str(metadata.get('title') or ''))[0]
    name = '.'.join(part for part in (identity, resolution, site, 'torrent') if part)
    if len(name.encode('utf-8')) > 220 or units(name) > 180:
        raise TitleError('Torrent filename is too long; use a shorter source title')
    return name
