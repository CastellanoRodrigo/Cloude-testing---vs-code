"""Pruebas de la exportación a PDF (solo datos falsos)."""

from pathlib import Path

from datetime import datetime

from streamlit.testing.v1 import AppTest

from data_guardian.agent import Agent, Report, ReportItem
from data_guardian.app import process_upload
from data_guardian.classifier import Action, Risk
from data_guardian.pdf_report import build_pdf
from data_guardian.scanner import FindingKind
from tests.test_agent import CONTENT, FAKE_KEY, FakeClient

APP = Path(__file__).resolve().parent.parent / "data_guardian" / "app.py"
MOMENT = datetime(2026, 10, 28, 12, 30)


def make_report() -> Report:
    report, _, _ = process_upload("data.txt", CONTENT.encode(), Agent(FakeClient()))
    return report


def test_pdf_is_valid_and_has_spanish_content() -> None:
    pdf = build_pdf("data.txt", make_report(), generated_at=MOMENT)
    assert pdf.startswith(b"%PDF") and pdf.rstrip().endswith(b"%%EOF")
    for expected in (b"Archivo: data.txt", b"Generado: 2026-10-28 12:30", b"3 hallazgos, 2 anonimizados"):
        assert expected in pdf
    assert b"Hallazgos en data.txt" in pdf
    assert b"Findings" not in pdf and b"Report" not in pdf


def test_pdf_has_no_real_values_only_masked_ones() -> None:
    pdf = build_pdf("data.txt", make_report(), generated_at=MOMENT)
    for secret in ("ana@example.com", "12345678Z", FAKE_KEY):
        assert secret.encode() not in pdf
    assert b"an***********om" in pdf


def test_pdf_columns_and_values_are_in_spanish() -> None:
    pdf = build_pdf("data.txt", make_report(), generated_at=MOMENT)
    assert "Línea".encode("latin-1") in pdf and "correo electrónico".encode("latin-1") in pdf


def test_no_findings_pdf() -> None:
    empty = Report(source="data.txt", output=None, items=[])
    pdf = build_pdf("data.txt", empty, generated_at=MOMENT)
    assert b"No se encontraron datos sensibles en data.txt" in pdf


def test_text_outside_latin1_never_breaks_the_pdf() -> None:
    item = ReportItem(
        id=0,
        kind=FindingKind.EMAIL,
        line=1,
        masked="an***om",
        risk=Risk.HIGH,
        risk_reason="r",
        action=Action.ANONYMIZE,
        action_reason="Parece real — dirección “personal” 日本",
        anonymized=True,
    )
    report = Report(source="x.txt", output=None, items=[item])
    pdf = build_pdf("naïve日.txt", report, generated_at=MOMENT)
    assert pdf.startswith(b"%PDF")
    assert b"Parece real ? direcci" in pdf and b"?personal? ??" in pdf


def test_long_unbroken_value_wraps_without_error() -> None:
    item = ReportItem(
        id=0, kind=FindingKind.API_KEY, line=1, masked="sk" + "*" * 200 + "L2", risk=Risk.CRITICAL,
        risk_reason="r", action=Action.ANONYMIZE, action_reason="x" * 300, anonymized=True,
    )
    pdf = build_pdf("x.txt", Report(source="x.txt", output=None, items=[item]), generated_at=MOMENT)
    assert pdf.startswith(b"%PDF")


def test_app_shows_the_export_button_with_a_result() -> None:
    report, copy_bytes, copy_name = process_upload("data.txt", CONTENT.encode(), Agent(FakeClient()))
    at = AppTest.from_file(str(APP))
    at.session_state["result"] = ("data.txt", report, copy_bytes, copy_name)
    at.run()
    assert not at.exception
    labels = [getattr(el, "proto", None) and getattr(el.proto, "label", "") for el in at.get("download_button")]
    assert "Exportar informe en PDF" in labels
