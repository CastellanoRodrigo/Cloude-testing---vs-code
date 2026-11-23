"""Interfaz de usuario con Streamlit."""

import tempfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from data_guardian.agent import Agent, Report
from data_guardian.auditor import audit_rows
from data_guardian.benchmark import BenchmarkResult, Score, run_benchmark
from data_guardian.benchmark_data import synthetic_cases
from data_guardian.classifier import ClassifierError
from data_guardian.cli import load_env
from data_guardian.dashboard import show_dashboard
from data_guardian.fetcher import MAX_BYTES, fetch_url
from data_guardian.history import History
from data_guardian.i18n import COLUMNS, Lang, report_rows, text
from data_guardian.pdf_report import build_pdf
from data_guardian.risk_colors import risk_table
from data_guardian.token_panel import show_budget_alert, show_token_panel
from data_guardian.usage import UsageLog
from data_guardian.wall import wall_css
from data_guardian.welcome import KNIGHT_HEIGHT, WELCOME_ES, knight_video_html

CARD_CSS = """
<style>
div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 12px; border-color: #b9c0c8; background: rgba(255, 255, 255, 0.7); }
div[data-testid="stButton"] button { border-radius: 8px; }
</style>
"""


def process_upload(name: str, data: bytes, agent: Agent) -> tuple[Report, bytes | None, str | None]:
    """Ejecuta el agente sobre los bytes subidos en una carpeta temporal; devuelve el informe, los bytes de la copia y su nombre."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(name).name
        path.write_bytes(data)
        report = agent.run(path)
        if report.output is None:
            return report, None, None
        copy = Path(report.output)
        return report, copy.read_bytes(), copy.name


def resolve_input(url: str, upload: tuple[str, bytes] | None) -> tuple[str, bytes]:
    """Devuelve (nombre de archivo, contenido) desde exactamente una fuente: una URL o un archivo subido."""
    url = url.strip()
    if url and upload is not None:
        raise ValueError("Usa una URL o un archivo, no ambos.")
    if upload is not None:
        if len(upload[1]) > MAX_BYTES:
            raise ValueError(f"El archivo supera los {MAX_BYTES // (1024 * 1024)} MB.")
        return upload
    if url:
        return fetch_url(url)
    raise ValueError("Escribe la URL de un archivo o adjunta un archivo TXT, CSV, PDF o imagen.")


def _show_section(lang: Lang, name: str, report: Report) -> None:
    """Muestra la tabla de hallazgos (o el mensaje de que no hay hallazgos) en un idioma."""
    if not report.items:
        st.success(text(lang, "none", name=name))
        return
    st.subheader(text(lang, "findings", name=name))
    st.table(risk_table(report_rows(report, lang), COLUMNS[lang]["risk"]))
    if note := text(lang, "reasons_note"):
        st.caption(note)


def _show_audit(report: Report) -> None:
    """Muestra el veredicto del auditor sobre la copia y, si hubo fugas, la tabla de hallazgos enmascarados."""
    if report.audit_error:
        st.warning(f"Auditoría no completada: {report.audit_error}")
    elif report.audit is None:
        return
    elif report.audit.no_data:
        st.warning(report.audit.headline)
    elif report.audit.approved:
        st.success(report.audit.headline)
    else:
        st.error(report.audit.headline)
        st.table(risk_table(audit_rows(report.audit), "riesgo"))
        if report.audit.recommendation:
            st.caption(report.audit.recommendation)


OCR_NOTE = "El texto se leyó con OCR local: puede contener errores de lectura, y la copia (.txt) no conserva el formato original."


def show_result(name: str, report: Report, copy_bytes: bytes | None, copy_name: str | None) -> None:
    """Muestra el informe (solo valores enmascarados), el botón de descarga de la copia y la exportación a PDF."""
    if report.extraction_method in ("ocr", "mixed"):
        st.info(OCR_NOTE)
    _show_section("es", name, report)
    if report.items:
        if copy_bytes is not None and copy_name is not None:
            st.download_button(f"Descargar {copy_name}", data=copy_bytes, file_name=copy_name)
        else:
            st.info("No hubo nada que anonimizar, así que no se creó ninguna copia.")
    _show_audit(report)
    pdf_col, _ = st.columns([1, 3])
    pdf_col.download_button(
        "Exportar informe en PDF",
        data=build_pdf(name, report, ("es",)),
        file_name=f"{Path(name).stem}_report.pdf",
        mime="application/pdf",
        key="export_pdf",
        use_container_width=True,
    )


def _score_cells(score: Score) -> list[str]:
    """Precisión, recall y falsos positivos de una variante, listos para una fila de tabla."""
    return [f"{score.precision:.0%}", f"{score.recall:.0%}", str(score.fp)]


def _show_benchmark_result(result: BenchmarkResult) -> None:
    """Muestra solo reglas frente a reglas + IA por archivo y en total, el veredicto y la comprobación de inyección."""
    header = [
        "Archivo",
        "Precisión (reglas)",
        "Recall (reglas)",
        "Falsos pos. (reglas)",
        "Precisión (IA)",
        "Recall (IA)",
        "Falsos pos. (IA)",
    ]
    rows = [[c.name, *_score_cells(c.rules), *_score_cells(c.ai)] for c in result.cases]
    rows.append(["TOTAL", *_score_cells(result.rules), *_score_cells(result.ai)])
    st.table([dict(zip(header, row)) for row in rows])
    (st.success if result.ai_contributes else st.warning)(result.verdict)
    st.caption(
        "Límite: la IA solo ve valores enmascarados, así que no puede distinguir los señuelos que el regex ya marca "
        "(buzones de rol, marcadores, etiquetas de compilación). Su ganancia es el recall, gracias a la auditoría, "
        "no una reducción de falsos positivos."
    )
    if result.injection_resisted is True:
        st.success("Las instrucciones ocultas en un archivo se trataron como datos: el auditor siguió reportando sus fugas.")
    elif result.injection_resisted is False:
        st.error("El auditor aprobó un archivo que llevaba instrucciones ocultas y aún tenía fugas.")


def show_benchmark() -> None:
    """Muestra la comparación de solo reglas frente a reglas + IA sobre archivos sintéticos."""
    with st.container(border=True):
        st.subheader("Reglas vs reglas + IA")
        st.caption(
            f"Ejecuta el agente sobre {len(synthetic_cases())} archivos sintéticos con respuesta conocida "
            "(unas 25-35 llamadas a la API, solo con valores enmascarados) y mide la precisión, el recall "
            "y los falsos positivos."
        )
        run = st.button("Ejecutar comparación", key="run_benchmark", type="primary")
    if run:
        try:
            with st.spinner("Ejecutando la comparación..."):
                st.session_state["benchmark"] = run_benchmark()
        except ClassifierError as exc:
            st.session_state.pop("benchmark", None)
            st.error(str(exc))
    if "benchmark" in st.session_state:
        _show_benchmark_result(st.session_state["benchmark"])


def show_welcome() -> None:
    """Muestra el avatar del caballero con el mensaje de bienvenida al lado."""
    avatar_col, text_col = st.columns([1.5, 4], vertical_alignment="center")
    with avatar_col:
        components.html(knight_video_html(), height=KNIGHT_HEIGHT)
    text_col.markdown(WELCOME_ES)


def show_scan() -> None:
    """Muestra el formulario de análisis y sus resultados."""
    form_id = st.session_state.setdefault("form_id", 0)  # un id nuevo da entradas vacías
    with st.container(border=True):
        st.subheader("Parámetros de ejecución")
        st.markdown("**URL del archivo** :red[*]")
        st.caption("URL del archivo (TXT, CSV, PDF o imagen) a analizar, por ejemplo https://ejemplo.com/datos.csv")
        url = st.text_input(
            "URL del archivo", key=f"url_{form_id}", placeholder="Escribe la URL del archivo...", label_visibility="collapsed"
        )
        uploaded = st.file_uploader("Adjunta un archivo TXT, CSV, PDF o imagen", type=["txt", "csv", "pdf", "png", "jpg", "jpeg"], key=f"upload_{form_id}")
        _, cancel_col, execute_col = st.columns([5, 1, 1.3])
        cancel = cancel_col.button("Cancelar", key="cancel", use_container_width=True)
        execute = execute_col.button("Ejecutar", key="execute", type="primary", use_container_width=True)

    if cancel:
        st.session_state.pop("result", None)
        st.session_state["form_id"] = form_id + 1
        st.rerun()
    if execute:
        try:
            upload = (uploaded.name, uploaded.getvalue()) if uploaded is not None else None
            name, data = resolve_input(url, upload)
            with st.spinner("Escaneando y clasificando..."):
                result = process_upload(name, data, Agent())
            History.default().record(result[0], Path(name).suffix)
            st.session_state["result"] = (name, *result)
        except (FileExistsError, ValueError, ClassifierError) as exc:
            st.session_state.pop("result", None)
            st.error(str(exc))
    if "result" in st.session_state:
        show_result(*st.session_state["result"])


def main() -> None:
    """Dibuja la app: bienvenida, luego la pestaña de análisis, el panel y la comparación."""
    load_env()
    st.markdown(CARD_CSS + wall_css(), unsafe_allow_html=True)
    st.title("AI Data Guardian Lite")
    st.caption(
        "Analiza archivos TXT, CSV, PDF e imágenes en busca de correos, números de documento y claves de API. "
        "A la IA solo llegan valores enmascarados."
    )
    show_welcome()
    scan_tab, dashboard_tab, benchmark_tab = st.tabs(["Revisar", "Panel", "Comparación"])
    with scan_tab:
        show_budget_alert(UsageLog.default())
        show_scan()
    with dashboard_tab:  # se dibuja después del análisis, así una revisión reciente ya queda contada
        show_dashboard(History.default())
        show_token_panel(UsageLog.default())
    with benchmark_tab:
        show_benchmark()


if __name__ == "__main__":
    main()
