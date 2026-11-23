"""Pruebas de la app de Streamlit (cliente falso, solo datos falsos)."""

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from data_guardian.agent import Agent
import pytest

from data_guardian import app, benchmark
from data_guardian.benchmark import BenchmarkResult, CaseResult, Score
from data_guardian.classifier import ClassifierError
from data_guardian.app import process_upload, resolve_input
from tests.test_agent import CONTENT, FAKE_KEY, FakeClient

APP = Path(__file__).resolve().parent.parent / "data_guardian" / "app.py"


def test_process_upload_returns_anonymized_copy() -> None:
    report, copy_bytes, copy_name = process_upload("data.txt", CONTENT.encode(), Agent(FakeClient()))
    assert copy_name == "data_anonymized.txt"
    assert copy_bytes is not None
    assert copy_bytes.decode() == "mail [EMAIL_REDACTED] id 12345678Z key [API_KEY_REDACTED]\n"
    dumped = json.dumps(report.model_dump(mode="json"))
    for secret in ("ana@example.com", FAKE_KEY):
        assert secret not in dumped


def test_process_upload_without_findings() -> None:
    report, copy_bytes, copy_name = process_upload("data.txt", "nada sensible aquí".encode(), Agent(FakeClient()))
    assert report.items == [] and copy_bytes is None and copy_name is None


def test_process_upload_strips_directories_from_name() -> None:
    _, _, copy_name = process_upload("../../evil.txt", CONTENT.encode(), Agent(FakeClient()))
    assert copy_name == "evil_anonymized.txt"


def _audit_script() -> None:
    """Script para AppTest: muestra un resultado cuya auditoría encontró una fuga."""
    from data_guardian.agent import Agent
    from data_guardian.app import process_upload, show_result
    from tests.test_agent import CONTENT, LEAK, FakeClient

    class LeakyClient(FakeClient):
        audit_findings = [LEAK]

    report, copy_bytes, copy_name = process_upload("data.txt", CONTENT.encode(), Agent(LeakyClient()))
    show_result("data.txt", report, copy_bytes, copy_name)


def test_result_shows_the_audit_verdict_and_masked_leaks() -> None:
    at = AppTest.from_function(_audit_script, default_timeout=30).run()
    assert not at.exception
    assert [e.value for e in at.error] == ["VERIFICACIÓN: CON FUGAS (1)"]
    tables = [t.value for t in at.table]
    assert any("11******66" in t.to_string() for t in tables)


def test_result_shows_an_approved_audit() -> None:
    def script() -> None:
        from data_guardian.agent import Agent
        from data_guardian.app import process_upload, show_result
        from tests.test_agent import CONTENT, FakeClient

        report, copy_bytes, copy_name = process_upload("data.txt", CONTENT.encode(), Agent(FakeClient()))
        show_result("data.txt", report, copy_bytes, copy_name)

    at = AppTest.from_function(script, default_timeout=30).run()
    assert [s.value for s in at.success] == ["VERIFICACIÓN: APROBADA (no quedó ningún dato sensible visible)"]


def _fake_result() -> BenchmarkResult:
    rules = Score(tp=9, fp=3, fn=11)
    ai = Score(tp=20)
    case = CaseResult(name="people", rules=rules, ai=ai, injection=True, audit_flagged_leaks=True)
    return BenchmarkResult(cases=[case])


def test_comparison_tab_runs_the_benchmark_and_shows_the_verdict(monkeypatch) -> None:
    monkeypatch.setattr(benchmark, "run_benchmark", _fake_result)
    at = AppTest.from_file(str(APP)).run()
    at.button(key="run_benchmark").click().run()
    assert not at.exception
    successes = [s.value for s in at.success if s.value != "Dentro del presupuesto."]  # sin el aviso del panel de tokens
    assert successes[0].startswith("La IA aporta en este conjunto de datos")
    rows = at.table[-1].value
    assert list(rows["Archivo"]) == ["people", "TOTAL"] and rows["Recall (IA)"][1] == "100%"
    assert "se trataron como datos" in successes[1]
    assert any("no puede distinguir los señuelos" in c.value for c in at.caption)


def test_comparison_tab_shows_a_clear_error_when_the_ai_step_fails(monkeypatch) -> None:
    def fail() -> BenchmarkResult:
        raise ClassifierError("No se pudo conectar con la API de Claude. Revisa tu red.")

    monkeypatch.setattr(benchmark, "run_benchmark", fail)
    at = AppTest.from_file(str(APP)).run()
    at.button(key="run_benchmark").click().run()
    assert at.error[0].value == "No se pudo conectar con la API de Claude. Revisa tu red."


def test_app_renders_without_errors() -> None:
    at = AppTest.from_file(str(APP)).run()
    assert not at.exception
    assert at.title[0].value == "AI Data Guardian Lite"


def test_app_shows_run_parameters_card() -> None:
    at = AppTest.from_file(str(APP)).run()
    assert at.subheader[0].value == "Parámetros de ejecución"
    assert at.text_input(key="url_0").placeholder == "Escribe la URL del archivo..."
    assert [b.label for b in at.button] == ["Cancelar", "Ejecutar", "Guardar presupuesto", "Ejecutar comparación"]


def test_execute_without_input_shows_clear_error() -> None:
    at = AppTest.from_file(str(APP)).run()
    at.button(key="execute").click().run()
    assert at.error[0].value == "Escribe la URL de un archivo o adjunta un archivo TXT, CSV, PDF o imagen."


def test_execute_with_http_url_is_refused_without_network() -> None:
    at = AppTest.from_file(str(APP)).run()
    at.text_input(key="url_0").set_value("http://files.example.com/a.txt")
    at.button(key="execute").click().run()
    assert at.error[0].value == "Solo se permiten URL https."


def test_cancel_clears_the_url_and_the_error() -> None:
    at = AppTest.from_file(str(APP)).run()
    at.text_input(key="url_0").set_value("http://files.example.com/a.txt")
    at.button(key="execute").click().run()
    at.button(key="cancel").click().run()
    assert not at.error
    assert at.text_input(key="url_1").value == ""


def test_resolve_input_uses_the_upload() -> None:
    assert resolve_input("  ", ("a.txt", b"x")) == ("a.txt", b"x")


def test_resolve_input_downloads_the_url(monkeypatch) -> None:
    monkeypatch.setattr(app, "fetch_url", lambda url: ("remote.csv", b"y"))
    assert resolve_input(" https://files.example.com/remote.csv ", None) == ("remote.csv", b"y")


def test_resolve_input_requires_exactly_one_source(monkeypatch) -> None:
    monkeypatch.setattr(app, "fetch_url", lambda url: pytest.fail("must not download"))
    with pytest.raises(ValueError, match="no ambos"):
        resolve_input("https://files.example.com/a.txt", ("a.txt", b"x"))
    with pytest.raises(ValueError, match="Escribe la URL"):
        resolve_input("", None)


def test_resolve_input_rejects_a_large_upload() -> None:
    with pytest.raises(ValueError, match="supera los 10 MB"):
        resolve_input("", ("big.txt", b"x" * (10 * 1024 * 1024 + 1)))
    assert resolve_input("", ("ok.txt", b"x" * (10 * 1024 * 1024))) == ("ok.txt", b"x" * (10 * 1024 * 1024))
