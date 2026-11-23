"""Colores del nivel de riesgo (rojo alto, naranja medio, verde bajo), compartidos por las tablas y el PDF."""

import pandas as pd
from pandas.io.formats.style import Styler

RED = ("#b71c1c", "#fde2e2")  # (texto, tono de fondo)
DARK_RED = ("#7f0000", "#f4bcbc")
ORANGE = ("#b34700", "#ffe5cc")
GREEN = ("#1b5e20", "#dff2e1")

# Nombres en inglés y en español de los niveles, en las tablas del informe y en la de la auditoría.
RISK_COLORS: dict[str, tuple[str, str]] = {
    "high": RED, "alto": RED,
    "critical": DARK_RED, "crítico": DARK_RED,
    "medium": ORANGE, "medio": ORANGE,
    "low": GREEN, "bajo": GREEN,
}


def risk_colors(value: object) -> tuple[str, str] | None:
    """Los colores (texto, fondo) de un nivel de riesgo en cualquiera de los dos idiomas, o None si es desconocido."""
    return RISK_COLORS.get(str(value).strip().lower())


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """Convierte '#rrggbb' en una tupla (r, g, b)."""
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _css(value: object) -> str:
    colors = risk_colors(value)
    return f"color: {colors[0]}; background-color: {colors[1]}; font-weight: bold" if colors else ""


def risk_table(rows: list[dict[str, str | int]], risk_column: str) -> Styler:
    """Una tabla de filas donde la columna de riesgo se muestra en negrita, con el color de su nivel."""
    return pd.DataFrame(rows).style.map(_css, subset=[risk_column])
