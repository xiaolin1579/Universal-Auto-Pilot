"""One account-statistics series for Dedbit/Bitsuse; counters are never summed."""
from copy import deepcopy
import json
import math
import re

SHARED_SITE = 'DED/BITS'
ALIASES = ('BITSUSE', 'DEDBIT', SHARED_SITE)


def account_identity(username):
    value = str(username or '').strip().casefold()
    return value if value not in ('', 'n/a', 'unknown') else ''


def shared_account_key(site, username):
    if str(site).upper() in ALIASES and account_identity(username):
        return SHARED_SITE, account_identity(username)
    return None


def _rows(value, history):
    if not isinstance(value, dict): return []
    return list(value.values()) if history else [value]


def _counter(value):
    try:
        if isinstance(value, str):
            match = re.fullmatch(r'\s*([\d.,]+)\s*([KMGTPE]?)I?B?\s*', value, re.I)
            if not match: return 0
            number = float(match[1].replace(',', ''))
            if match[2]: number *= 1024 ** ('KMGTPE'.index(match[2].upper()) + 1 - 3)
            elif value.strip().upper().endswith('B'): number /= 1024 ** 3
        else:
            number = float(value)
        return number if math.isfinite(number) else 0
    except (TypeError, ValueError):
        return 0


def coalesce_stats(data, *, history=False):
    """Merge only a single identified account; keep conflicting accounts separate."""
    result = deepcopy(data)
    records = [row for key in ALIASES for row in _rows(data.get(key), history)]
    if not records or any(not isinstance(row, dict) for row in records): return result
    users = {account_identity(row.get('username')) for row in records}
    if len(users) != 1 or '' in users: return result
    if history:
        merged = {}
        for key in ALIASES:
            for hour, row in data.get(key, {}).items():
                old = merged.get(hour)
                rank = lambda item: (str(item.get('raw_time') or hour), _counter(item.get('up', 0)), _counter(item.get('dl', 0)))
                if old is None or rank(row) >= rank(old): merged[hour] = deepcopy(row)
    else:
        merged = deepcopy(max(records, key=lambda row: (_counter(row.get('up', 0)), _counter(row.get('dl', 0)))))
    for key in ALIASES: result.pop(key, None)
    result[SHARED_SITE] = merged
    return result


def storage_key(site, data, *, username=None, history=False):
    key = str(site).upper()
    if key not in ALIASES: return site
    if SHARED_SITE in data:
        users = {account_identity(row.get('username')) for row in _rows(data[SHARED_SITE], history) if isinstance(row, dict)}
        if username is not None and users and account_identity(username) not in users:
            return key  # Do not append a different account to the shared series.
        return SHARED_SITE
    return key if any(alias in data for alias in ALIASES) else SHARED_SITE


def load_history(path):
    with open(path, encoding='utf-8') as handle:
        return coalesce_stats(json.load(handle), history=True)
