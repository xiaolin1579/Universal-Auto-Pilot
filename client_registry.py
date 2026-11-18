"""Client extension points; register an implementation rather than branching by vendor."""
from dataclasses import dataclass
from typing import Callable, Protocol


class TorrentClient(Protocol):
    name: str
    def login(self) -> bool: ...
    def refresh_status(self) -> bool: ...
    def get_all_torrents_info(self) -> list | None: ...
    def get_torrent_file_manifest(self, infohash: str) -> list | None: ...
    def is_torrent_exists(self, infohash: str) -> bool | None: ...
    def reannounce_torrent(self, infohash: str) -> bool: ...
    def stop_torrent(self, infohash: str) -> bool: ...
    def delete_torrent(self, infohash: str, delete_files: bool = False) -> bool: ...


@dataclass(frozen=True)
class ClientAdapter:
    client_type: str
    factory: Callable
    client_class: type
    storage_reader: Callable
    purge_reader: Callable | None = None
    supports_keep_files: bool = True
    close: Callable | None = None
    upload_factory: Callable | None = None
    storage_conflict_reader: Callable | None = None


class ClientRegistry:
    def __init__(self):
        self._adapters = {}

    def register(self, adapter):
        key = adapter.client_type.strip().lower()
        if not key or key in self._adapters:
            raise ValueError('Duplicate or empty client type')
        self._adapters[key] = adapter

    def create(self, config):
        key = str(config.get('type') or '').lower()
        if key not in self._adapters:
            raise ValueError('Unsupported torrent client: ' + key)
        return self._adapters[key].factory(config)

    def adapter_for(self, client):
        matches = [a for a in self._adapters.values() if isinstance(client, a.client_class)]
        if len(matches) != 1:
            raise ValueError('Unregistered or ambiguous torrent client')
        return matches[0]

    def close(self, client):
        adapter = self.adapter_for(client)
        closer = adapter.close or getattr(client, 'close', None)
        if adapter.close:
            adapter.close(client)
        elif callable(closer):
            closer()
