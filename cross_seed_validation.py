"""Shared path conventions and preflight evidence for Cross-Seed stages."""
import re


def relative_path(value):
    path = str(value or '').replace('\\', '/')
    if not path or path.startswith('/') or '\0' in path or re.match(r'^[A-Za-z]:', path):
        raise ValueError('Invalid relative file path')
    parts = [part for part in path.split('/') if part not in ('', '.')]
    if not parts or any(part == '..' for part in parts):
        raise ValueError('Unsafe relative file path')
    return '/'.join(parts)


def source_file_pairs(files, root_name, multi_file):
    """qBittorrent includes the multi-file root; torrent files are root-relative.

    Strip exactly one common, verified root prefix. Never match by basename,
    case folding, Unicode rewriting, or a guessed directory.
    """
    if not isinstance(files, list) or not files:
        raise ValueError('Source file manifest unavailable')
    pairs = []
    for row in files:
        if not isinstance(row, dict) or type(row.get('size_bytes')) is not int or row['size_bytes'] < 0:
            raise ValueError('Invalid source file entry')
        pairs.append((relative_path(row.get('path')), row['size_bytes']))
    if len({p for p, _ in pairs}) != len(pairs):
        raise ValueError('Duplicate source paths')
    if multi_file:
        root = relative_path(root_name)
        if '/' in root:
            raise ValueError('Invalid source root name')
        prefix = root + '/'
        if all(p.startswith(prefix) for p, _ in pairs):
            pairs = [(p[len(prefix):], size) for p, size in pairs]
    return pairs


def preflight_evidence(result):
    """Preserve proof and pending-content state when collecting search matches."""
    keys = ('candidate_info_hash','candidate_trackers','candidate_manifest','torrent_content',
            'source_file_mapping','candidate_extra_files','content_verification',
            'source_total_size_bytes','candidate_total_size_bytes')
    return {**{key:result.get(key) for key in keys}, 'preflight_status':result.get('status'),
            'preflight_action':result.get('action'), 'candidate_filelist':result.get('filelist', {})}
