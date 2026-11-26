"""Shared trust boundary for OnlyOneSeedbox hostnames."""

ONLYONE_HOST = 'onlyoneseedbox.com'


def is_onlyone_host(host):
    """Accept the central hub and its per-node subdomains, case-insensitively."""
    host = str(host or '').strip().lower()
    return host == ONLYONE_HOST or host.endswith('.' + ONLYONE_HOST)
