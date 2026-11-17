"""Read verified source metainfo, including jobs moved from another checkout."""
from pathlib import Path

from auto_upload import UploadBlocked
from torrent_metainfo import _extract_torrent_info_hash, payload_signature, verify_completed

MAX_METAINFO_BYTES = 16 * 1024**2


def verified_source(job, client):
    if client.name != job['source_node']:
        raise UploadBlocked('Source client differs from the queued job')
    try:
        path = Path(job['source_artifact'])
        if path.stat().st_size > MAX_METAINFO_BYTES:
            raise UploadBlocked('Source metainfo exceeds the recovery size limit')
        content = path.read_bytes()
    except (OSError, KeyError):
        exporter = getattr(client, 'export_torrent', None)
        if not callable(exporter):
            raise UploadBlocked('Source artifact unavailable; this client does not support verified export') from None
        content = exporter(job['source_hash'])
    if (not isinstance(content, bytes) or len(content) > MAX_METAINFO_BYTES
            or _extract_torrent_info_hash(content) != job['source_hash']
            or payload_signature(content) != job['content_group_id']):
        raise UploadBlocked('Source metainfo differs from the queued job')
    live = client.inventory(job['source_hash'])
    if live.get('save_path') != job['save_path']:
        raise UploadBlocked('Source save path changed')
    verify_completed(content, live, client.files(job['source_hash']))
    return content
