"""Sección del panel con el uso de tokens de la API: presupuesto del mes, consumo diario y detalle por herramienta."""

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from data_guardian.history import HistoryError
from data_guardian.usage import (
    BudgetStatus,
    Level,
    UsageLog,
    UsageSummary,
    budget_level,
    budget_limit,
    by_tool,
    daily,
    month_status,
    summarize,
)

DAYS = 14
INPUT_COLOR = "#7a5cc8"  # entrada (violeta) y salida (verde azulado): distintos del azul de "archivos" y de los tipos de hallazgo
OUTPUT_COLOR = "#0d9aa8"
TRACK_COLOR = "#e3e6ea"  # fondo de la barra del presupuesto
LEVEL_COLORS: dict[Level, str] = {Level.OK: "#199e70", Level.WARNING: "#c98500", Level.CRITICAL: "#b71c1c"}
LEVEL_TEXT: dict[Level, str] = {
    Level.OK: "Dentro del presupuesto.",
    Level.WARNING: "Atención: ya se usó el 70 % o más del presupuesto del mes.",
    Level.CRITICAL: "Alerta: ya se usó el 90 % o más del presupuesto del mes.",
}
TOOL_NAMES: dict[str, str] = {
    "report_classification": "Clasificar",
    "report_recommendations": "Recomendar",
    "report_audit": "Auditar",
}


def tokens_text(value: int) -> str:
    """Un número de tokens con separador de miles: 1 234 567."""
    return f"{value:,}".replace(",", " ")


def budget_chart(status: BudgetStatus) -> alt.Chart:
    """Barra horizontal: lo usado (del color del nivel) sobre el fondo gris del presupuesto completo."""
    shown = min(status.used, status.limit)
    frame = pd.DataFrame({"used": [shown], "limit": [status.limit]})
    scale = alt.Scale(domain=[0, status.limit])
    track = (
        alt.Chart(frame)
        .mark_bar(color=TRACK_COLOR, cornerRadius=6, height=26)
        .encode(x=alt.X("limit:Q", scale=scale, title=None, axis=alt.Axis(format="~s")))
    )
    used = (
        alt.Chart(frame)
        .mark_bar(color=LEVEL_COLORS[status.level], cornerRadius=6, height=26)
        .encode(
            x=alt.X("used:Q", scale=scale, title=None),
            tooltip=[alt.Tooltip("used:Q", title="Tokens usados", format=",")],
        )
    )
    return (track + used).properties(height=60)


def daily_chart(series: list[tuple[date, UsageSummary]]) -> alt.Chart:
    """Columnas apiladas: tokens de entrada y de salida por día."""
    frame = pd.DataFrame(
        [
            {"day": day.isoformat(), "kind": name, "tokens": value, "order": index}
            for day, summary in series
            for index, (name, value) in enumerate(
                (("Entrada", summary.input_tokens), ("Salida", summary.output_tokens))
            )
        ]
    )
    return (
        alt.Chart(frame)
        .mark_bar(stroke="#ffffff", strokeWidth=2, width=alt.RelativeBandSize(0.6))
        .encode(
            x=alt.X("day:N", sort=None, title=None, axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("tokens:Q", title="Tokens", axis=alt.Axis(format="~s")),
            color=alt.Color(
                "kind:N",
                scale=alt.Scale(domain=["Entrada", "Salida"], range=[INPUT_COLOR, OUTPUT_COLOR]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            order=alt.Order("order:Q", sort="ascending"),
            tooltip=[
                alt.Tooltip("day:N", title="Día"),
                alt.Tooltip("kind:N", title="Tipo"),
                alt.Tooltip("tokens:Q", title="Tokens", format=","),
            ],
        )
        .properties(height=260)
    )


def tool_rows(summaries: dict[str, UsageSummary]) -> list[dict[str, str | int | float]]:
    """Una fila por herramienta con sus llamadas, tokens y costo estimado."""
    return [
        {
            "Herramienta": TOOL_NAMES.get(tool, tool),
            "Llamadas": summary.calls,
            "Entrada": summary.input_tokens,
            "Salida": summary.output_tokens,
            "Total": summary.total_tokens,
            "Costo estimado (USD)": round(summary.cost, 4),
        }
        for tool, summary in summaries.items()
    ]


def _describe(series: list[tuple[date, UsageSummary]]) -> str:
    """Descripción en texto plano del gráfico diario para lectores de pantalla."""
    points = ", ".join(f"{day.isoformat()}: {summary.total_tokens}" for day, summary in series)
    return f"Tokens por día. {points}"


def show_budget_alert(log: UsageLog, today: date | None = None) -> None:
    """Aviso corto (ámbar o rojo) si el mes ya gastó el 70 % o el 90 % del presupuesto; nada si está dentro."""
    try:
        status = month_status(log, today)
    except HistoryError:
        return
    if status.level is Level.OK:
        return
    message = f"{LEVEL_TEXT[status.level]} Van {tokens_text(status.used)} de {tokens_text(status.limit)} tokens."
    (st.error if status.level is Level.CRITICAL else st.warning)(message)


def show_token_panel(log: UsageLog, today: date | None = None) -> None:
    """Muestra el presupuesto del mes, los indicadores, el gráfico diario y el detalle por herramienta."""
    st.subheader("Uso de tokens")
    day = today or date.today()
    try:
        first = day.replace(day=1)
        loaded = log.records(since=min(first, day - timedelta(days=DAYS - 1)))
    except HistoryError as exc:
        st.error(str(exc))
        return

    records = [r for r in loaded if r.used_at.date() >= first]
    recent = [r for r in records if r.used_at.date() == day]
    series = daily(loaded, DAYS, day)
    month = summarize(records)
    limit = budget_limit(log)
    status = BudgetStatus(used=month.total_tokens, limit=limit, level=budget_level(month.total_tokens, limit))
    st.markdown(
        f"**Presupuesto del mes:** {tokens_text(status.used)} de {tokens_text(status.limit)} tokens "
        f"({status.fraction:.0%})"
    )
    st.altair_chart(
        budget_chart(status),
        width="stretch",
        alt=f"Presupuesto del mes: {status.used} de {status.limit} tokens usados.",
    )
    {Level.OK: st.success, Level.WARNING: st.warning, Level.CRITICAL: st.error}[status.level](LEVEL_TEXT[status.level])

    with st.expander("Cambiar presupuesto mensual"):
        with st.form("token_budget_form"):
            new_limit = st.number_input("Tokens por mes", min_value=1000, step=10_000, value=max(limit, 1000))
            if st.form_submit_button("Guardar presupuesto"):
                if log.save_budget(int(new_limit)):
                    st.rerun()
                st.error("No se pudo guardar el presupuesto.")

    columns = st.columns(4)
    columns[0].metric("Tokens hoy", tokens_text(summarize(recent).total_tokens))
    columns[1].metric("Tokens este mes", tokens_text(month.total_tokens))
    columns[2].metric("Llamadas este mes", month.calls)
    columns[3].metric("Costo estimado del mes", f"${month.cost:.2f}")
    columns[3].caption("Estimación con precios aproximados")

    if not records and not any(summary.calls for _, summary in series):
        st.info("Aún no hay llamadas a la API registradas.")
        return
    st.markdown("**Tokens por día**")
    st.altair_chart(daily_chart(series), width="stretch", alt=_describe(series))
    with st.expander("Ver detalle por herramienta"):
        st.dataframe(tool_rows(by_tool(records)), hide_index=True)
