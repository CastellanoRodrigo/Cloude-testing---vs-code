"""Exportación del informe a PDF. Solo incluye valores enmascarados."""

from datetime import datetime

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

from data_guardian.agent import Report
from data_guardian.i18n import Lang, report_rows, text
from data_guardian.risk_colors import hex_to_rgb, risk_colors

COL_WIDTHS = (14, 34, 52, 22, 28, 30, 97)  # A4 horizontal: 277 mm útiles


RISK_COLUMN = 3  # posición de la columna de riesgo en las filas del informe


def _risk_style(risk: str) -> FontFace | None:
    """Texto en negrita con el color del nivel de riesgo, sobre un tono claro del mismo color."""
    colors = risk_colors(risk)
    if colors is None:
        return None
    return FontFace(emphasis="BOLD", color=hex_to_rgb(colors[0]), fill_color=hex_to_rgb(colors[1]))


def _safe(value: object) -> str:
    """Hace el texto imprimible con las fuentes estándar del PDF (latin-1); los demás caracteres pasan a '?'."""
    return str(value).encode("latin-1", "replace").decode("latin-1")


def _line(pdf: FPDF, size: int, content: str, bold: bool = False) -> None:
    """Escribe una línea de ancho completo y pasa a la siguiente."""
    pdf.set_font("Helvetica", "B" if bold else "", size)
    pdf.cell(0, size * 0.5 + 2, _safe(content), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _section(pdf: FPDF, lang: Lang, name: str, report: Report) -> None:
    """Agrega la tabla de hallazgos (o el mensaje de que no hay hallazgos) en un idioma."""
    if not report.items:
        _line(pdf, 11, text(lang, "none", name=name))
        return
    _line(pdf, 13, text(lang, "findings", name=name), bold=True)
    rows = report_rows(report, lang)
    pdf.set_font("Helvetica", "", 9)
    with pdf.table(col_widths=COL_WIDTHS, text_align="LEFT") as table:
        header = table.row()
        for title in rows[0]:
            header.cell(_safe(title))
        for item, row_data in zip(report.items, rows):
            row = table.row()
            for index, cell in enumerate(row_data.values()):
                row.cell(_safe(cell), style=_risk_style(item.risk.value) if index == RISK_COLUMN else None)
    if note := text(lang, "reasons_note"):
        pdf.ln(2)
        _line(pdf, 9, note)


def build_pdf(
    name: str, report: Report, langs: tuple[Lang, ...] = ("es",), generated_at: datetime | None = None
) -> bytes:
    """Crea el PDF del informe: primero el idioma principal y, debajo, cada idioma adicional."""
    primary = langs[0]
    moment = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    pdf = FPDF(orientation="L", format="A4")
    pdf.set_compression(False)
    pdf.set_title(_safe(text(primary, "pdf_title")))
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    _line(pdf, 18, text(primary, "pdf_title"), bold=True)
    _line(pdf, 10, text(primary, "pdf_file", name=name))
    _line(pdf, 10, text(primary, "pdf_date", date=moment))
    done = sum(item.anonymized for item in report.items)
    _line(pdf, 10, text(primary, "pdf_summary", total=str(len(report.items)), done=str(done)))
    for index, lang in enumerate(langs):
        pdf.ln(4)
        if index > 0:
            _line(pdf, 16, text(lang, "pdf_title"), bold=True)
            _line(pdf, 10, text(lang, "pdf_summary", total=str(len(report.items)), done=str(done)))
            pdf.ln(2)
        _section(pdf, lang, name, report)
    return bytes(pdf.output())
