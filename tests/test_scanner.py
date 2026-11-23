"""Pruebas del escáner (solo datos falsos)."""

import pytest

from data_guardian.scanner import (
    SUPPORTED_EXTENSIONS,
    FindingKind,
    mask_value,
    scan_file,
    scan_text,
)

FAKE_KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
FAKE_AWS = "AKIA" + "ABCDEFGH12345678"


def test_finds_email() -> None:
    [finding] = scan_text("contact: jane.doe@example.com today")
    assert finding.kind == FindingKind.EMAIL
    assert finding.value == "jane.doe@example.com"


def test_finds_document_ids() -> None:
    found = scan_text("DNI 12345678Z, NIE X1234567L, SSN 123-45-6789")
    assert [f.kind for f in found] == [FindingKind.DOCUMENT_ID] * 3


def test_finds_api_keys() -> None:
    found = scan_text(f"key={FAKE_KEY} aws={FAKE_AWS}")
    assert [f.kind for f in found] == [FindingKind.API_KEY] * 2


def test_ignores_clean_text() -> None:
    assert scan_text("nothing sensitive here, just 12345 and a@b") == []


def test_offsets_slice_the_whole_text() -> None:
    text = "mail ana@example.com id 12345678Z"
    for finding in scan_text(text):
        assert text[finding.start : finding.end] == finding.value


def test_findings_are_ordered_by_position_with_line_numbers() -> None:
    text = f"first\nmail ana@example.com\nid 12345678Z key {FAKE_KEY}\n"
    found = scan_text(text)
    assert [f.kind for f in found] == [FindingKind.EMAIL, FindingKind.DOCUMENT_ID, FindingKind.API_KEY]
    assert [f.line for f in found] == [2, 3, 3]


def test_overlap_keeps_a_single_finding() -> None:
    [finding] = scan_text(f"{FAKE_KEY}@example.com")
    assert finding.kind == FindingKind.API_KEY


def test_mask_value_keeps_first_and_last_two() -> None:
    assert mask_value("ana@example.com") == "an***********om"
    assert mask_value("abcd") == "****"


def test_repr_hides_value() -> None:
    [finding] = scan_text("ana@example.com")
    assert "ana@example.com" not in repr(finding)


def test_kind_values_are_the_labels_sent_to_the_api() -> None:
    assert [k.value for k in FindingKind] == ["email", "document_id", "api_key"]


def test_supported_extensions() -> None:
    assert SUPPORTED_EXTENSIONS == {".txt", ".csv"}


def test_scan_txt_file(tmp_path) -> None:
    file = tmp_path / "notes.txt"
    file.write_text("first\nmail jane@example.com\n", encoding="utf-8")
    [finding] = scan_file(file)
    assert finding.line == 2 and finding.value == "jane@example.com"


def test_scan_csv_file_with_quoted_cell(tmp_path) -> None:
    file = tmp_path / "data.csv"
    file.write_text('name,contact\r\nJane,"jane@example.com"\r\n', encoding="utf-8", newline="")
    [finding] = scan_file(file)
    assert finding.line == 2 and finding.value == "jane@example.com"


def test_scan_does_not_modify_original(tmp_path) -> None:
    file = tmp_path / "notes.txt"
    file.write_text("jane@example.com", encoding="utf-8")
    scan_file(file)
    assert file.read_text(encoding="utf-8") == "jane@example.com"


def test_unsupported_extension(tmp_path) -> None:
    with pytest.raises(ValueError, match="no admitido"):
        scan_file(tmp_path / "notes.pdf")


def test_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="No se encontró el archivo"):
        scan_file(tmp_path / "missing.txt")


def test_invalid_utf8_error(tmp_path) -> None:
    file = tmp_path / "bad.txt"
    file.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ValueError, match="UTF-8 válido"):
        scan_file(file)
