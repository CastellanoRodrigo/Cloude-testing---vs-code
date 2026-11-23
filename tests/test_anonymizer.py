"""Pruebas del anonimizador (solo datos falsos)."""

import pytest

from data_guardian.anonymizer import anonymize_file, anonymize_text, output_path
from data_guardian.scanner import scan_text

FAKE_KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
SAMPLE = f"mail ana@example.com id 12345678Z key {FAKE_KEY} end"


def test_anonymize_text_replaces_all_kinds() -> None:
    result = anonymize_text(SAMPLE, scan_text(SAMPLE))
    assert result == "mail [EMAIL_REDACTED] id [DOCUMENT_ID_REDACTED] key [API_KEY_REDACTED] end"


def test_anonymize_text_is_deterministic() -> None:
    findings = scan_text(SAMPLE)
    assert anonymize_text(SAMPLE, findings) == anonymize_text(SAMPLE, findings)


def test_anonymize_text_without_findings() -> None:
    assert anonymize_text("nothing here", []) == "nothing here"


def test_output_path_naming(tmp_path) -> None:
    assert output_path(tmp_path / "data.csv").name == "data_anonymized.csv"


def test_anonymize_file_creates_copy_and_keeps_original(tmp_path) -> None:
    original = tmp_path / "data.csv"
    content = "name,email\r\nAna,ana@example.com\r\n"
    original.write_text(content, encoding="utf-8", newline="")
    copy = anonymize_file(original)
    assert copy.name == "data_anonymized.csv"
    assert copy.read_text(encoding="utf-8", newline="") == "name,email\r\nAna,[EMAIL_REDACTED]\r\n"
    assert original.read_text(encoding="utf-8", newline="") == content


def test_anonymize_file_fails_if_copy_exists(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text("ana@example.com", encoding="utf-8")
    (tmp_path / "data_anonymized.txt").write_text("old", encoding="utf-8")
    with pytest.raises(FileExistsError):
        anonymize_file(original)
    assert (tmp_path / "data_anonymized.txt").read_text(encoding="utf-8") == "old"


def test_anonymize_file_rejects_unsupported_and_missing(tmp_path) -> None:
    with pytest.raises(ValueError, match="no admitido"):
        anonymize_file(tmp_path / "data.pdf")
    with pytest.raises(FileNotFoundError):
        anonymize_file(tmp_path / "missing.txt")
