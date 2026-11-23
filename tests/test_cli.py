"""Pruebas de la CLI con un cliente falso (sin llamadas reales a la API, solo datos falsos)."""

import os

import pytest
from typer.testing import CliRunner

from data_guardian import cli
from data_guardian.agent import Agent, Report, ReportItem
from data_guardian.classifier import Action, Risk
from data_guardian.history import History
from data_guardian.scanner import FindingKind, mask_value
from tests.test_agent import CONTENT, FAKE_KEY, LEAK, FakeClient

runner = CliRunner()


@pytest.fixture(autouse=True)
def fake_agent(monkeypatch) -> None:
    monkeypatch.setattr(cli, "Agent", lambda **kwargs: Agent(FakeClient(), **kwargs))


def test_scan_prints_masked_report_and_creates_copy(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    result = runner.invoke(cli.app, ["scan", str(original)])
    assert result.exit_code == 0
    assert "data_anonymized.txt" in result.output
    assert "(conservado)" in result.output
    for secret in ("ana@example.com", "12345678Z", FAKE_KEY):
        assert secret not in result.output
    assert (tmp_path / "data_anonymized.txt").exists()


def test_scan_prints_the_audit_verdict(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    result = runner.invoke(cli.app, ["scan", str(original)])
    assert "VERIFICACIÓN: APROBADA (no quedó ningún dato sensible visible)" in result.output


def test_scan_prints_audit_leaks_as_masked_lines(tmp_path, monkeypatch) -> None:
    class LeakyClient(FakeClient):
        audit_findings = [LEAK]

    monkeypatch.setattr(cli, "Agent", lambda **kwargs: Agent(LeakyClient(), **kwargs))
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    result = runner.invoke(cli.app, ["scan", str(original)])
    assert "VERIFICACIÓN: CON FUGAS (1)" in result.output
    assert "0 | línea 1 | teléfono | 11******66 | alto | [PHONE_REDACTED]" in result.output


def test_failed_audit_is_reported_without_hiding_the_findings() -> None:
    items = [
        ReportItem(id=0, kind=FindingKind.EMAIL, line=1, masked="an***om", risk=Risk.MEDIUM,
                   risk_reason="r", action=Action.ANONYMIZE, action_reason="r", anonymized=True),
    ]
    text = cli.format_report(Report(source="x.txt", output="y.txt", items=items, audit_error="El modelo rechazó la solicitud."))
    assert "Auditoría no completada: El modelo rechazó la solicitud." in text and "línea 1" in text


def test_scan_missing_file_shows_clear_error(tmp_path) -> None:
    result = runner.invoke(cli.app, ["scan", str(tmp_path / "missing.txt")])
    assert result.exit_code == 1
    assert "Error: No se encontró el archivo" in result.output


def test_scan_existing_copy_fails(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    (tmp_path / "data_anonymized.txt").write_text("old", encoding="utf-8")
    result = runner.invoke(cli.app, ["scan", str(original)])
    assert result.exit_code == 1
    assert "ya existe" in result.output


def test_scan_no_findings(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text("nada que ocultar aquí", encoding="utf-8")
    result = runner.invoke(cli.app, ["scan", str(original)])
    assert result.exit_code == 0 and "No se encontraron datos sensibles" in result.output


def test_load_env_sets_missing_variables_only(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nFAKE_NEW=abc\nFAKE_OLD=from_file\nFAKE_EMPTY=\n", encoding="utf-8")
    monkeypatch.delenv("FAKE_NEW", raising=False)
    monkeypatch.delenv("FAKE_EMPTY", raising=False)
    monkeypatch.setenv("FAKE_OLD", "from_env")
    cli.load_env(env_file)
    assert os.environ["FAKE_NEW"] == "abc"
    assert os.environ["FAKE_OLD"] == "from_env"
    assert "FAKE_EMPTY" not in os.environ
    monkeypatch.delenv("FAKE_NEW")


def test_load_env_ignores_utf8_bom(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("FAKE_BOM=abc\n", encoding="utf-8-sig")
    monkeypatch.delenv("FAKE_BOM", raising=False)
    cli.load_env(env_file)
    assert os.environ["FAKE_BOM"] == "abc"
    monkeypatch.delenv("FAKE_BOM")


def test_scan_records_the_review_in_the_history(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    assert runner.invoke(cli.app, ["scan", str(original)]).exit_code == 0
    assert [b.files for b in History.default().series("day", 1)] == [1]


def test_failed_scan_records_nothing_in_the_history(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    (tmp_path / "data_anonymized.txt").write_text("old", encoding="utf-8")
    assert runner.invoke(cli.app, ["scan", str(original)]).exit_code == 1
    assert not History.default().path.exists()


def test_report_columns_stay_aligned_with_long_masked_values() -> None:
    long_key = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    text = f"mail ana@example.com key {long_key}\n"
    report = Agent(FakeClient()).run_text_for_test(text) if False else None
    items = [
        ReportItem(id=0, kind=FindingKind.EMAIL, line=1, masked=mask_value("ana@example.com"), risk=Risk.MEDIUM,
                   risk_reason="r", action=Action.ANONYMIZE, action_reason="r", anonymized=True),
        ReportItem(id=1, kind=FindingKind.API_KEY, line=1, masked=mask_value(long_key), risk=Risk.CRITICAL,
                   risk_reason="r", action=Action.ANONYMIZE, action_reason="r", anonymized=True),
    ]
    rows = cli.format_report(Report(source="x.txt", output="y.txt", items=items)).splitlines()[1:3]
    assert len({row.index("riesgo=") for row in rows}) == 1
