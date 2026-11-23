"""Pruebas del panel de tokens (solo datos falsos, registro temporal)."""

from datetime import date, datetime

from streamlit.testing.v1 import AppTest

from data_guardian.token_panel import (
    LEVEL_COLORS,
    TOOL_NAMES,
    budget_chart,
    daily_chart,
    tokens_text,
    tool_rows,
)
from data_guardian.usage import BudgetStatus, Level, UsageLog, UsageRecord, by_tool, daily

TODAY = date(2026, 11, 18)


def record(tool: str, tokens_in: int, tokens_out: int, when: datetime) -> UsageRecord:
    return UsageRecord(used_at=when, tool=tool, model="claude-sonnet-5-5", input_tokens=tokens_in, output_tokens=tokens_out)


def seeded(tmp_path) -> UsageLog:
    log = UsageLog(tmp_path / "uso.db")
    log.record(record("report_classification", 700, 100, datetime(2026, 11, 18, 9)))
    log.record(record("report_audit", 100, 100, datetime(2026, 11, 10, 9)))
    return log


def render(log: UsageLog) -> AppTest:
    def app() -> None:
        from datetime import date as _date

        from data_guardian.token_panel import show_token_panel as panel
        from data_guardian.usage import UsageLog as Log
        import os

        panel(Log(os.environ["TEST_USAGE_DB"]), _date(2026, 11, 18))

    return AppTest.from_function(app)


def test_tokens_text_agrupa_miles() -> None:
    assert tokens_text(1234567) == "1 234 567"


def test_barra_usa_el_color_del_nivel() -> None:
    spec = budget_chart(BudgetStatus(used=950, limit=1000, level=Level.CRITICAL)).to_dict()
    colors = [layer["mark"]["color"] for layer in spec["layer"]]
    assert LEVEL_COLORS[Level.CRITICAL] in colors


def test_barra_no_pasa_del_limite() -> None:
    spec = budget_chart(BudgetStatus(used=5000, limit=1000, level=Level.CRITICAL)).to_dict()
    values = list(spec["datasets"].values())[0][0]
    assert values["used"] == 1000


def test_grafico_diario_tiene_entrada_y_salida(tmp_path) -> None:
    series = daily(seeded(tmp_path).records(), 3, TODAY)
    spec = daily_chart(series).to_dict()
    assert spec["encoding"]["color"]["scale"]["domain"] == ["Entrada", "Salida"]


def test_filas_por_herramienta_con_nombres_en_espanol(tmp_path) -> None:
    rows = tool_rows(by_tool(seeded(tmp_path).records()))
    assert rows[0]["Herramienta"] == TOOL_NAMES["report_classification"] == "Clasificar"
    assert rows[0]["Total"] == 800


def test_panel_muestra_presupuesto_y_alerta(tmp_path, monkeypatch) -> None:
    log = seeded(tmp_path)
    monkeypatch.setenv("TOKEN_BUDGET", "1000")
    monkeypatch.setenv("TEST_USAGE_DB", str(log.path))
    at = render(log).run()
    assert not at.exception
    assert [m.value for m in at.metric][:2] == ["800", "1 000"]  # hoy, este mes
    assert any("90 %" in w.value for w in at.error)  # 1000 de 1000 = crítico
    assert any("1 000 de 1 000" in m.value for m in at.markdown)


def test_panel_vacio_avisa(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_USAGE_DB", str(tmp_path / "nada.db"))
    at = render(UsageLog(tmp_path / "nada.db")).run()
    assert not at.exception
    assert any("Aún no hay llamadas" in i.value for i in at.info)


def alert_app(log_path: str) -> AppTest:
    def app() -> None:
        import os
        from datetime import date as _date

        from data_guardian.token_panel import show_budget_alert
        from data_guardian.usage import UsageLog as Log

        show_budget_alert(Log(os.environ["TEST_USAGE_DB"]), _date(2026, 11, 18))

    return AppTest.from_function(app)


def test_aviso_rojo_ambar_y_nada(tmp_path, monkeypatch) -> None:
    log = seeded(tmp_path)  # 800 tokens hoy + 200 el día 10 = 1000
    monkeypatch.setenv("TEST_USAGE_DB", str(log.path))
    monkeypatch.setenv("TOKEN_BUDGET", "1000")
    at = alert_app(str(log.path)).run()
    assert any("90 %" in e.value for e in at.error)
    monkeypatch.setenv("TOKEN_BUDGET", "1300")  # 77 %
    at = alert_app(str(log.path)).run()
    assert any("70 %" in w.value for w in at.warning) and not at.error
    monkeypatch.setenv("TOKEN_BUDGET", "100000")
    at = alert_app(str(log.path)).run()
    assert not at.warning and not at.error


def test_guardar_presupuesto_desde_el_panel(tmp_path, monkeypatch) -> None:
    log = seeded(tmp_path)
    monkeypatch.setenv("TOKEN_BUDGET", "1000")
    monkeypatch.setenv("TEST_USAGE_DB", str(log.path))
    at = render(log).run()
    at.number_input[0].set_value(50_000)
    at.button[0].click().run()
    assert log.saved_budget() == 50_000
    assert any("50 000" in m.value for m in at.markdown)
