"""Read account storage independently of the torrent client implementation."""
import math
import re
from urllib.parse import urlsplit


def parse_size_gib(value):
    """Parse dashboard sizes into binary GiB, keeping decimal and binary units distinct."""
    if not isinstance(value, str):
        raise TypeError("Swizzin size must be a string with a unit")
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|KIB|MIB|GIB|TIB)\s*",
        value, flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError("Invalid Swizzin size")
    multipliers = {
        "B": 1, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
        "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4,
    }
    size = float(match.group(1)) * multipliers[match.group(2).upper()] / (1024 ** 3)
    if not math.isfinite(size) or size < 0:
        raise ValueError("Swizzin size is outside the valid range")
    return size


def read_swizzin_storage_stats(url, request, mount="/"):
    """Prefer quota; use the configured mount only when quota is explicitly absent.

    Authentication, network and malformed-data errors propagate. They must never
    turn an unreadable account limit into permission to use the whole disk.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Invalid client URL for Swizzin API")
    if parsed.username or parsed.password:
        raise ValueError("Swizzin URL must not contain credentials")
    origin = f"{parsed.scheme}://{parsed.netloc}"

    def check_status(payload):
        if (payload.get("error") or payload.get("errors")
                or payload.get("success") is False or payload.get("ok") is False):
            raise ValueError("Swizzin API reported an error")

    def get_json(endpoint, optional=False):
        response = request(
            "GET", origin + endpoint,
            headers={"Accept": "application/json", "Referer": origin + "/"},
            timeout=15, allow_redirects=False, verify=True,
        )
        if response is None:
            raise RuntimeError(f"Swizzin {endpoint} network failure")
        if optional and response.status_code in (404, 405, 501):
            return None
        if response.status_code != 200:
            raise RuntimeError(f"Swizzin {endpoint} HTTP {response.status_code}")
        content_type = str(response.headers.get("Content-Type", "")).casefold()
        # Upstream Flask returns this literal body with HTTP 200/text-html when
        # /install/.quota.lock is absent. Other HTML (including login) is an error.
        if (optional and content_type.split(";", 1)[0].strip() in ("text/html", "text/plain")
                and response.text.strip() == "Quota not installed"):
            return None
        if "json" not in content_type:
            raise ValueError(f"Swizzin {endpoint} response is not JSON")
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError(f"Swizzin {endpoint} payload is invalid")
        check_status(payload)
        return payload

    def sample(source, values, disk_mount=None):
        if not isinstance(values, dict):
            raise TypeError(f"Swizzin {source} object is invalid")
        check_status(values)
        free = parse_size_gib(values.get("diskfree"))
        total = parse_size_gib(values.get("disktotal"))
        used = parse_size_gib(values.get("diskused"))
        if total <= 0 or free > total or used > total:
            raise ValueError(f"Swizzin {source} values are out of range")
        result = {
            "source": source, "free_gb": free, "total_gb": total, "used_gb": used,
            "quota_free_gb": None, "quota_total_gb": None, "quota_used_gb": None,
            "disk_free_gb": None, "disk_total_gb": None, "disk_used_gb": None,
            "disk_mount": disk_mount,
        }
        prefix = "quota" if source == "quota" else "disk"
        result.update({f"{prefix}_free_gb": free, f"{prefix}_total_gb": total,
                       f"{prefix}_used_gb": used})
        return result

    payload = get_json("/stats/quota", optional=True)
    quota = payload.get("quota") if payload is not None else None
    if quota is not None:
        return sample("quota", quota)

    if (not isinstance(mount, str) or not mount.startswith("/")
            or any(part in (".", "..") for part in mount.split("/"))
            or "\x00" in mount or "\\" in mount):
        raise ValueError("Swizzin storage_mount must be a safe absolute mount path")
    disks = get_json("/stats/disk")
    if not isinstance(disks.get(mount), dict):
        raise ValueError("Swizzin configured mount is missing")
    return sample("mount", disks[mount], mount)
