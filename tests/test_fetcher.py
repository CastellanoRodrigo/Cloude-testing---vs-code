"""Pruebas del descargador. Sin red real: el DNS y las conexiones se simulan."""

import ipaddress
import socket
import threading
from typing import Any

import pytest

from data_guardian import fetcher
from data_guardian.fetcher import FetchError, fetch_url, parse_url

DNS = {"files.example.com": "93.184.216.34", "evil.example.com": "10.0.0.5", "localhost": "127.0.0.1"}
URL = "https://files.example.com/data/report.csv"


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self._headers = headers or {}

    def getheader(self, name: str) -> str | None:
        return self._headers.get(name)

    def read(self, size: int) -> bytes:
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk


class FakeConnection:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.requests: list[tuple[str, str]] = []
        self.closed = False

    def request(self, method: str, target: str, headers: dict[str, str]) -> None:
        self.requests.append((method, target))

    def getresponse(self) -> FakeResponse:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch) -> None:
    def getaddrinfo(host: str, port: int, type: int = 0) -> list[Any]:
        try:
            ipaddress.ip_address(host)
            ip = host
        except ValueError:
            if host not in DNS:
                raise socket.gaierror("unknown host") from None
            ip = DNS[host]
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def serve(monkeypatch, response: FakeResponse | Exception) -> dict[str, Any]:
    """Replace the connection; return what was asked of it."""
    seen: dict[str, Any] = {}

    def open_connection(host: str, ip: str, port: int, timeout: float) -> FakeConnection:
        seen.update(host=host, ip=ip, port=port, connection=FakeConnection(response))
        return seen["connection"]

    monkeypatch.setattr(fetcher, "_open_connection", open_connection)
    return seen


@pytest.mark.parametrize(
    "url",
    [
        "http://files.example.com/a.txt",
        "ftp://files.example.com/a.txt",
        "files.example.com/a.txt",
        "https://user:pass@files.example.com/a.txt",
        "https:///a.txt",
        "https://files.example.com/a.docx",
        "https://files.example.com/",
        "https://files.example.com/.csv",
        "https://files.example.com:notaport/a.txt",
        "https://files.example.com:8443/a.txt",
        "https://files.example.com:22/a.txt",
    ],
)
def test_parse_url_rejects_bad_urls(url: str) -> None:
    with pytest.raises(FetchError):
        parse_url(url)


def test_parse_url_keeps_query_and_sanitizes_name() -> None:
    host, port, target, name = parse_url("https://files.example.com:443/a/my%20data.csv?v=2")
    assert (host, port, target, name) == ("files.example.com", 443, "/a/my%20data.csv?v=2", "my_data.csv")


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/a.txt",
        "https://10.0.0.5/a.txt",
        "https://192.168.1.1/a.txt",
        "https://169.254.169.254/a.csv",
        "https://[::1]/a.txt",
        "https://[::ffff:127.0.0.1]/a.txt",
        "https://localhost/a.txt",
        "https://evil.example.com/a.txt",
    ],
)
def test_private_addresses_are_rejected_before_connecting(monkeypatch, url: str) -> None:
    seen = serve(monkeypatch, FakeResponse(body=b"x"))
    with pytest.raises(FetchError, match="dirección pública"):
        fetch_url(url)
    assert seen == {}


def test_unresolvable_host(monkeypatch) -> None:
    with pytest.raises(FetchError, match="No se pudo resolver"):
        fetch_url("https://nowhere.example.org/a.txt")


def test_download_returns_name_and_bytes_on_pinned_ip(monkeypatch) -> None:
    seen = serve(monkeypatch, FakeResponse(body=b"a,b\n1,2\n"))
    name, data = fetch_url(URL + "?v=1")
    assert (name, data) == ("report.csv", b"a,b\n1,2\n")
    assert (seen["host"], seen["ip"], seen["port"]) == ("files.example.com", "93.184.216.34", 443)
    assert seen["connection"].requests == [("GET", "/data/report.csv?v=1")]
    assert seen["connection"].closed


def test_redirect_is_not_followed(monkeypatch) -> None:
    serve(monkeypatch, FakeResponse(status=302, headers={"Location": "https://127.0.0.1/a.txt"}))
    with pytest.raises(FetchError, match="redirigir"):
        fetch_url(URL)


def test_non_200_status(monkeypatch) -> None:
    serve(monkeypatch, FakeResponse(status=404))
    with pytest.raises(FetchError, match="estado 404"):
        fetch_url(URL)


def test_too_large_by_content_length(monkeypatch) -> None:
    serve(monkeypatch, FakeResponse(body=b"x", headers={"Content-Length": "999999999"}))
    with pytest.raises(FetchError, match="supera"):
        fetch_url(URL)


def test_too_large_while_streaming_closes_connection(monkeypatch) -> None:
    seen = serve(monkeypatch, FakeResponse(body=b"x" * 3_000_000))
    with pytest.raises(FetchError, match="supera"):
        fetch_url(URL, max_bytes=1_048_576)
    assert seen["connection"].closed


def test_network_error_message_has_host_but_not_the_url(monkeypatch) -> None:
    serve(monkeypatch, TimeoutError("timed out"))
    with pytest.raises(FetchError) as info:
        fetch_url(URL + "?token=SECRET123")
    message = str(info.value)
    assert "files.example.com" in message and "TimeoutError" in message
    assert "SECRET123" not in message and "/data/report.csv" not in message


def test_slow_trickle_hits_the_total_deadline(monkeypatch) -> None:
    serve(monkeypatch, FakeResponse(body=b"x" * 1_000_000))
    clock = iter(range(0, 10_000, 10))  # every look at the clock is 10 s later
    monkeypatch.setattr(fetcher.time, "monotonic", lambda: next(clock))
    with pytest.raises(FetchError, match="tardó demasiado"):
        fetch_url(URL, total_timeout=25)


def test_dns_lookup_that_hangs_is_cut_off(monkeypatch) -> None:
    release = threading.Event()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: release.wait(5))
    try:
        with pytest.raises(FetchError, match="No se pudo resolver"):
            fetch_url(URL, timeout=0.1)
    finally:
        release.set()
