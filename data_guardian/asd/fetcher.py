"""Download one TXT/CSV file from a user-typed https URL. Used by the UI only, never by the agent."""

import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from data_guardian.scanner import SUPPORTED_EXTENSIONS

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = 10
TOTAL_TIMEOUT_SECONDS = 30
CHUNK_BYTES = 64 * 1024


class FetchError(ValueError):
    """A refused or failed download. Messages never include the full URL."""


class _PinnedConnection(http.client.HTTPSConnection):
    """HTTPS connection to an already validated IP, still checking the host's certificate."""

    def __init__(self, host: str, ip: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _open_connection(host: str, ip: str, port: int, timeout: float) -> http.client.HTTPSConnection:
    """Open a connection pinned to `ip`."""
    return _PinnedConnection(host, ip, port, timeout)


def parse_url(url: str) -> tuple[str, int, str, str]:
    """Validate the URL; return host, port, request target and a safe file name."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise FetchError("Only https URLs are allowed.")
    if parts.username or parts.password:
        raise FetchError("URLs with credentials are not allowed.")
    host = parts.hostname
    if not host:
        raise FetchError("The URL has no host.")
    try:
        port = parts.port or 443
    except ValueError:
        raise FetchError("The URL has an invalid port.") from None
    if port != 443:
        raise FetchError("Only the default https port (443) is allowed.")
    name = PurePosixPath(unquote(parts.path)).name
    if PurePosixPath(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise FetchError("The URL must point to a .txt or .csv file.")
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    return host, port, target, re.sub(r"[^\w.-]", "_", name)


def _check_public(ip: str, host: str) -> None:
    """Reject loopback, private, link-local and other non-public addresses."""
    address = ipaddress.ip_address(ip)
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    if not address.is_global:
        raise FetchError(f"Host '{host}' does not resolve to a public address.")


def _lookup(host: str, port: int, timeout: float) -> list[tuple]:
    """getaddrinfo with a time limit (it has none of its own); raises OSError on failure or timeout."""
    outcome: list[list[tuple] | OSError] = []

    def work() -> None:
        try:
            outcome.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as exc:
            outcome.append(exc)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout)
    if not outcome:
        raise TimeoutError("DNS lookup timed out")
    if isinstance(outcome[0], OSError):
        raise outcome[0]
    return outcome[0]


def _resolve(host: str, port: int, timeout: float) -> str:
    """Resolve the host and return an IP, after checking every address it resolves to."""
    try:
        infos = _lookup(host, port, timeout)
    except OSError:
        raise FetchError(f"Could not resolve host '{host}'.") from None
    ips = [str(info[4][0]) for info in infos]
    for ip in ips:
        _check_public(ip, host)
    return ips[0]


def fetch_url(
    url: str,
    max_bytes: int = MAX_BYTES,
    timeout: float = TIMEOUT_SECONDS,
    total_timeout: float = TOTAL_TIMEOUT_SECONDS,
) -> tuple[str, bytes]:
    """Download a TXT/CSV file over https and return (file name, content).

    `timeout` limits each network wait; `total_timeout` limits the whole download.
    """
    host, port, target, name = parse_url(url)
    deadline = time.monotonic() + total_timeout
    ip = _resolve(host, port, timeout)
    too_large = f"The file is larger than {max_bytes // (1024 * 1024)} MB."
    try:
        connection = _open_connection(host, ip, port, timeout)
        try:
            connection.request("GET", target, headers={"User-Agent": "ai-data-guardian-lite"})
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise FetchError("The server tried to redirect; redirects are not followed.")
            if response.status != 200:
                raise FetchError(f"The server answered with status {response.status}.")
            length = response.getheader("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise FetchError(too_large)
            data = bytearray()
            while chunk := response.read(CHUNK_BYTES):
                data += chunk
                if time.monotonic() > deadline:
                    raise FetchError("The download took too long.")
                if len(data) > max_bytes:
                    raise FetchError(too_large)
        finally:
            connection.close()
    except (OSError, http.client.HTTPException) as exc:
        raise FetchError(f"Could not download from '{host}' ({type(exc).__name__}).") from None
    return name, bytes(data)
