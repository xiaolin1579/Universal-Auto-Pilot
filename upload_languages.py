"""Extract declared track languages from structured MediaInfo text only."""
import re
import unicodedata

LANGUAGES = {
    'thai': 'ไทย', 'tha': 'ไทย', 'ไทย': 'ไทย',
    'english': 'อังกฤษ', 'eng': 'อังกฤษ', 'อังกฤษ': 'อังกฤษ',
    'japanese': 'ญี่ปุ่น', 'jpn': 'ญี่ปุ่น', 'ญี่ปุ่น': 'ญี่ปุ่น',
    'korean': 'เกาหลี', 'kor': 'เกาหลี', 'เกาหลี': 'เกาหลี',
    'chinese': 'จีน', 'zho': 'จีน', 'chi': 'จีน', 'จีน': 'จีน',
    'mandarin': 'จีนกลาง', 'cantonese': 'กวางตุ้ง',
    'french': 'ฝรั่งเศส', 'fra': 'ฝรั่งเศส', 'fre': 'ฝรั่งเศส',
    'german': 'เยอรมัน', 'deu': 'เยอรมัน', 'ger': 'เยอรมัน',
    'spanish': 'สเปน', 'spa': 'สเปน', 'italian': 'อิตาลี', 'ita': 'อิตาลี',
    'russian': 'รัสเซีย', 'rus': 'รัสเซีย', 'hindi': 'ฮินดี', 'hin': 'ฮินดี',
}
FIELDS = {'Audio': 'c3dub', 'Text': 'c3sub'}


def language_value(value):
    return isinstance(value, str) and bool(value) and all(
        part in set(LANGUAGES.values()) for part in value.split(' / '))


def track_languages(description):
    text = unicodedata.normalize('NFKC', str(description or ''))[:200000]
    # Formatting tags may wrap a header, but arbitrary HTML is never interpreted.
    text = re.sub(r'\[/?(?:b|i|u|code|quote|pre)\]', '', text, flags=re.I)
    sections = re.split(r'(?m)^\s*(General|Video|Audio|Text|Menu|Image)(?:\s*#\d+)?\s*\r?$', text)
    found = {field: set() for field in FIELDS.values()}
    incomplete = set()
    for kind, body in zip(sections[1::2], sections[2::2]):
        if kind not in FIELDS:
            continue
        field = FIELDS[kind]
        declarations = re.findall(r'(?mi)^\s*Language\s*:\s*([^\r\n]+)', body)
        mapped = [LANGUAGES.get(value.strip().casefold()) for value in declarations]
        # A missing/unknown language in any track prevents claiming a full list.
        structured = all(re.search(r'(?mi)^\s*' + name + r'\s*:', body) for name in ('ID', 'Format'))
        if not structured or len(mapped) != 1 or mapped[0] is None:
            incomplete.add(field)
        else:
            found[field].add(mapped[0])
    return {field: ' / '.join(sorted(values)) for field, values in found.items()
            if values and field not in incomplete}
