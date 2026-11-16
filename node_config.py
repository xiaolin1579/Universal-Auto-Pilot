"""Resolve shared node configuration without changing the document on disk.

Only the returned runtime view contains compatibility fields and credentials.
Editors must always save the original document, never this expanded view.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """A configuration error whose message contains field names, never values."""


def uses_shared_nodes(config):
    return (any(key in config for key in ("CREDENTIALS", "STORAGES", "CLEANER"))
            or any(isinstance(n, dict) and ("auth" in n or "id" in n)
                   for n in config.get("NODES", [])))


def _object(value, field):
    if not isinstance(value, dict):
        raise ConfigError(field + " must be an object")
    return value


def _bool(value, field):
    if type(value) is not bool:
        raise ConfigError(field + " must be true or false")
    return value


def _number(value, field):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ConfigError(field + " must be a finite non-negative number")
    return value


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(field + " must be a non-empty string")
    return value


def _url(value, field, origin_only=False):
    _text(value, field)
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ("http", "https") and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment)
        parsed.port
    except ValueError:
        valid = False
    if not valid or (origin_only and parsed.path not in ("", "/")):
        raise ConfigError(field + " must be an HTTP(S) URL without credentials or query")
    return value.rstrip("/")


def _known(value, allowed, field):
    if set(value) - set(allowed):
        raise ConfigError(field + " contains unsupported fields")


def normalize_config(raw):
    """Return a legacy-compatible view; raw input and on-disk data are untouched."""
    _object(raw, "config")
    config = deepcopy(raw)
    nodes = config.get("NODES", [])
    if not isinstance(nodes, list) or any(not isinstance(n, dict) for n in nodes):
        raise ConfigError("NODES must be a list of objects")
    if not uses_shared_nodes(config):
        return config

    credentials = _object(config.get("CREDENTIALS", {}), "CREDENTIALS")
    storages = _object(config.get("STORAGES", {}), "STORAGES")
    cleaner = _object(config.get("CLEANER", {}), "CLEANER")
    _known(cleaner, {"enable", "defaults", "policies"}, "CLEANER")
    defaults = _object(cleaner.get("defaults", {}), "CLEANER.defaults")
    policies = _object(cleaner.get("policies", {}), "CLEANER.policies")
    cleaner_fields = {"enable", "allow_space_reclaim", "policy", "min_ratio",
                      "min_time_minutes", "max_time_minutes"}
    _known(defaults, cleaner_fields, "CLEANER.defaults")
    master = _bool(cleaner.get("enable", False), "CLEANER.enable")
    for policy in policies.values():
        _object(policy, "CLEANER policy")
        _known(policy, {"min_ratio", "min_time_minutes", "max_time_minutes"}, "CLEANER policy")
        for key, value in policy.items():
            _number(value, "CLEANER policy." + key)

    def credential(ref):
        _text(ref, "credential_ref")
        if ref not in credentials:
            raise ConfigError("Unknown credential_ref")
        item = _object(credentials[ref], "CREDENTIALS entry")
        _known(item, {"username", "password"}, "CREDENTIALS entry")
        if not isinstance(item.get("username"), str) or not isinstance(item.get("password"), str):
            raise ConfigError("CREDENTIALS entry requires username and password strings")
        return item["username"], item["password"]

    by_id, names = {}, set()
    for node in nodes:
        ident = _text(node.get("id"), "NODES.id")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", ident):
            raise ConfigError("NODES.id contains unsupported characters")
        name = _text(node.get("name"), "NODES.name")
        if name != name.strip():
            raise ConfigError("NODES.name must not contain surrounding whitespace")
        name_key = name.strip().casefold()
        if ident in by_id or name_key in names:
            raise ConfigError("Duplicate node id or name")
        by_id[ident] = node
        names.add(name_key)
        _bool(node.get("enable", False), "NODES.enable")
        _url(node.get("url"), "NODES.url")
        if node.get("type") not in ("qbit", "rtorrent", "deluge"):
            raise ConfigError("Unsupported shared-config client type")
        storage_id = _text(node.get("storage_id"), "NODES.storage_id")
        if storage_id not in storages:
            raise ConfigError("Unknown storage_id")

        for key in ("qb_user", "qb_pass", "rt_user", "rt_pass", "deluge_user", "deluge_pass", "http_basic_user",
                    "http_basic_pass", "provider_user", "provider_pass", "provider_auth_origin"):
            node.pop(key, None)
        auth = _object(node.get("auth"), "NODES.auth")
        _known(auth, {"http_basic", "session"}, "NODES.auth")
        basic = auth.get("http_basic")
        node["nginx"] = basic is not None
        node["provider_sso"] = False
        if basic is not None:
            _object(basic, "auth.http_basic")
            _known(basic, {"credential_ref"}, "auth.http_basic")
            user, password = credential(basic.get("credential_ref"))
            node["http_basic_user"], node["http_basic_pass"] = user, password
        if node["type"] == "rtorrent":
            if auth.get("session") is not None or basic is None:
                raise ConfigError("rTorrent requires HTTP auth and does not support auth.session")
            node["rt_user"], node["rt_pass"] = credential(basic.get("credential_ref"))
        elif node['type'] == 'deluge':
            session = _object(auth.get('session'), 'auth.session')
            _known(session, {'type', 'credential_ref'}, 'auth.session')
            if session.get('type') != 'deluge_web':
                raise ConfigError('Deluge requires a deluge_web session')
            node['deluge_user'], node['deluge_pass'] = credential(session.get('credential_ref'))
            if 'daemon_id' in node:
                _text(node['daemon_id'], 'NODES.daemon_id')
        else:
            session = _object(auth.get("session"), "auth.session")
            _known(session, {"type", "credential_ref", "origin"}, "auth.session")
            user, password = credential(session.get("credential_ref"))
            node["qb_user"], node["qb_pass"] = user, password
            if session.get("type") == "onlyone_sso":
                if basic is not None:
                    raise ConfigError("OnlyOne SSO does not support HTTP Basic auth")
                node["provider_sso"] = True
                node["provider_user"], node["provider_pass"] = user, password
                if "origin" in session:
                    node["provider_auth_origin"] = _url(session["origin"], "auth.session.origin", True)
            elif session.get("type") != "qbit" or "origin" in session:
                raise ConfigError("Unsupported qBittorrent session type or origin")

        selected = {**defaults, **_object(node.get("cleaner", {}), "NODES.cleaner")}
        _known(selected, cleaner_fields, "NODES.cleaner")
        policy = selected.get("policy")
        if policy is not None:
            _text(policy, "cleaner.policy")
            if policy not in policies:
                raise ConfigError("Unknown Cleaner policy")
        rule = {**defaults, **policies.get(policy, {}), **node.get("cleaner", {})}
        node["clean_settings"] = {"enable": _bool(rule.get("enable", False), "cleaner.enable")}
        for new, old, default in (("min_ratio", "min_ratio", 1.5),
                                  ("min_time_minutes", "min_time", 720),
                                  ("max_time_minutes", "max_time", 1440)):
            node["clean_settings"][old] = _number(rule.get(new, default), "cleaner." + new)
        if node["clean_settings"]["max_time"] < node["clean_settings"]["min_time"]:
            raise ConfigError("Cleaner max_time_minutes must be >= min_time_minutes")
        node["allow_space_reclaim"] = _bool(rule.get("allow_space_reclaim", False), "cleaner.allow_space_reclaim")

    for node in nodes:
        storage = _object(storages[node["storage_id"]], "STORAGES entry")
        _known(storage, {"disk_type", "quota_gib", "safety_buffer_gib", "telemetry"}, "STORAGES entry")
        node["disk_type"] = storage.get("disk_type", "HDD")
        if node["disk_type"] not in ("HDD", "SSD", "NVME", "HYBRID"):
            raise ConfigError("Invalid STORAGES.disk_type")
        node["quota_gb"] = _number(storage.get("quota_gib", 0), "STORAGES.quota_gib")
        accounting = _object(node.get("space_accounting", {}), "NODES.space_accounting")
        _known(accounting, {"safety_buffer_gib", "preallocated_in_free_space"}, "NODES.space_accounting")
        node["storage_safety_buffer_gb"] = _number(
            accounting.get("safety_buffer_gib", storage.get("safety_buffer_gib", 15)),
            "storage safety_buffer_gib",
        )
        if "preallocated_in_free_space" in accounting:
            node["preallocated_downloads_accounted_in_free_space"] = _bool(
                accounting["preallocated_in_free_space"], "space_accounting.preallocated_in_free_space")
        telemetry = _object(storage.get("telemetry", {}), "STORAGES.telemetry")
        _known(telemetry, {"type", "via_node", "mount"}, "STORAGES.telemetry")
        kind = telemetry.get("type", "client")
        if kind not in ("swizzin", "onlyone", "client"):
            raise ConfigError("Unsupported storage telemetry type")
        source_id = telemetry.get("via_node", node["id"])
        _text(source_id, "telemetry.via_node")
        if source_id not in by_id or by_id[source_id]["storage_id"] != node["storage_id"]:
            raise ConfigError("telemetry.via_node must reference a node on the same storage")
        source = by_id[source_id]
        node["storage_api"] = "swizzin" if kind == "swizzin" else ""
        node["storage_mount"] = telemetry.get("mount", "/")
        mount = node["storage_mount"]
        if (not isinstance(mount, str) or not mount.startswith("/") or "\x00" in mount
                or "\\" in mount or any(p in (".", "..") for p in mount.split("/"))):
            raise ConfigError("telemetry.mount must be a safe absolute mount path")
        node.pop("_storage_reader", None)
        if kind == "swizzin":
            if source.get("provider_sso"):
                raise ConfigError("Swizzin telemetry does not support a provider SSO source")
            node["_storage_reader"] = {
                "url": source["url"],
                "http_basic_user": source.get("http_basic_user"),
                "http_basic_pass": source.get("http_basic_pass"),
            }
        if kind == "onlyone" and (not node.get("provider_sso") or source_id != node["id"]):
            raise ConfigError("OnlyOne telemetry requires its own qBittorrent SSO session")

    # Stale edits by legacy tools must not silently lose to the shared values.
    aliases = {"qb_user", "qb_pass", "rt_user", "rt_pass", "deluge_user", "deluge_pass", "http_basic_user",
               "http_basic_pass", "nginx", "provider_sso", "provider_user",
               "provider_pass", "provider_auth_origin", "quota_gb", "disk_type",
               "storage_api", "storage_mount", "storage_safety_buffer_gb",
               "allow_space_reclaim", "clean_settings",
               "preallocated_downloads_accounted_in_free_space"}
    for original, resolved in zip(raw.get("NODES", []), nodes):
        if any(key in original and original[key] != resolved.get(key) for key in aliases):
            raise ConfigError("Legacy node fields conflict with shared references; edit the shared source")

    # Preserve unrelated root fields. New Cleaner is authoritative in this view.
    config["GLOBAL_CLEAN"] = {"enable": master, "min_ratio": 1.5,
                              "min_time": 720, "max_time": 1440}
    if "GLOBAL_CLEAN" in raw:
        previous = _object(raw["GLOBAL_CLEAN"], "GLOBAL_CLEAN")
        if any(key.lower() not in config["GLOBAL_CLEAN"]
               or value != config["GLOBAL_CLEAN"][key.lower()]
               for key, value in previous.items()):
            raise ConfigError("GLOBAL_CLEAN conflicts with CLEANER; remove legacy Cleaner fields")
    return config


def load_config(path):
    with Path(path).open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return normalize_config(raw)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Validate legacy/shared node config without connecting to services")
    parser.add_argument("path", nargs="?", default="config.json")
    args = parser.parse_args()
    try:
        config = load_config(args.path)
    except (OSError, ValueError) as exc:
        # Do not print the raw document, credential values or parser context.
        parser.exit(1, "Configuration invalid: " + (str(exc) if isinstance(exc, ConfigError)
                                                   else type(exc).__name__) + "\n")
    print("Configuration OK: " + str(len(config.get("NODES", []))) + " nodes")
