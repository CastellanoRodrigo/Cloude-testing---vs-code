"""Escáner con regex de correos, números de documento y probables claves de API en archivos TXT y CSV."""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

SUPPORTED_EXTENSIONS = {".txt", ".csv"}


class FindingKind(str, Enum):
    """Los tipos de datos sensibles que detecta el escáner."""

    EMAIL = "email"
    DOCUMENT_ID = "document_id"
    API_KEY = "api_key"


# Primero las claves de API, para que gane una clave que contenga una parte parecida a un correo o a un documento.
PATTERNS: list[tuple[FindingKind, re.Pattern[str]]] = [
    (
        FindingKind.API_KEY,
        re.compile(
            r"(?<![A-Za-z0-9_-])"
            r"(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36})"
            r"(?![A-Za-z0-9_-])"
        ),
    ),
    (FindingKind.EMAIL, re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    (
        FindingKind.DOCUMENT_ID,
        re.compile(
            r"(?<![A-Za-z0-9])"
            r"(?:\d{8}[A-Z]|[XYZ]\d{7}[A-Z]|\d{3}-\d{2}-\d{4})"
            r"(?![A-Za-z0-9])"
        ),
    ),
]


@dataclass(frozen=True)
class Finding:
    """Un valor sensible encontrado. `value` es un dato real: nunca lo registres ni lo envíes."""

    kind: FindingKind
    value: str = field(repr=False)
    line: int  # número de línea (empieza en 1)
    start: int  # posición del valor en el texto completo
    end: int


def mask_value(value: str) -> str:
    """Conserva los 2 primeros y los 2 últimos caracteres; es la única forma que puede salir de la máquina."""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def scan_text(text: str) -> list[Finding]:
    """Busca valores sensibles en `text`, en orden de posición, descartando los solapamientos."""
    found: list[Finding] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < other_end and other_start < end for other_start, other_end in taken):
                continue
            taken.append((start, end))
            line = text.count("\n", 0, start) + 1
            found.append(Finding(kind, match.group(), line, start, end))
    return sorted(found, key=lambda finding: finding.start)


def scan_file(path: str | Path) -> list[Finding]:
    """Escanea un archivo TXT o CSV, abierto en solo lectura. Los errores nunca incluyen su contenido."""
    file = Path(path)
    if file.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Tipo de archivo no admitido '{file.suffix}'. Usa .txt o .csv.")
    if not file.is_file():
        raise FileNotFoundError(f"No se encontró el archivo: {file}")
    try:
        with open(file, "r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except UnicodeDecodeError as exc:
        raise ValueError(f"El archivo no es texto UTF-8 válido: {file}") from exc
    return scan_text(text)
