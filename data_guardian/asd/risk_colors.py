"""Colors for the risk level (red high, orange medium, green low), shared by the tables and the PDF."""

import pandas as pd
from pandas.io.formats.style import Styler

RED = ("#b71c1c", "#fde2e2")  # (text, background tint)
DARK_RED = ("#7f0000", "#f4bcbc")
ORANGE = ("#b34700", "#ffe5cc")
GREEN = ("#1b5e20", "#dff2e1")

# English and Spanish names of the levels, in the report tables and in the audit table.
RISK_COLORS: dict[str, tuple[str, str]] = {
    "high": RED, "alto": RED,
    "critical": DARK_RED, "crítico": DARK_RED,
    "medium": ORANGE, "medio": ORANGE,
    "low": GREEN, "bajo": GREEN,
}


def risk_colors(value: object) -> tuple[str, str] | None:
    """The (text, background) colors for a risk level in either language, or None when unknown."""
    return RISK_COLORS.get(str(value).strip().lower())


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """Convert '#rrggbb' to an (r, g, b) tuple."""
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _css(value: object) -> str:
    colors = risk_colors(value)
    return f"color: {colors[0]}; background-color: {colors[1]}; font-weight: bold" if colors else ""


def risk_table(rows: list[dict[str, str | int]], risk_column: str) -> Styler:
    """A table of rows where the risk column is shown in bold, in the color of its level."""
    return pd.DataFrame(rows).style.map(_css, subset=[risk_column])
