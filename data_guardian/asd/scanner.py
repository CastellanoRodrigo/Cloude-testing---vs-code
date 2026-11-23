"""Regex scanner for emails, ID numbers and likely API keys in TXT and CSV files."""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

SUPPORTED_EXTENSIONS = {".txt", ".csv"}


class FindingKind(str, Enum):
    """The kinds of sensitive data the scanner detects."""

    EMAIL = "email"
    DOCUMENT_ID = "document_id"
    API_KEY = "api_key"


# API keys first, so a key that contains an email-like or ID-like part wins.
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
    """One sensitive value found. `value` is real data: never log or send it."""

    kind: FindingKind
    value: str = field(repr=False)
    line: int  # 1-based line number
    start: int  # offset of the value in the whole text
    end: int


def mask_value(value: str) -> str:
    """Keep the first 2 and last 2 characters; the only form that may leave the machine."""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def scan_text(text: str) -> list[Finding]:
    """Find sensitive values in `text`, in order of position, dropping overlaps."""
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
    """Scan a TXT or CSV file, opened read-only. Errors never include file content."""
    file = Path(path)
    if file.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '{file.suffix}'. Use .txt or .csv.")
    if not file.is_file():
        raise FileNotFoundError(f"File not found: {file}")
    try:
        with open(file, "r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8 text: {file}") from exc
    return scan_text(text)
