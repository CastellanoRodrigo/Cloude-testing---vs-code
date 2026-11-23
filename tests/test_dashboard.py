"""Pruebas del panel (solo datos falsos, historial temporal)."""

from datetime import date, datetime
from pathlib import Path

from streamlit.testing.v1 import AppTest

from data_guardian.agent import Agent
from data_guardian.dashboard import (
    FILES_COLOR,
    KINDS,
    SURFACE,
    files_chart,
    kinds_chart,
    table_rows,
)
from data_guardian.history import History
from tests.test_agent import CONTENT, FakeClient

TODAY = date(2026, 10, 28)
APP = Path(__file__).resolve().parent.parent / "data_guardian" / "app.py"


def seed(history: History, tmp_path, count: int = 2) -> None:
    """Registra `count` revisiones de un archivo con 3 hallazgos (2 anonimizados), a la fecha de hoy."""
    source = tmp_path / "seed.txt"
    source.write_text(CONTENT, encoding="utf-8")
    report = Agent(FakeClient()).run(source)
    for _ in range(count):
        history.record(report, ".txt", datetime.now())


def run_app() -> AppTest:
    return AppTest.from_file(str(APP)).run()


def test_palette_is_the_validated_one() -> None:
    assert FILES_COLOR == "#3987e5" and SURFACE == "#ffffff"
    assert [color for _, _, color in KINDS] == ["#d95926", "#199e70", "#c98500"]
    assert FILES_COLOR not in [color for _, _, color in KINDS]  # blue means files only


def test_table_rows_hold_the_numbers_behind_the_charts(tmp_path) -> None:
    history = History(tmp_path / "h.db")
    seed(history, tmp_path, count=2)
    series = history.series("day", 2)
    rows = table_rows(series)
    assert [row["Archivos"] for row in rows] == [0, 2]
    assert rows[1]["Correo"] == 2 and rows[1]["Anonimizados"] == 4


def test_files_chart_is_one_series_in_blue_with_integer_ticks(tmp_path) -> None:
    history = History(tmp_path / "h.db")
    spec = files_chart(history.series("day", 3, TODAY)).to_dict()
    assert spec["mark"]["color"] == FILES_COLOR
    assert spec["encoding"]["y"]["axis"]["tickMinStep"] == 1
    assert "color" not in spec["encoding"]


def test_kinds_chart_stacks_three_types_in_fixed_order_with_a_gap(tmp_path) -> None:
    history = History(tmp_path / "h.db")
    spec = kinds_chart(history.series("week", 4, TODAY)).to_dict()
    scale = spec["encoding"]["color"]["scale"]
    assert scale["domain"] == [name for _, name, _ in KINDS]
    assert scale["range"] == [color for _, _, color in KINDS]
    assert spec["mark"]["stroke"] == SURFACE and spec["mark"]["strokeWidth"] == 2
    assert spec["encoding"]["order"]["field"] == "order"


def test_app_has_three_tabs() -> None:
    at = run_app()
    assert not at.exception
    assert [tab.label for tab in at.tabs] == ["Revisar", "Panel", "Comparación"]


def test_empty_history_shows_zeros_and_a_notice() -> None:
    at = run_app()
    assert [m.value for m in at.metric][:4] == ["0", "0", "0", "0"]  # los 4 primeros son del panel de archivos
    assert any("Aún no hay archivos revisados" in i.value for i in at.info)
    assert not at.exception


def test_kpis_and_period_selector_with_data(tmp_path) -> None:
    seed(History.default(), tmp_path, count=2)
    at = run_app()
    assert not at.exception
    assert [(m.label, m.value) for m in at.metric][:4] == [
        ("Hoy", "2"),
        ("Esta semana", "2"),
        ("Este mes", "2"),
        ("Hallazgos este mes", "6"),
    ]
    assert any("4 anonimizados" in c.value for c in at.caption)

    for choice in ("week", "month", "day"):
        at.radio(key="dashboard_period").set_value(choice).run()
        assert not at.exception


def test_corrupt_history_shows_a_clear_error() -> None:
    path = History.default().path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"esto no es una base de datos sqlite" * 50)
    at = run_app()
    assert any("No se pudo leer el archivo de historial" in e.value for e in at.error)
