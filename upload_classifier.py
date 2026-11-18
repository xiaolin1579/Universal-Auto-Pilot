"""Evidence-based category suggestions. Pure analysis: never edits a job or publishes.

Schema options are constraints, not evidence. String similarity repairs spelling
only; identity, origin, language and censorship are evaluated independently.
"""
from collections import Counter
import hashlib
import json
from pathlib import PurePosixPath
import re
import unicodedata
from urllib.parse import urlsplit, parse_qsl
import ipaddress

AUDIO = {'.mp3': 'MP3', '.flac': 'Lossless', '.ape': 'Lossless', '.wav': 'Lossless',
         '.aiff': 'Lossless', '.aif': 'Lossless', '.wv': 'Lossless', '.dsf': 'Lossless',
         '.dff': 'Lossless', '.aac': 'AAC', '.m4a': 'AAC', '.wma': 'WMA'}
VIDEO = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.m2ts', '.ts', '.vob'}
PICTURE = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
TECH_CODES = {'SACD', 'DSD', 'DSF', 'DFF', 'FLAC', 'MP', 'ISO', 'AVC', 'HEVC', 'HDR', 'VOL', 'PART'}
LEVEL = {'low': 0, 'medium': 1, 'high': 2}


def clean_text(value, limit=500):
    text = re.sub(r'https?://\S+', '[URL]', str(value or ''), flags=re.I)
    text = re.sub(r'''(?ix)\b(passkey|token|password|cookie|authorization|csrf|secret|sid)\s*[:=]\s*(?:"[^"]*"|'[^']*'|\S+)''',
                  r'\1=[REDACTED]', text)
    return re.sub(r'[\x00-\x1f\x7f]+', ' ', text).strip()[:limit]


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def origin_requires_review(job, identity=None):
    identity = identity or extract_identity(job)
    metadata = job.get('metadata') or {}
    text = clean_text(str(metadata.get('title', '')) + '\n' + str(metadata.get('description', '')), 30000)
    return identity['kind'] == 'adult' and bool(re.search(r'(?i)\bleaked\b|แอบถ่าย|คลิปหลุด', text))


def public_source(value):
    try:
        p = urlsplit(str(value or ''))
        if (p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment
                or re.search(r'(?i)passkey|token|auth|csrf|secret|password|sid=', p.query)):
            return None
        if any(c.isspace() for c in str(value)) or p.hostname in ('localhost','localhost.localdomain') or p.hostname.endswith('.local'):
            return None
        try:
            if not ipaddress.ip_address(p.hostname).is_global: return None
        except ValueError:
            pass
        if any(k.casefold() not in ('id','rid','mbid','page','lang','language') for k,v in parse_qsl(p.query)):
            return None
        return p.geturl()
    except ValueError:
        return None


def release_codes(value):
    return sorted({m[0].upper() + '-' + m[1] for m in re.findall(r'\b([A-Za-z]{2,10})[-_](\d{2,7})\b', value)
                   if m[0].upper() not in TECH_CODES})


def extract_identity(job):
    metadata = job.get('metadata', {})
    original = clean_text(metadata.get('title') or metadata.get('root_name'))
    root = clean_text(metadata.get('root_name') or original)
    category = clean_text(metadata.get('category'))
    schema = job.get('form_schema', {}).get('fields', {})
    adult = job.get('source_zone') == 'special' or any(k.startswith('c100') for k in schema)
    music = any(k in schema for k in ('c4genre', 'c41type', 'c41sub')) or 'เพลง' in category
    kind = 'adult' if adult else 'music' if music else 'video'
    title = re.sub(r'^(?:\[[^\]]+\]\s*[- ]*)+', '', original)
    # Vol 1974 is a volume identifier, not evidence of a 1974 release date.
    year_text = re.sub(r'(?i)\b(?:vol(?:ume)?|part)\.?\s*\d+', '', title)
    years = set(re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)', year_text))
    year = int(next(iter(years))) if len(years) == 1 else None
    artist = None
    parts = re.split(r'\s[-–—]\s',title,maxsplit=1)
    if music and len(parts) == 2:
        artist, title = parts
        title = re.sub(r'\s*[-–]?\s*\(?(?:19|20)\d{2}\)?.*$', '', title).strip(' -')
    identity = {'kind': kind, 'title': title, 'original_title': original,
                'artist': artist, 'year': year, 'release_codes': [] if music else release_codes(root)}
    if kind == 'video':
        try:
            from guessit import guessit
            parsed = guessit(root)
            if isinstance(parsed.get('title'), str): identity['title'] = clean_text(parsed['title'])
            for key in ('type', 'season', 'episode', 'screen_size', 'video_codec'):
                if key in parsed: identity[key] = parsed[key]
        except (ImportError, ValueError):
            pass
    return identity


def input_fingerprint(job, files=None):
    data = {k: job.get(k) for k in ('source_hash', 'source_node', 'source_zone', 'target_site',
                                   'target_category', 'metadata', 'rule', 'field_overrides', 'form_schema')}
    # URLs/paths may participate in an integrity hash but are never copied to the report.
    data['files'] = [{'path': f.get('path'), 'size_bytes': f.get('size_bytes')} for f in files or []]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def spelling_match(value, options):
    """A typo suggestion, never semantic matching of two different categories."""
    from rapidfuzz.fuzz import ratio
    from rapidfuzz.distance import Levenshtein
    query = normalized(value)
    ranked = sorted(((ratio(query, normalized(o)), o) for o in options if isinstance(o, str)), reverse=True)
    if not query or not ranked: return None
    best, answer = ranked[0]
    margin = best - ranked[1][0] if len(ranked) > 1 else 100
    edits = Levenshtein.distance(query, normalized(answer))
    if best >= 85 and margin >= 12 and edits <= (2 if len(query) >= 8 else 1):
        return {'value': answer, 'similarity': round(best, 1)}
    return None


def eligible_records(identity, records):
    """Title+artist, year and medium must agree. A search ranking alone is not identity."""
    from rapidfuzz.fuzz import ratio
    matches = []
    for record in records:
        if not isinstance(record, dict) or not public_source(record.get('url')): continue
        if record.get('scope', 'release') != 'release': continue
        if record.get('kind') != identity['kind']: continue
        if identity['kind'] == 'music':
            if not identity['artist'] or not record.get('artist'): continue
            if ratio(normalized(identity['artist']), normalized(record['artist'])) < 94: continue
            if ratio(normalized(identity['title']), normalized(record.get('title'))) < 94: continue
            if identity['year'] and record.get('year') != identity['year']: continue
            declared_sacd = bool(re.search(r'(?i)\bSACD\b', identity['original_title']))
            if declared_sacd and 'sacd' not in normalized(record.get('format')): continue
        else:
            # An exact catalog code is required for catalog facts on adult titles.
            codes = release_codes(str(record.get('release_code', '')))
            if identity['kind'] == 'adult':
                if len(identity['release_codes']) != 1 or codes != identity['release_codes']: continue
            elif ratio(normalized(identity['title']), normalized(record.get('title'))) < 94:
                continue
            if identity['year'] and record.get('year') != identity['year']: continue
        matches.append(record)
    return matches


def classify_job(job, *, files=None, records=None, references=None, diagnostics=None):
    schema = job.get('form_schema', {}).get('fields', {})
    identity = extract_identity(job)
    metadata = job.get('metadata', {})
    text = clean_text(str(metadata.get('title', '')) + '\n' + str(metadata.get('description', '')), 30000)
    current = dict(job.get('rule', {}).get('fields', {}))
    current.update((job.get('field_overrides') or {}).get('fields', {}))
    flags = []
    suggestions = []
    observations = []
    candidates = {}
    if origin_requires_review(job, identity):
        flags.append('origin_requires_review')
    if not schema: flags.append('destination_schema_unavailable')
    if len(identity['release_codes']) > 1: flags.append('multiple_release_codes')

    def evidence(kind, detail, url=None):
        item = {'kind': kind, 'detail': clean_text(detail, 200)}
        if public_source(url): item['url'] = public_source(url)
        return item

    def propose(field, value, confidence, reason, proof):
        if field in schema:
            options = schema[field].get('options')
            if options is not None and value not in options: return
            candidates.setdefault(field, []).append({'field': field, 'value': value, 'confidence': confidence,
                'reasons': [reason], 'evidence': proof})

    if 'origin_requires_review' not in flags:
        if identity['kind'] == 'video':
            from upload_languages import track_languages
            for field, value in track_languages(metadata.get('description')).items():
                propose(field, value, 'high', 'mediainfo_track_languages',
                        [evidence('source_track_metadata', value)])
        for field, value in current.items():
            options = schema.get(field, {}).get('options')
            if value and isinstance(options, list) and value not in options:
                match = spelling_match(value, options)
                if match:
                    propose(field, match['value'], 'medium', 'spelling_only_not_semantic_confirmation',
                            [evidence('current_field', value)])
        if files:
            extensions = Counter(PurePosixPath(str(f.get('path', ''))).suffix.lower() for f in files)
            extensions = Counter({ext:count for ext,count in extensions.items() if re.fullmatch(r'\.[a-z0-9]{1,12}',ext)})
            observations.append({'kind': 'manifest_summary', 'files': len(files), 'extensions': dict(extensions)})
            formats = {AUDIO[ext] for ext in extensions if ext in AUDIO}
            # M4A is a container that can contain AAC or ALAC. Extension alone cannot decide.
            if '.m4a' in extensions:
                formats.discard('AAC'); flags.append('m4a_codec_requires_metadata')
            uncertain_format = ('.m4a' in extensions or ('.iso' in extensions and bool(formats))
                or (bool(formats) and any(ext in VIDEO or ext in ('.opus','.ogg','.m4b','.alac') for ext in extensions)))
            if uncertain_format: flags.append('mixed_or_unverified_audio_container')
            if len(formats) == 1 and not uncertain_format:
                confidence = 'medium' if any(ext in extensions for ext in ('.wav','.aiff','.aif')) else 'high'
                propose('c41sub', next(iter(formats)), confidence, 'manifest_audio_extensions',
                        [evidence('manifest', ', '.join(sorted(ext for ext in extensions if ext in AUDIO)))])
            elif len(formats) > 1:
                flags.append('mixed_audio_formats')
            if '.iso' in extensions and re.search(r'(?i)\bSACD\b', text) and not formats:
                propose('c41sub', 'Lossless', 'medium', 'declared_sacd_image_not_audio_decoded',
                        [evidence('source', 'SACD declaration'), evidence('manifest', '.iso')])
            if identity['kind'] == 'adult' and any(ext in VIDEO for ext in extensions):
                if not any(word in text for word in ('การ์ตูน', 'animation', 'hentai')):
                    propose('c100type', 'ภาพยนตร์', 'medium', 'video_manifest', [evidence('manifest', 'video files')])
            if identity['kind'] == 'adult' and all(PurePosixPath(str(f.get('path',''))).suffix.lower() in PICTURE for f in files):
                propose('c100type', 'รูปภาพ', 'high', 'picture_manifest', [evidence('manifest', 'image files only')])
        elif identity['kind'] == 'music':
            flags.append('manifest_unavailable')
            if re.search(r'(?i)\bMP3\b', text):
                propose('c41sub', 'MP3', 'medium', 'source_declares_mp3', [evidence('source', 'MP3')])
            if re.search(r'(?i)\b(?:FLAC|SACD|Lossless)\b', text):
                propose('c41sub', 'Lossless', 'medium', 'source_declares_lossless', [evidence('source', 'lossless/SACD')])
        if identity['kind'] == 'music':
            if re.search(r'(?i)\bdiscography\b', str(metadata.get('title', ''))):
                releases = {part for file in files or [] for part in PurePosixPath(str(file.get('path', ''))).parts[:-1]
                            if re.match(r'^(?:19|20)\d{2}\s*[-–]\s*\S', part)}
                propose('c41type', 'รวม', 'high' if len(releases) >= 2 else 'medium', 'explicit_discography',
                        [evidence('source_title', 'Discography'), evidence('manifest', str(len(releases)) + ' release directories')])
            if re.search(r'(?i)\b(?:Albums? Singles|compilation|various artists)\b|รวมเพลง|รวมอัลบั้ม', text):
                propose('c41type', 'รวม', 'high', 'explicit_compilation', [evidence('source', 'compilation declaration')])
            if re.search(r'(?i)\b(?:OST|soundtrack)\b', text):
                propose('c41type', 'OST', 'medium', 'source_declares_soundtrack', [evidence('source', 'OST/soundtrack')])
            if re.search(r'(?i)\bFull Album\b', text):
                propose('c41type', 'Full Album', 'medium', 'source_declares_album', [evidence('source', 'Full Album')])
            genre_text = clean_text(metadata.get('category'))
            for label, pattern in [('ไทยสากล', 'ไทยสากล|สตริง'), ('ไทยลูกทุ่ง', 'ลูกทุ่ง'),
                                   ('สากล', '(?<!ไทย)สากล'), ('เอเชีย', 'เอเชีย|เอเซีย')]:
                if re.search(pattern, genre_text):
                    propose('c4genre', label, 'high', 'explicit_source_category', [evidence('source_category', genre_text)])
            if not candidates.get('c4genre'):
                found = [label for label, pattern in [('ไทยสากล','สตริง|ไทยสากล'),('ไทยลูกทุ่ง','ลูกทุ่ง'),
                         ('สากล','(?<!ไทย)สากล'),('เอเชีย','เอเชีย|เอเซีย')] if re.search(pattern,text)]
                if len(found) == 1:
                    propose('c4genre', found[0], 'medium', 'source_description_category', [evidence('source', found[0])])
                elif len(found) > 1:
                    flags.append('mixed_music_regions_or_styles')
        if identity['kind'] == 'adult':
            # Origin/censorship are separate observations, never guessed from code prefixes.
            category = str(metadata.get('category', ''))
            for label in ('ญี่ปุ่น', 'ไทย', 'เกาหลี', 'จีน', 'ฝรั่ง'):
                if label in category:
                    for field in ('c100cat', 'c184cat'):
                        propose(field, label, 'high', 'explicit_source_category', [evidence('source_category', category)])
            uncensored = bool(re.search(r'(?i)\buncensored\b|ไม่เซ็น|ไม่เซนเซอร์', text))
            censored = bool(re.search(r'(?i)(?<!un)\bcensored\b|(?<!ไม่)เซนเซอร์|(?<!ไม่)เซ็น', text))
            if uncensored or censored:
                observations.append({'kind': 'censorship_claim', 'value': 'conflicting' if uncensored and censored else
                                     'uncensored' if uncensored else 'censored', 'verified': False})

    if identity['kind'] == 'music' and identity.get('artist') and 'origin_requires_review' not in flags:
        artists = [r for r in records or [] if isinstance(r, dict) and r.get('kind') == 'music'
                   and r.get('scope') == 'artist' and public_source(r.get('url'))
                   and normalized(r.get('artist')) == normalized(identity['artist'])]
        regions = {r.get('music_region') for r in artists if r.get('music_region')}
        if len(regions) > 1:
            flags.append('catalog_identity_ambiguous')
        elif len(regions) == 1:
            region = next(iter(regions))
            if region in ('ไทยสากล','ไทยลูกทุ่ง','สากล','เอเชีย','เพลงบรรเลง(ไทย)','เพลงบรรเลง(ตปท)'):
                propose('c4genre', region, 'high', 'catalog_explicit_artist_music_region',
                        [evidence('artist_catalog', r.get('provider', 'catalog'), r['url']) for r in artists])
    matched = eligible_records(identity, records or []) if 'origin_requires_review' not in flags else []
    if len(matched) > 1:
        flags.append('catalog_identity_ambiguous')
    elif len(matched) == 1:
        record = matched[0]
        proof = [evidence('catalog', str(record.get('provider') or 'catalog'), record['url'])]
        observations.append({'kind': 'catalog_match', 'provider': clean_text(record.get('provider')),
                             'url': public_source(record['url'])})
        if identity['kind'] == 'music':
            secondary = {str(v).casefold() for v in record.get('secondary_types', [])}
            primary = str(record.get('release_type', '')).casefold()
            if 'compilation' in secondary:
                propose('c41type', 'รวม', 'high', 'catalog_compilation', proof)
            elif 'soundtrack' in secondary:
                propose('c41type', 'OST', 'high', 'catalog_soundtrack', proof)
            elif primary == 'single':
                propose('c41type', 'Single', 'high', 'catalog_single', proof)
            elif primary == 'album' and 'live' not in secondary:
                propose('c41type', 'Full Album', 'high', 'catalog_album', proof)
            # A sales country or track-list language is not the artist's language/genre.
            region = record.get('music_region')
            if region in ('ไทยสากล','ไทยลูกทุ่ง','สากล','เอเชีย','เพลงบรรเลง(ไทย)','เพลงบรรเลง(ตปท)'):
                propose('c4genre', region, 'high', 'catalog_explicit_music_region', proof)
        elif identity['kind'] == 'adult':
            countries = {'jp':'ญี่ปุ่น','japan':'ญี่ปุ่น','th':'ไทย','thailand':'ไทย','kr':'เกาหลี','korea':'เกาหลี','cn':'จีน','china':'จีน'}
            media = {'movie':'ภาพยนตร์','video':'ภาพยนตร์','animation':'การ์ตูน','images':'รูปภาพ','game':'เกมส์'}
            for source, field, aliases in (('production_country','c100cat',countries),('media_type','c100type',media)):
                value = record.get(source)
                if isinstance(value,str): propose(field,aliases.get(normalized(value),value),'high','catalog_exact_code',proof)
            country = record.get('production_country')
            if isinstance(country, str):
                propose('c184cat', countries.get(normalized(country), country), 'high', 'catalog_exact_code', proof)
            if record.get('censorship') in ('censored','uncensored'):
                observations.append({'kind':'censorship','value':record['censorship'],'verified':True,'evidence':proof})

    conflicts = []
    for field, values in candidates.items():
        distinct = {v['value'] for v in values}
        if len(distinct) > 1:
            conflicts.append(field)
            continue
        chosen = max(values, key=lambda v:LEVEL[v['confidence']])
        chosen['current'] = clean_text(current.get(field, ''))
        chosen['needs_change'] = current.get(field) != chosen['value']
        chosen['reasons'] = sorted({r for v in values for r in v['reasons']})
        chosen['evidence'] = [e for v in values for e in v['evidence']]
        suggestions.append(chosen)
    unresolved = []
    for field, spec in schema.items():
        if spec.get('required',True):
            options = spec.get('options')
            value = current.get(field)
            valid = isinstance(value,str) and bool(value.strip()) and (options is None or value in options)
            if not valid and not any(s['field']==field for s in suggestions): unresolved.append(field)
    censorship_claims = {o['value'] for o in observations if o['kind']=='censorship_claim'}
    confirmed_censorship = {o['value'] for o in observations if o['kind']=='censorship' and o.get('verified')}
    if censorship_claims and not confirmed_censorship: flags.append('censorship_requires_edition_evidence')
    if censorship_claims and confirmed_censorship and censorship_claims != confirmed_censorship:
        flags.append('censorship_claim_conflict')
        for observation in observations:
            if observation['kind']=='censorship': observation['verified']=False
    if conflicts: flags.append('conflicting_evidence')
    if diagnostics: flags.extend(diagnostics)
    status = 'review_required' if flags or conflicts or unresolved else 'suggested'
    return {'version':1, 'job_id':job.get('id'), 'source_hash':job.get('source_hash'),
            'status':status, 'job_fingerprint':input_fingerprint(job), 'input_fingerprint':input_fingerprint(job,files),
            'identity':identity, 'suggestions':suggestions, 'unresolved_fields':sorted(unresolved),
            'conflict_fields':sorted(conflicts), 'review_flags':sorted(set(flags)),
            'observations':observations, 'references':[{'title':clean_text(r.get('title'),200),'url':public_source(r.get('url'))}
                for r in references or [] if isinstance(r,dict) and public_source(r.get('url'))],
            'schema_source':'stored_form_schema', 'requires_live_form_check':True,
            'applied':False}
