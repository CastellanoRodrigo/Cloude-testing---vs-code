"""Dashboard tab: files reviewed per day, week or month, from the local history."""

import altair as alt
import pandas as pd
import streamlit as st

from data_guardian.history import Bucket, History, HistoryError, Period

PERIODS: dict[Period, tuple[str, int]] = {
    "day": ("Daily · Diario", 14),
    "week": ("Weekly · Semanal", 8),
    "month": ("Monthly · Mensual", 6),
}

# Colors first validated on a dark surface; SURFACE is the light panel the charts now sit on.
SURFACE = "#ffffff"
FILES_COLOR = "#3987e5"  # blue means "files" only
KINDS: list[tuple[str, str, str]] = [  # bucket field, display name, color (fixed order)
    ("emails", "Email · Correo", "#d95926"),
    ("document_ids", "ID number · Documento", "#199e70"),
    ("api_keys", "API key · Clave de API", "#c98500"),
]
FILES_TITLE = "Files · Archivos"
PERIOD_TITLE = "Period · Periodo"


def table_rows(series: list[Bucket]) -> list[dict[str, str | int]]:
    """The numbers behind the charts, one row per period."""
    return [
        {
            PERIOD_TITLE: bucket.label,
            FILES_TITLE: bucket.files,
            **{name: getattr(bucket, field) for field, name, _ in KINDS},
            "Anonymized · Anonimizados": bucket.anonymized,
        }
        for bucket in series
    ]


def _describe(series: list[Bucket], period: Period, what: str, value) -> str:
    """Plain-text description of a chart for screen readers."""
    points = ", ".join(f"{bucket.label}: {value(bucket)}" for bucket in series)
    return f"{what} per {period}. {points}"


def files_chart(series: list[Bucket]) -> alt.Chart:
    """Single-series columns: files reviewed per period."""
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
    """Stacked columns: findings per period, split by type, with a 2px gap between segments."""
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
            y=alt.Y("count:Q", title="Findings · Hallazgos", axis=alt.Axis(tickMinStep=1, format="d")),
            color=alt.Color(
                "kind:N",
                scale=alt.Scale(domain=[name for _, name, _ in KINDS], range=[color for _, _, color in KINDS]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            order=alt.Order("order:Q", sort="ascending"),
            tooltip=[
                alt.Tooltip("label:N", title=PERIOD_TITLE),
                alt.Tooltip("kind:N", title="Type · Tipo"),
                alt.Tooltip("count:Q", title="Findings · Hallazgos"),
            ],
        )
        .properties(height=260)
    )


def show_dashboard(history: History) -> None:
    """Render the KPIs, the period selector, both charts and the table view."""
    st.subheader("Dashboard · Panel")
    try:
        today = history.series("day", 1)[0]
        week = history.series("week", 1)[0]
        month = history.series("month", 1)[0]
        columns = st.columns(4)
        columns[0].metric("Today · Hoy", today.files)
        columns[1].metric("This week · Esta semana", week.files)
        columns[2].metric("This month · Este mes", month.files)
        columns[3].metric("Findings this month · Hallazgos este mes", month.findings)
        columns[3].caption(f"{month.anonymized} anonymized · anonimizados")

        period: Period = st.radio(
            "Period · Periodo",
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
        st.info("No files reviewed in this period yet. · Aún no hay archivos revisados en este periodo.")
        return

    st.markdown("**Files reviewed · Archivos revisados**")
    st.altair_chart(
        files_chart(series),
        width="stretch",
        alt=_describe(series, period, "Files reviewed", lambda b: b.files),
    )
    st.markdown("**Findings by type · Hallazgos por tipo**")
    st.altair_chart(
        kinds_chart(series),
        width="stretch",
        alt=_describe(series, period, "Findings by type", lambda b: b.findings),
    )
    with st.expander("Table view · Ver tabla"):
        st.dataframe(table_rows(series), hide_index=True)
