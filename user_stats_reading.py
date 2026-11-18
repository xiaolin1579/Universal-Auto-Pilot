"""Read account totals from profile rows, keeping their original precision."""
import re

SIZE = re.compile(r'(?<![\d.])([\d,]+(?:\.\d+)?)\s*(Pi?B|Ti?B|Gi?B|Mi?B|Ki?B|Ei?B|B)\b', re.I)


def profile_transfer(soup, field):
    label = r'^\s*(?:Uploaded|Upload)\s*:?\s*$' if field == 'up' else r'^\s*(?:Downloaded|Download)\s*:?\s*$'
    for text in soup.find_all(string=re.compile(label, re.I)):
        row = text.find_parent('tr')
        if row is None: continue
        cells = row.find_all(['td', 'th'], recursive=False)
        if len(cells) != 2 or text.find_parent(['td', 'th']) is not cells[0]: continue
        match = SIZE.search(cells[1].get_text(' ', strip=True))
        if match is None: continue
        number, unit = match.groups(); unit = unit.upper()
        decimals = len(number.split('.', 1)[1]) if '.' in number else 0
        return {'value': number + ' ' + unit, 'precision': unit + ':' + str(decimals), 'source': 'profile_row'}
    return None


def copy_reading_metadata(target, source, field):
    for suffix in ('precision', 'display', 'source'):
        key = field + '_' + suffix
        if key in source: target[key] = source[key]
        else: target.pop(key, None)


def counter_delta(before, after, field):
    if before.get(field + '_precision') != after.get(field + '_precision'): return None
    return after[field] - before[field]


def counter_baseline(snapshots, field):
    precision = snapshots[-1].get(field + '_precision')
    return next(row for row in snapshots if row.get(field + '_precision') == precision)
