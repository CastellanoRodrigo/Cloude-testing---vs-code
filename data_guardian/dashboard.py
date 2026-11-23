"""Pestaña del panel: archivos revisados por día, semana o mes, a partir del historial local."""

import altair as alt
import pandas as pd
import streamlit as st

from data_guardian.history import Bucket, History, HistoryError, Period

PERIODS: dict[Period, tuple[str, int]] = {
    "day": ("Diario", 14),
    "week": ("Semanal", 8),
    "month": ("Mensual", 6),
}
PERIOD_NAMES: dict[Period, str] = {"day": "día", "week": "semana", "month": "mes"}

# Colores validados primero sobre una superficie oscura; SURFACE es el panel claro sobre el que ahora se dibujan los gráficos.
SURFACE = "#ffffff"
FILES_COLOR = "#3987e5"  # el azul significa solo "archivos"
KINDS: list[tuple[str, str, str]] = [  # campo del periodo, nombre mostrado, color (orden fijo)
    ("emails", "Correo", "#d95926"),
    ("document_ids", "Documento de identidad", "#199e70"),
    ("api_keys", "Clave de API", "#c98500"),
]
FILES_TITLE = "Archivos"
PERIOD_TITLE = "Periodo"


def table_rows(series: list[Bucket]) -> list[dict[str, str | int]]:
    """Los números detrás de los gráficos, una fila por periodo."""
    return [
        {
            PERIOD_TITLE: bucket.label,
            FILES_TITLE: bucket.files,
            **{name: getattr(bucket, field) for field, name, _ in KINDS},
            "Anonimizados": bucket.anonymized,
        }
        for bucket in series
    ]


def _describe(series: list[Bucket], period: Period, what: str, value) -> str:
    """Descripción en texto plano de un gráfico para lectores de pantalla."""
    points = ", ".join(f"{bucket.label}: {value(bucket)}" for bucket in series)
    return f"{what} por {PERIOD_NAMES[period]}. {points}"


def files_chart(series: list[Bucket]) -> alt.Chart:
    """Columnas de una sola serie: archivos revisados por periodo."""
    frame = pd.DataFrame({"label": [b.label for b in series], "files": [b.files for b in series]})
    return (
        alt.Chart(frame)
        .mark_bar(color=FILES_COLOR, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, width=alt.RelativeBandSize(0.6))
        .encode(
            x=alt.X("label:N", sort=None, title=None, axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("files:Q", title=FILES_TITLE, axis=alt.Axis(tickMinStep=1, format="d")),
            tooltip=[alt.Tooltip("label:N", title=PERIOD_TITLE), alt.Tooltip("files:Q", title=FILES_TITLE)],
        )
        .properties(height=260)
    )


def kinds_chart(series: list[Bucket]) -> alt.Chart:
    """Columnas apiladas: hallazgos por periodo, divididos por tipo, con 2 px de separación entre segmentos."""
    frame = pd.DataFrame(
        [
            {"label": bucket.label, "kind": name, "count": getattr(bucket, field), "order": index}
            for bucket in series
            for index, (field, name, _) in enumerate(KINDS)
        ]
    )
    return (
        alt.Chart(frame)
        .mark_bar(stroke=SURFACE, strokeWidth=2, width=alt.RelativeBandSize(0.6))
        .encode(
            x=alt.X("label:N", sort=None, title=None, axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("count:Q", title="Hallazgos", axis=alt.Axis(tickMinStep=1, format="d")),
            color=alt.Color(
                "kind:N",
                scale=alt.Scale(domain=[name for _, name, _ in KINDS], range=[color for _, _, color in KINDS]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            order=alt.Order("order:Q", sort="ascending"),
            tooltip=[
                alt.Tooltip("label:N", title=PERIOD_TITLE),
                alt.Tooltip("kind:N", title="Tipo"),
                alt.Tooltip("count:Q", title="Hallazgos"),
            ],
        )
        .properties(height=260)
    )


def show_dashboard(history: History) -> None:
    """Muestra los indicadores, el selector de periodo, ambos gráficos y la vista en tabla."""
    st.subheader("Panel")
    try:
        today = history.series("day", 1)[0]
        week = history.series("week", 1)[0]
        month = history.series("month", 1)[0]
        columns = st.columns(4)
        columns[0].metric("Hoy", today.files)
        columns[1].metric("Esta semana", week.files)
        columns[2].metric("Este mes", month.files)
        columns[3].metric("Hallazgos este mes", month.findings)
        columns[3].caption(f"{month.anonymized} anonimizados")

        period: Period = st.radio(
            "Periodo",
            list(PERIODS),
            format_func=lambda key: PERIODS[key][0],
            horizontal=True,
            key="dashboard_period",
            label_visibility="collapsed",
        )
        series = history.series(period, PERIODS[period][1])
    except HistoryError as exc:
        st.error(str(exc))
        return

    if not any(bucket.files for bucket in series):
        st.info("Aún no hay archivos revisados en este periodo.")
        return

    st.markdown("**Archivos revisados**")
    st.altair_chart(
        files_chart(series),
        width="stretch",
        alt=_describe(series, period, "Archivos revisados", lambda b: b.files),
    )
    st.markdown("**Hallazgos por tipo**")
    st.altair_chart(
        kinds_chart(series),
        width="stretch",
        alt=_describe(series, period, "Hallazgos por tipo", lambda b: b.findings),
    )
    with st.expander("Ver tabla"):
        st.dataframe(table_rows(series), hide_index=True)
