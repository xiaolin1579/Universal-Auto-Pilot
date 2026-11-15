"""Byte-preserving v1 metainfo helpers shared by search and upload."""
import hashlib

def _bencode_value_end(data, pos):
    """คืนตำแหน่ง byte ถัดจากค่า bencode หนึ่งค่า"""
    if pos >= len(data):
        raise ValueError("Unexpected end of bencode")

    token = data[pos]

    if token == ord("i"):
        end = data.find(b"e", pos + 1)
        if end < 0:
            raise ValueError("Unterminated bencode integer")
        return end + 1

    if token in (ord("l"), ord("d")):
        is_dict = token == ord("d")
        pos += 1

        while True:
            if pos >= len(data):
                raise ValueError("Unterminated bencode container")
            if data[pos] == ord("e"):
                return pos + 1

            pos = _bencode_value_end(data, pos)
            if is_dict:
                pos = _bencode_value_end(data, pos)

    if ord("0") <= token <= ord("9"):
        colon = data.find(b":", pos)
        if colon < 0:
            raise ValueError("Invalid bencode byte string")
        length_text = data[pos:colon]
        if not length_text or not length_text.isdigit():
            raise ValueError("Invalid byte string length")
        end = colon + 1 + int(length_text)
        if end > len(data):
            raise ValueError("Truncated bencode byte string")
        return end

    raise ValueError(f"Invalid bencode token at offset {pos}")



def _decode_bencode_value(data, offset=0):
    """Decode one bencode value, returning (value, next_offset)."""
    if offset >= len(data):
        raise ValueError("Unexpected end of bencode")

    token = data[offset]

    if token == ord("i"):
        end = data.find(b"e", offset + 1)
        if end < 0:
            raise ValueError("Unterminated integer")
        try:
            return int(data[offset + 1:end]), end + 1
        except ValueError as exc:
            raise ValueError("Invalid bencode integer") from exc

    if token == ord("l"):
        items = []
        pos = offset + 1
        while True:
            if pos >= len(data):
                raise ValueError("Unterminated list")
            if data[pos] == ord("e"):
                return items, pos + 1
            value, pos = _decode_bencode_value(data, pos)
            items.append(value)

    if token == ord("d"):
        result = {}
        pos = offset + 1
        while True:
            if pos >= len(data):
                raise ValueError("Unterminated dictionary")
            if data[pos] == ord("e"):
                return result, pos + 1

            key, pos = _decode_bencode_value(data, pos)
            if not isinstance(key, bytes):
                raise ValueError("Bencode dictionary key must be bytes")
            if key in result:
                raise ValueError("Duplicate Bencode dictionary key")

            value, pos = _decode_bencode_value(data, pos)
            result[key] = value

    if ord("0") <= token <= ord("9"):
        colon = data.find(b":", offset)
        if colon < 0:
            raise ValueError("Invalid byte-string length")
        raw_length = data[offset:colon]
        if not raw_length.isdigit():
            raise ValueError("Invalid byte-string length")

        end = colon + 1 + int(raw_length)
        if end > len(data):
            raise ValueError("Truncated byte string")
        return data[colon + 1:end], end

    raise ValueError(f"Invalid bencode token at offset {offset}")


def _torrent_text(value):
    if not isinstance(value, bytes):
        raise ValueError("Expected a bencode byte string")
    return value.decode("utf-8", errors="surrogateescape")


def _extract_torrent_manifest(content):
    """Extract v1 info hash, root name and per-file path/size."""
    data = bytes(content)
    decoded, end = _decode_bencode_value(data, 0)

    if end != len(data):
        raise ValueError("Unexpected trailing data after torrent metainfo")
    if not isinstance(decoded, dict):
        raise ValueError("Torrent metainfo is not a dictionary")

    info = decoded.get(b"info")
    if not isinstance(info, dict):
        raise ValueError("Missing info dictionary")

    name_raw = info.get(b"name.utf-8", info.get(b"name"))
    root_name = _torrent_text(name_raw)

    if (
        not root_name
        or root_name in {".", ".."}
        or "\x00" in root_name
        or "/" in root_name
        or "\\" in root_name
    ):
        raise ValueError("Invalid torrent root name")

    files = []

    if b"files" in info:
        entries = info.get(b"files")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Invalid multi-file manifest")

        seen_paths = set()

        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid file entry")

            path_parts = entry.get(
                b"path.utf-8",
                entry.get(b"path"),
            )
            size = entry.get(b"length")

            if not isinstance(path_parts, list) or not path_parts:
                raise ValueError("Missing file path")
            if not isinstance(size, int) or size < 0:
                raise ValueError("Invalid file size")

            parts = [_torrent_text(part) for part in path_parts]

            if any(
                not part
                or part in {".", ".."}
                or "\x00" in part
                or "/" in part
                or "\\" in part
                for part in parts
            ):
                raise ValueError("Unsafe file path component")

            relative_path = "/".join(parts)
            if relative_path in seen_paths:
                raise ValueError("Duplicate file path in torrent")
            seen_paths.add(relative_path)

            file = {"path": relative_path, "size_bytes": size}
            attr = entry.get(b"attr", b"")
            if not isinstance(attr, bytes):
                raise ValueError("Invalid file attributes")
            if b"p" in attr:
                file["is_padding"] = True
            files.append(file)

        multi_file = True

    else:
        size = info.get(b"length")
        if not isinstance(size, int) or size < 0:
            raise ValueError("Invalid single-file length")

        files.append({
            "path": root_name,
            "size_bytes": size,
        })
        multi_file = False

    tracker_urls = set()

    announce = decoded.get(b"announce")
    if isinstance(announce, bytes):
        tracker_urls.add(
            announce.decode("utf-8", errors="surrogateescape").strip()
        )

    announce_list = decoded.get(b"announce-list", [])
    if isinstance(announce_list, list):
        for group in announce_list:
            entries = group if isinstance(group, list) else [group]
            for entry in entries:
                if isinstance(entry, bytes):
                    tracker_urls.add(
                        entry.decode(
                            "utf-8", errors="surrogateescape"
                        ).strip()
                    )

    tracker_urls = sorted(
        url for url in tracker_urls
        if url.startswith(("http://", "https://", "udp://"))
    )

    return {
        "info_hash": _extract_torrent_info_hash(data),
        "root_name": root_name,
        "multi_file": multi_file,
        "files": files,
        "total_size_bytes": sum(f["size_bytes"] for f in files),
        "announce_urls": tracker_urls,
    }

def _extract_torrent_info_hash(content):
    """SHA-1 of the exact raw bencoded info dictionary."""
    data = bytes(content)
    decoded, total_end = _decode_bencode_value(data, 0)

    if total_end != len(data):
        raise ValueError("Unexpected trailing bytes in torrent metainfo")
    if not isinstance(decoded, dict):
        raise ValueError("Torrent metainfo is not a dictionary")
    if not isinstance(decoded.get(b"info"), dict):
        raise ValueError("Torrent has no valid info dictionary")

    pos = 1
    while pos < len(data):
        if data[pos] == ord("e"):
            break

        key, value_start = _decode_bencode_value(data, pos)
        if not isinstance(key, bytes):
            raise ValueError("Top-level Bencode key is not bytes")

        _, value_end = _decode_bencode_value(data, value_start)

        if key == b"info":
            return hashlib.sha1(data[value_start:value_end]).hexdigest().lower()

        pos = value_end

    raise ValueError("Torrent has no raw info dictionary")


def bencode(value):
    if type(value) is int:
        return b'i' + str(value).encode() + b'e'
    if isinstance(value, bytes):
        return str(len(value)).encode() + b':' + value
    if isinstance(value, list):
        return b'l' + b''.join(bencode(v) for v in value) + b'e'
    if isinstance(value, dict) and all(isinstance(k, bytes) for k in value):
        return b'd' + b''.join(bencode(k) + bencode(value[k]) for k in sorted(value)) + b'e'
    raise ValueError('Unsupported bencode value')


def payload_signature(content):
    """Identity of exact v1 layout and pieces; excludes only private/source fields."""
    manifest = _extract_torrent_manifest(content)
    root, _ = _decode_bencode_value(bytes(content))
    info = root[b'info']
    if b'meta version' in info or b'file tree' in info:
        raise ValueError('Auto-upload currently supports v1 torrents only')
    pieces, length = info.get(b'pieces'), info.get(b'piece length')
    if type(length) is not int or length <= 0 or not isinstance(pieces, bytes):
        raise ValueError('Missing v1 piece hashes')
    count = (manifest['total_size_bytes'] + length - 1) // length
    if manifest['total_size_bytes'] <= 0 or len(pieces) != count * 20:
        raise ValueError('Piece count does not match file sizes')
    for entry in info.get(b'files', []):
        attr = entry.get(b'attr', b'')
        if not isinstance(attr, bytes) or b'l' in attr or b'symlink path' in entry:
            raise ValueError('Symlinks need a dedicated client adapter')
    layout = {k: v for k, v in info.items() if k not in (b'source', b'private')}
    return hashlib.sha256(bencode(layout)).hexdigest()


UPLOAD_TARGETS = {'normal': frozenset({'DEDBIT', 'TORRENTDD'}),
                  'special': frozenset({'BITSUSE', 'TORRENTDD'})}
UPLOAD_ANNOUNCE_HOSTS = {'DEDBIT': frozenset({'tracker.cab'}),
                         'BITSUSE': frozenset({'bitsuse.com', 'www.bitsuse.com'}),
                         'TORRENTDD': frozenset({'sv-seed.com'})}


def upload_route_supported(site, zone, category=None):
    if site not in UPLOAD_TARGETS.get(zone, ()):
        return False
    if site == 'TORRENTDD':
        # Confirmed on the authenticated upload form; IDs are site-specific.
        allowed = set(range(34, 39)) if zone == 'special' else set(range(1, 34)) | {40}
        return str(category) in {str(value) for value in allowed}
    return True


def valid_target_announce(site, announce):
    """Shared allowlist for prepared and website-canonical upload artifacts."""
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(announce)
        return (parsed.scheme in ('http', 'https')
                and parsed.hostname in UPLOAD_ANNOUNCE_HOSTS.get(site, ())
                and parsed.path == '/announce.php' and not parsed.username
                and not parsed.password and not parsed.fragment
                and parsed.port in (None, 80 if parsed.scheme == 'http' else 443))
    except (TypeError, ValueError):
        return False


def create_variant(content, site, zone, category, announce):
    site = str(site).upper()
    if not upload_route_supported(site, zone, category):
        raise ValueError('Unsupported upload route or category')
    if not valid_target_announce(site, announce):
        raise ValueError('Unrecognized target announce')
    signature = payload_signature(content)
    decoded, _ = _decode_bencode_value(bytes(content))
    info = dict(decoded[b'info'])
    info[b'private'] = 1
    info[b'source'] = f'UAP:{site}:{zone}:{category}'.encode()
    # Deliberate allowlist: no source passkey, web seeds, DHT nodes or publisher keys.
    result = bencode({b'announce': announce.encode(), b'info': info,
                      b'created by': b'Universal-Auto-Pilot'})
    if payload_signature(result) != signature or _extract_torrent_info_hash(result) == _extract_torrent_info_hash(content):
        raise ValueError('Variant did not preserve payload with a distinct hash')
    return result


def verify_completed(content, inventory, files):
    """Exact complete source on this client, including file selection/layout."""
    m = _extract_torrent_manifest(content)
    payload_signature(content)
    if (str(inventory.get('hash', '')).lower() != m['info_hash']
            or inventory.get('progress') != 1 or inventory.get('amount_left') != 0
            or inventory.get('size_bytes') != m['total_size_bytes']
            or not str(inventory.get('save_path') or '').startswith('/')
            or str(inventory.get('state', '')).lower().startswith(('checking', 'moving', 'error', 'missing', 'meta'))):
        raise ValueError('Source is incomplete or does not match the exported torrent')
    if not isinstance(files, list) or not files:
        raise ValueError('Source file inventory unavailable')
    expected = [(m['root_name'] + '/' + f['path'] if m['multi_file'] else f['path'], f['size_bytes']) for f in m['files']]
    if any('progress' in f and f['progress'] != 1 for f in files):
        raise ValueError('One or more source files are incomplete, including deselected files')
    actual = [(f['path'], f['size_bytes']) for f in files]
    # BEP 47 padding is virtual in qBittorrent's files API. Only explicitly
    # declared padding may be omitted; ordinary files must all match exactly.
    non_padding = [(m['root_name'] + '/' + f['path'] if m['multi_file'] else f['path'], f['size_bytes'])
                   for f in m['files'] if not f.get('is_padding')]
    if not non_padding or sorted(actual) not in (sorted(expected), sorted(non_padding)):
        raise ValueError('Source client file paths or sizes do not match metainfo')
    return m

def duplicate_payload_status(source, candidate):
    """Separate byte identity from layout identity used by cross-seed storage guards."""
    if payload_signature(source) == payload_signature(candidate):
        return 'duplicate'
    src, _ = _decode_bencode_value(bytes(source))
    dst, _ = _decode_bencode_value(bytes(candidate))
    def stream_identity(root):
        info = root[b'info']
        lengths = [f[b'length'] for f in info[b'files']] if b'files' in info else [info[b'length']]
        return (info[b'piece length'], info[b'pieces'], lengths)
    # Renaming a root or files does not make their identical piece stream new content.
    if stream_identity(src) == stream_identity(dst):
        return 'duplicate'
    left, right = _extract_torrent_manifest(source), _extract_torrent_manifest(candidate)
    def files(manifest):
        return {(f['path'].casefold(), f['size_bytes']) for f in manifest['files'] if not f.get('is_padding')}
    a, b = files(left), files(right)
    if a and a == b:
        return 'possible_duplicate'
    # A release may be part of a larger pack or have different padding/piece sizes.
    large_a = {(path.rsplit('/', 1)[-1], size) for path, size in a if size >= 16 * 1024**2}
    large_b = {(path.rsplit('/', 1)[-1], size) for path, size in b if size >= 16 * 1024**2}
    if large_a & large_b:
        return 'possible_duplicate'
    return 'different'
