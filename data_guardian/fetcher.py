"""Descarga un archivo TXT, CSV, PDF o imagen desde una URL https escrita por el usuario. Solo lo usa la interfaz, nunca el agente."""

import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from data_guardian.extractor import RICH_EXTENSIONS
from data_guardian.scanner import SUPPORTED_EXTENSIONS

ALLOWED_EXTENSIONS = SUPPORTED_EXTENSIONS | RICH_EXTENSIONS

MAX_BYTES = 10 * 1024 * 1024
TIMEOUT_SECONDS = 10
TOTAL_TIMEOUT_SECONDS = 30
CHUNK_BYTES = 64 * 1024


class FetchError(ValueError):
    """Una descarga rechazada o fallida. Los mensajes nunca incluyen la URL completa."""


class _PinnedConnection(http.client.HTTPSConnection):
    """Conexión HTTPS a una IP ya validada que sigue comprobando el certificado del host."""

    def __init__(self, host: str, ip: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _open_connection(host: str, ip: str, port: int, timeout: float) -> http.client.HTTPSConnection:
    """Abre una conexión fijada a `ip`."""
    return _PinnedConnection(host, ip, port, timeout)


def parse_url(url: str) -> tuple[str, int, str, str]:
    """Valida la URL; devuelve host, puerto, destino de la solicitud y un nombre de archivo seguro."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise FetchError("Solo se permiten URL https.")
    if parts.username or parts.password:
        raise FetchError("No se permiten URL con credenciales.")
    host = parts.hostname
    if not host:
        raise FetchError("La URL no tiene host.")
    try:
        port = parts.port or 443
    except ValueError:
        raise FetchError("La URL tiene un puerto no válido.") from None
    if port != 443:
        raise FetchError("Solo se permite el puerto https por defecto (443).")
    name = PurePosixPath(unquote(parts.path)).name
    if PurePosixPath(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise FetchError("La URL debe apuntar a un archivo .txt, .csv, .pdf, .png o .jpg.")
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    return host, port, target, re.sub(r"[^\w.-]", "_", name)


def _check_public(ip: str, host: str) -> None:
    """Rechaza direcciones de loopback, privadas, de enlace local y cualquier otra no pública."""
    address = ipaddress.ip_address(ip)
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    if not address.is_global:
        raise FetchError(f"El host '{host}' no resuelve a una dirección pública.")


def _lookup(host: str, port: int, timeout: float) -> list[tuple]:
    """getaddrinfo con límite de tiempo (no tiene uno propio); lanza OSError si falla o se agota el tiempo."""
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
        raise TimeoutError("La búsqueda DNS agotó el tiempo")
    if isinstance(outcome[0], OSError):
        raise outcome[0]
    return outcome[0]


def _resolve(host: str, port: int, timeout: float) -> str:
    """Resuelve el host y devuelve una IP, tras comprobar cada dirección a la que resuelve."""
    try:
        infos = _lookup(host, port, timeout)
    except OSError:
        raise FetchError(f"No se pudo resolver el host '{host}'.") from None
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
    """Descarga un archivo TXT, CSV, PDF o imagen por https y devuelve (nombre de archivo, contenido).

    `timeout` limita cada espera de red; `total_timeout` limita la descarga completa.
    """
    host, port, target, name = parse_url(url)
    deadline = time.monotonic() + total_timeout
    ip = _resolve(host, port, timeout)
    too_large = f"El archivo supera los {max_bytes // (1024 * 1024)} MB."
    try:
        connection = _open_connection(host, ip, port, timeout)
        try:
            connection.request("GET", target, headers={"User-Agent": "ai-data-guardian-lite"})
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise FetchError("El servidor intentó redirigir; no se siguen las redirecciones.")
            if response.status != 200:
                raise FetchError(f"El servidor respondió con el estado {response.status}.")
            length = response.getheader("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise FetchError(too_large)
            data = bytearray()
            while chunk := response.read(CHUNK_BYTES):
                data += chunk
                if time.monotonic() > deadline:
                    raise FetchError("La descarga tardó demasiado.")
                if len(data) > max_bytes:
                    raise FetchError(too_large)
        finally:
            connection.close()
    except (OSError, http.client.HTTPException) as exc:
        raise FetchError(f"No se pudo descargar desde '{host}' ({type(exc).__name__}).") from None
    return name, bytes(data)
