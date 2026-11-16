"""Deluge daemon SSL/RPC transport (1.3 zlib stream and 2.x protocol 1).

No automatic reconnect/replay: a lost acknowledgement may represent a completed write.
The wire format follows Deluge's core/rpcserver.py and transfer.py.
"""
import hashlib
import hmac
import re
import socket
import ssl
import struct
import threading
import time
import zlib
from urllib.parse import urlsplit

from auto_upload import UploadBlocked


class DaemonRPCError(UploadBlocked):
    """Controlled errors without remote exception text or credentials."""


def _lists(value):
    # rencode decodes all sequences as tuples; the shared Web/core adapter uses lists.
    if isinstance(value, (tuple, list)):
        return [_lists(item) for item in value]
    if isinstance(value, dict):
        return {key: _lists(item) for key, item in value.items()}
    return value


def validate_rpc_config(url, options=None):
    """Validate public connection settings without connecting or loading a certificate."""
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == 'deluge+ssl' and parsed.hostname and parsed.port
                 and not parsed.username and not parsed.password and not parsed.query
                 and not parsed.fragment and parsed.path in ('', '/'))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise DaemonRPCError('Deluge RPC URL requires deluge+ssl://host:port without credentials or path')
    options = {} if options is None else options
    if not isinstance(options, dict) or set(options) - {'protocol', 'tls_ca_file', 'tls_cert_sha256'}:
        raise DaemonRPCError('Invalid Deluge RPC settings')
    if options.get('protocol', '2') not in ('1.3', '2'):
        raise DaemonRPCError('Deluge rpc.protocol must be "1.3" or "2"')
    ca = options.get('tls_ca_file')
    pin = options.get('tls_cert_sha256')
    if ca is not None and (not isinstance(ca, str) or not ca.strip() or '\x00' in ca):
        raise DaemonRPCError('Deluge rpc.tls_ca_file must be a certificate file path')
    if pin is not None and (not isinstance(pin, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', pin)):
        raise DaemonRPCError('Deluge rpc.tls_cert_sha256 requires 64 hexadecimal characters')
    if ca is not None and pin is not None:
        raise DaemonRPCError('Choose Deluge RPC CA verification or certificate pinning')
    return parsed


class DaemonRPC:
    TIMEOUT = 30
    MAX_WIRE = 32 * 1024 * 1024
    MAX_PLAIN = 128 * 1024 * 1024

    def __init__(self, url, options=None):
        parsed = validate_rpc_config(url, options)
        self.host, self.port = parsed.hostname, parsed.port
        self.options = dict(options or {})
        self.legacy = self.options.get('protocol', '2') == '1.3'
        self._socket = None
        self._buffer = b''
        self._ident = 0
        self._lock = threading.RLock()

    def close(self):
        with self._lock:
            if self._socket is not None:
                self._socket.close()
            self._socket = None
            self._buffer = b''

    def connect(self):
        with self._lock:
            self.close()
            raw = None
            try:
                # Keep Web UI usable even if the optional daemon codec is not installed yet.
                import rencode
                self._codec = rencode
                pin = self.options.get('tls_cert_sha256')
                if pin:
                    # The exact DER certificate is the trust anchor; verify before any RPC/auth.
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                else:
                    context = ssl.create_default_context(cafile=self.options.get('tls_ca_file'))
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                raw = socket.create_connection((self.host, self.port), timeout=self.TIMEOUT)
                self._socket = context.wrap_socket(raw, server_hostname=self.host)
                raw = None
                if pin and not hmac.compare_digest(
                        hashlib.sha256(self._socket.getpeercert(binary_form=True)).hexdigest(), pin.lower()):
                    raise DaemonRPCError('Deluge RPC certificate fingerprint mismatch')
            except Exception as exc:
                if raw is not None:
                    raw.close()
                self.close()
                if isinstance(exc, DaemonRPCError):
                    raise
                raise DaemonRPCError('Deluge RPC TLS connection failed: ' + type(exc).__name__) from None

    def _read_more(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DaemonRPCError('Deluge RPC response timeout')
        self._socket.settimeout(remaining)
        chunk = self._socket.recv(65536)
        if not chunk:
            raise DaemonRPCError('Deluge RPC connection closed before acknowledgement')
        self._buffer += chunk
        if len(self._buffer) > self.MAX_WIRE + 65536:
            raise DaemonRPCError('Deluge RPC frame exceeds limit')

    def _message(self, deadline):
        if not self.legacy:
            while len(self._buffer) < 5:
                self._read_more(deadline)
            version, length = struct.unpack('!BI', self._buffer[:5])
            if version != 1 or not 0 < length <= self.MAX_WIRE:
                raise DaemonRPCError('Invalid Deluge RPC frame header')
            while len(self._buffer) < 5 + length:
                self._read_more(deadline)
            body, self._buffer = self._buffer[5:5 + length], self._buffer[5 + length:]
            decoder = zlib.decompressobj()
            plain = decoder.decompress(body, self.MAX_PLAIN + 1)
            if not decoder.eof or decoder.unused_data or len(plain) > self.MAX_PLAIN:
                raise DaemonRPCError('Invalid or oversized Deluge RPC compression')
        else:
            decoder = zlib.decompressobj()
            parts, plain_size, wire_size = [], 0, 0
            while True:
                if not self._buffer:
                    self._read_more(deadline)
                chunk = self._buffer
                self._buffer = b''
                part = decoder.decompress(chunk, self.MAX_PLAIN - plain_size + 1)
                parts.append(part); plain_size += len(part)
                wire_size += len(chunk) - len(decoder.unused_data)
                if plain_size > self.MAX_PLAIN or wire_size > self.MAX_WIRE:
                    raise DaemonRPCError('Deluge RPC stream exceeds limit')
                if decoder.eof:
                    self._buffer = decoder.unused_data
                    break
            plain = b''.join(parts)
        return self._codec.loads(plain, decode_utf8=True)

    def call(self, method, params=(), **kwargs):
        with self._lock:
            if self._socket is None:
                raise DaemonRPCError('Deluge RPC is disconnected; login required')
            try:
                deadline = time.monotonic() + self.TIMEOUT
                self._ident += 1
                ident = self._ident
                body = zlib.compress(self._codec.dumps(((ident, method, tuple(params), kwargs),)))
                if len(body) > self.MAX_WIRE:
                    raise DaemonRPCError('Deluge RPC request exceeds limit')
                self._socket.settimeout(self.TIMEOUT)
                self._socket.sendall(body if self.legacy else struct.pack('!BI', 1, len(body)) + body)
                while True:
                    if time.monotonic() >= deadline:
                        raise DaemonRPCError('Deluge RPC response timeout')
                    message = self._message(deadline)
                    if not isinstance(message, (tuple, list)) or len(message) < 3 or type(message[0]) is not int:
                        raise DaemonRPCError('Invalid Deluge RPC response')
                    if message[0] == 3:  # Unsolicited events are not acknowledgements.
                        continue
                    if type(message[1]) is not int or message[1] != ident:
                        raise DaemonRPCError('Deluge RPC response identity mismatch')
                    if message[0] == 2:
                        # Never forward server exception arguments/tracebacks (may contain secrets).
                        raise DaemonRPCError('Deluge daemon rejected operation: ' + method)
                    if message[0] != 1 or len(message) != 3:
                        raise DaemonRPCError('Invalid Deluge RPC acknowledgement')
                    return _lists(message[2])
            except Exception as exc:
                self.close()
                if isinstance(exc, DaemonRPCError):
                    raise
                raise DaemonRPCError('Deluge RPC transport or response failed: ' + type(exc).__name__) from None
