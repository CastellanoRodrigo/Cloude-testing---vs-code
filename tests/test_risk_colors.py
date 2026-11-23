"""Pruebas de los colores de riesgo en las tablas y en el PDF (solo datos falsos)."""

from data_guardian.agent import Report, ReportItem
from data_guardian.classifier import Action, Risk
from data_guardian.pdf_report import build_pdf
from data_guardian.risk_colors import DARK_RED, GREEN, ORANGE, RED, hex_to_rgb, risk_colors, risk_table
from data_guardian.scanner import FindingKind


def test_levels_have_the_requested_colors_by_internal_or_spanish_name() -> None:
    assert risk_colors("high") == risk_colors("alto") == RED
    assert risk_colors("medium") == risk_colors("medio") == ORANGE
    assert risk_colors("low") == risk_colors("bajo") == GREEN
    assert risk_colors("critical") == risk_colors("crítico") == DARK_RED
    assert risk_colors("unknown") is None and RED != DARK_RED


def test_table_colors_only_the_risk_column() -> None:
    rows = [{"Line": 1, "Risk": "high"}, {"Line": 2, "Risk": "medium"}, {"Line": 3, "Risk": "low"}]
    html = risk_table(rows, "Risk").to_html()
    for text_color, background in (RED, ORANGE, GREEN):
        assert f"color: {text_color}" in html and f"background-color: {background}" in html
    assert html.count("font-weight: bold") == 3  # the Line column is not styled


def test_table_leaves_unknown_levels_unstyled() -> None:
    assert "font-weight" not in risk_table([{"Risk": "n/a"}], "Risk").to_html()


def item(risk: Risk) -> ReportItem:
    return ReportItem(id=0, kind=FindingKind.EMAIL, line=1, masked="an***om", risk=risk, risk_reason="r",
                      action=Action.ANONYMIZE, action_reason="r", anonymized=True)


def color_op(color: str) -> bytes:
    parts = (f"{value / 255:.4f}".rstrip("0").rstrip(".") or "0" for value in hex_to_rgb(color))  # fpdf trims zeros
    return f"{' '.join(parts)} rg".encode()


def test_pdf_colors_the_risk_cell_of_each_level() -> None:
    for risk, (text_color, background) in ((Risk.HIGH, RED), (Risk.MEDIUM, ORANGE), (Risk.LOW, GREEN),
                                           (Risk.CRITICAL, DARK_RED)):
        pdf = build_pdf("x.txt", Report(source="x.txt", output=None, items=[item(risk)]))
        assert color_op(text_color) in pdf and color_op(background) in pdf


def test_pdf_colors_the_risk_in_the_spanish_table() -> None:
    pdf = build_pdf("x.txt", Report(source="x.txt", output=None, items=[item(Risk.LOW)]))
    assert color_op(GREEN[0]) in pdf and b"bajo" in pdf
