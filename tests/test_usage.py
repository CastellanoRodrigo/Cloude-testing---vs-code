"""Pruebas del registro de uso de tokens (solo datos falsos)."""

from datetime import date, datetime
from types import SimpleNamespace

from data_guardian.usage import (
    Level,
    UsageLog,
    UsageRecord,
    budget_level,
    budget_limit,
    by_tool,
    daily,
    estimate_cost,
    month_status,
    record_response_usage,
    summarize,
)


def make_record(tool: str = "report_classification", tokens_in: int = 100, tokens_out: int = 50) -> UsageRecord:
    return UsageRecord(
        used_at=datetime(2026, 11, 18, 10, 0, 0),
        tool=tool,
        model="claude-sonnet-5-5",
        input_tokens=tokens_in,
        output_tokens=tokens_out,
    )


def test_estimate_cost_usa_precio_por_millon() -> None:
    assert estimate_cost("claude-sonnet-5-5", 1_000_000, 1_000_000) == 18.0


def test_modelo_desconocido_usa_precio_por_defecto() -> None:
    assert estimate_cost("otro-modelo", 1_000_000, 0) == 3.0


def test_guardar_y_leer(tmp_path) -> None:
    log = UsageLog(tmp_path / "uso.db")
    assert log.record(make_record(tokens_in=10, tokens_out=5))
    assert log.record(make_record(tool="report_audit", tokens_in=20, tokens_out=1))
    records = log.records()
    assert [r.tool for r in records] == ["report_classification", "report_audit"]
    assert records[0].total_tokens == 15


def test_filtro_por_fecha(tmp_path) -> None:
    log = UsageLog(tmp_path / "uso.db")
    log.record(make_record())
    assert log.records(since=date(2026, 11, 19)) == []
    assert len(log.records(since=date(2026, 11, 18))) == 1


def test_registro_inexistente_devuelve_vacio(tmp_path) -> None:
    assert UsageLog(tmp_path / "no_existe.db").records() == []


def test_guardar_en_ruta_invalida_devuelve_false(tmp_path) -> None:
    blocker = tmp_path / "archivo"
    blocker.write_text("x")
    assert UsageLog(blocker / "uso.db").record(make_record()) is False


def test_respuesta_con_uso_se_registra(tmp_path) -> None:
    log = UsageLog(tmp_path / "uso.db")
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=7, output_tokens=3))
    assert record_response_usage("report_audit", "claude-sonnet-5-5", response, log)
    assert log.records()[0].total_tokens == 10


def test_respuesta_sin_uso_se_ignora(tmp_path) -> None:
    log = UsageLog(tmp_path / "uso.db")
    assert record_response_usage("x", "m", SimpleNamespace(), log) is False
    assert log.records() == []


def test_llamada_del_clasificador_queda_registrada() -> None:
    from data_guardian import classifier

    response = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="t", input={"ok": 1})],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=12, output_tokens=4),
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: response))
    assert classifier._call_tool(client, "sys", {"name": "t"}, "{}") == {"ok": 1}
    [record] = UsageLog.default().records()
    assert (record.tool, record.input_tokens, record.output_tokens) == ("t", 12, 4)


def test_presupuesto_por_defecto_y_variable(monkeypatch) -> None:
    monkeypatch.delenv("TOKEN_BUDGET", raising=False)
    assert budget_limit() == 1_000_000
    monkeypatch.setenv("TOKEN_BUDGET", "5000")
    assert budget_limit() == 5000
    for bad in ("abc", "0", "-5", ""):
        monkeypatch.setenv("TOKEN_BUDGET", bad)
        assert budget_limit() == 1_000_000


def test_niveles_de_alerta() -> None:
    assert budget_level(699, 1000) is Level.OK
    assert budget_level(700, 1000) is Level.WARNING
    assert budget_level(899, 1000) is Level.WARNING
    assert budget_level(900, 1000) is Level.CRITICAL
    assert budget_level(2000, 1000) is Level.CRITICAL


def test_resumen_y_por_herramienta() -> None:
    records = [make_record("a", 10, 5), make_record("b", 100, 50), make_record("a", 10, 5)]
    total = summarize(records)
    assert (total.calls, total.total_tokens) == (3, 180)
    grouped = by_tool(records)
    assert list(grouped) == ["b", "a"]  # de mayor a menor
    assert grouped["a"].calls == 2


def test_serie_diaria_con_ceros() -> None:
    series = daily([make_record(tokens_in=10, tokens_out=0)], days=3, today=date(2026, 11, 19))
    assert [day.isoformat() for day, _ in series] == ["2026-11-17", "2026-11-18", "2026-11-19"]
    assert [s.total_tokens for _, s in series] == [0, 10, 0]


def test_estado_del_mes_ignora_meses_anteriores(tmp_path) -> None:
    log = UsageLog(tmp_path / "uso.db")
    log.record(make_record(tokens_in=800, tokens_out=0))
    log.record(make_record(tokens_in=500, tokens_out=0).model_copy(update={"used_at": datetime(2026, 10, 30)}))
    status = month_status(log, today=date(2026, 11, 20), limit=1000)
    assert (status.used, status.level, status.fraction) == (800, Level.WARNING, 0.8)


def test_presupuesto_guardado_gana_a_la_variable(tmp_path, monkeypatch) -> None:
    log = UsageLog(tmp_path / "uso.db")
    monkeypatch.setenv("TOKEN_BUDGET", "5000")
    assert budget_limit(log) == 5000
    assert log.save_budget(2000) and log.saved_budget() == 2000
    assert budget_limit(log) == 2000
    assert log.save_budget(3000) and budget_limit(log) == 3000  # se reemplaza, no se duplica


def test_presupuesto_invalido_no_se_guarda(tmp_path) -> None:
    log = UsageLog(tmp_path / "uso.db")
    assert log.save_budget(0) is False and log.saved_budget() is None
    assert UsageLog(tmp_path / "no_existe.db").saved_budget() is None
