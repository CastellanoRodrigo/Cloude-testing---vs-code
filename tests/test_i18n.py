"""Pruebas del informe en español y de los botones de la interfaz (solo datos falsos)."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from data_guardian.agent import Agent
from data_guardian.app import process_upload
from data_guardian.classifier import Action, Risk
from data_guardian.i18n import COLUMNS, VALUES_ES, report_rows, text
from data_guardian.scanner import FindingKind
from tests.test_agent import CONTENT, FakeClient

APP = Path(__file__).resolve().parent.parent / "data_guardian" / "app.py"


def make_result() -> tuple:
    report, copy_bytes, copy_name = process_upload("data.txt", CONTENT.encode(), Agent(FakeClient()))
    return "data.txt", report, copy_bytes, copy_name


def test_every_enum_value_has_a_spanish_translation() -> None:
    for enum in (FindingKind, Risk, Action):
        for member in enum:
            assert member.value in VALUES_ES


def test_only_spanish_is_available() -> None:
    assert list(COLUMNS) == ["es"]


def test_rows_are_in_spanish() -> None:
    _, report, _, _ = make_result()
    spanish = report_rows(report, "es")
    assert list(spanish[0]) == list(COLUMNS["es"].values())
    assert spanish[0]["Tipo"] == "correo electrónico"
    assert spanish[1]["Anonimizado"] == "no (conservado)"


def test_rows_contain_only_masked_values() -> None:
    _, report, _, _ = make_result()
    dumped = str(report_rows(report, "es"))
    assert "ana@example.com" not in dumped and "12345678Z" not in dumped


def test_text_fills_in_the_name() -> None:
    assert text("es", "findings", name="a.txt") == "Hallazgos en a.txt"
    assert text("es", "reasons_note") == ""


def test_app_shows_the_report_only_in_spanish() -> None:
    at = AppTest.from_file(str(APP))
    at.session_state["result"] = make_result()
    at.run()
    assert [s.value for s in at.subheader if "data.txt" in s.value] == ["Hallazgos en data.txt"]
    assert len(at.table) == 1
    assert not any(b.key == "translate" for b in at.button)


def test_cancel_clears_the_report() -> None:
    at = AppTest.from_file(str(APP))
    at.session_state["result"] = make_result()
    at.run()
    at.button(key="cancel").click().run()
    assert len(at.table) == 0
    assert "result" not in at.session_state
