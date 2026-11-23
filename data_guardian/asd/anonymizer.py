"""Deterministic replacement of sensitive values with redaction labels."""

from pathlib import Path

from data_guardian.scanner import SUPPORTED_EXTENSIONS, Finding, FindingKind, scan_text

LABELS: dict[FindingKind, str] = {
    FindingKind.EMAIL: "[EMAIL_REDACTED]",
    FindingKind.DOCUMENT_ID: "[DOCUMENT_ID_REDACTED]",
    FindingKind.API_KEY: "[API_KEY_REDACTED]",
}


def anonymize_text(text: str, findings: list[Finding]) -> str:
    """Replace each finding in the text with its label (no AI involved)."""
    result = text
    for finding in sorted(findings, key=lambda f: f.start, reverse=True):
        result = result[: finding.start] + LABELS[finding.kind] + result[finding.end :]
    return result


def output_path(path: str | Path) -> Path:
    """Return the anonymized copy path: <name>_anonymized.<ext>, next to the original."""
    original = Path(path)
    return original.with_name(f"{original.stem}_anonymized{original.suffix}")


def anonymize_file(path: str | Path, findings: list[Finding] | None = None) -> Path:
    """Write an anonymized copy of a TXT/CSV file and return its path.

    The original is opened read-only and never modified. Fails if the copy exists.
    If findings are not given, the file content is scanned first.
    """
    original = Path(path)
    if original.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '{original.suffix}'. Use .txt or .csv.")
    if not original.is_file():
        raise FileNotFoundError(f"File not found: {original}")
    target = output_path(original)
    if target.exists():
        raise FileExistsError(f"Anonymized copy already exists: {target}")
    try:
        with open(original, "r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8 text: {original}") from exc
    if findings is None:
        findings = scan_text(text)
    with open(target, "x", encoding="utf-8", newline="") as handle:
        handle.write(anonymize_text(text, findings))
    return target
