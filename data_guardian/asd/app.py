"""Streamlit user interface."""

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
from data_guardian.wall import wall_css
from data_guardian.welcome import KNIGHT_HEIGHT, WELCOME_EN, WELCOME_ES, knight_video_html

CARD_CSS = """
<style>
div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 12px; border-color: #b9c0c8; background: rgba(255, 255, 255, 0.7); }
div[data-testid="stButton"] button { border-radius: 8px; }
</style>
"""


def process_upload(name: str, data: bytes, agent: Agent) -> tuple[Report, bytes | None, str | None]:
    """Run the agent on uploaded bytes in a temp folder; return report, copy bytes and copy name."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(name).name
        path.write_bytes(data)
        report = agent.run(path)
        if report.output is None:
            return report, None, None
        copy = Path(report.output)
        return report, copy.read_bytes(), copy.name


def resolve_input(url: str, upload: tuple[str, bytes] | None) -> tuple[str, bytes]:
    """Return (file name, content) from exactly one source: a URL or an uploaded file."""
    url = url.strip()
    if url and upload is not None:
        raise ValueError("Use either a URL or a file, not both.")
    if upload is not None:
        if len(upload[1]) > MAX_BYTES:
            raise ValueError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
        return upload
    if url:
        return fetch_url(url)
    raise ValueError("Enter a file URL or attach a TXT/CSV file.")


def _show_section(lang: Lang, name: str, report: Report) -> None:
    """Show the findings table (or the no-findings message) in one language."""
    if not report.items:
        st.success(text(lang, "none", name=name))
        return
    st.subheader(text(lang, "findings", name=name))
    st.table(risk_table(report_rows(report, lang), COLUMNS[lang]["risk"]))
    if note := text(lang, "reasons_note"):
        st.caption(note)


def _show_audit(report: Report) -> None:
    """Show the auditor's verdict on the copy and, when something leaked, the table of masked findings."""
    if report.audit_error:
        st.warning(f"Audit not completed: {report.audit_error}")
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


def _toggle_spanish() -> None:
    """Button callback: show or hide the Spanish report."""
    st.session_state["show_es"] = not st.session_state.get("show_es", False)


def show_result(name: str, report: Report, copy_bytes: bytes | None, copy_name: str | None) -> None:
    """Display the report in English (masked values only), the download button and, on request, Spanish below."""
    _show_section("en", name, report)
    if report.items:
        if copy_bytes is not None and copy_name is not None:
            st.download_button(f"Download {copy_name}", data=copy_bytes, file_name=copy_name)
        else:
            st.info("Nothing needed anonymizing, so no copy was created.")
    _show_audit(report)
    show_es = st.session_state.get("show_es", False)
    translate_col, pdf_col, _ = st.columns([1, 1, 2])
    translate_col.button(
        "Ocultar español" if show_es else "Traducir al español",
        key="translate",
        on_click=_toggle_spanish,
        use_container_width=True,
    )
    pdf_col.download_button(
        "Exportar informe en PDF",
        data=build_pdf(name, report, ("en", "es") if show_es else ("en",)),
        file_name=f"{Path(name).stem}_report.pdf",
        mime="application/pdf",
        key="export_pdf",
        use_container_width=True,
    )
    if show_es:
        st.divider()
        _show_section("es", name, report)


def _score_cells(score: Score) -> list[str]:
    """Precision, recall and false positives of one arm, ready for a table row."""
    return [f"{score.precision:.0%}", f"{score.recall:.0%}", str(score.fp)]


def _show_benchmark_result(result: BenchmarkResult) -> None:
    """Show rules-only against rules + AI per file and in total, the verdict and the injection check."""
    header = ["File", "Rules precision", "Rules recall", "Rules false pos.", "AI precision", "AI recall", "AI false pos."]
    rows = [[c.name, *_score_cells(c.rules), *_score_cells(c.ai)] for c in result.cases]
    rows.append(["TOTAL", *_score_cells(result.rules), *_score_cells(result.ai)])
    st.table([dict(zip(header, row)) for row in rows])
    (st.success if result.ai_contributes else st.warning)(result.verdict)
    st.caption(
        "Limit: the AI only sees masked values, so it cannot tell apart decoys the regex already flags "
        "(role mailboxes, placeholders, build tags). Its gain is recall, from the audit, not fewer false positives."
    )
    if result.injection_resisted is True:
        st.success("Hidden instructions in a file were treated as data: the auditor still reported its leaks.")
    elif result.injection_resisted is False:
        st.error("The auditor approved a file that carried hidden instructions and still had leaks.")


def show_benchmark() -> None:
    """Render the rules-only against rules + AI comparison on synthetic files."""
    with st.container(border=True):
        st.subheader("Rules vs rules + AI")
        st.caption(
            f"Runs the agent on {len(synthetic_cases())} synthetic files with a known answer (about 25-35 API calls, masked values only) "
            "and measures precision, recall and false positives. Ejecuta el agente sobre archivos sintéticos."
        )
        run = st.button("Run comparison", key="run_benchmark", type="primary")
    if run:
        try:
            with st.spinner("Running the comparison..."):
                st.session_state["benchmark"] = run_benchmark()
        except ClassifierError as exc:
            st.session_state.pop("benchmark", None)
            st.error(str(exc))
    if "benchmark" in st.session_state:
        _show_benchmark_result(st.session_state["benchmark"])


def show_welcome() -> None:
    """Show the knight avatar with the welcome message in English and Spanish below."""
    avatar_col, text_col = st.columns([1.5, 4], vertical_alignment="center")
    with avatar_col:
        components.html(knight_video_html(), height=KNIGHT_HEIGHT)
    text_col.markdown(WELCOME_EN)
    text_col.caption(WELCOME_ES)


def show_scan() -> None:
    """Render the scan form and its results."""
    form_id = st.session_state.setdefault("form_id", 0)  # a new id gives empty inputs
    with st.container(border=True):
        st.subheader("Run parameters")
        st.markdown("**file_url** :red[*]")
        st.caption("URL del archivo TXT o CSV a analizar, por ejemplo https://ejemplo.com/datos.csv")
        url = st.text_input(
            "file_url", key=f"url_{form_id}", placeholder="Enter file_url...", label_visibility="collapsed"
        )
        uploaded = st.file_uploader("Attach a TXT or CSV file", type=["txt", "csv"], key=f"upload_{form_id}")
        _, cancel_col, execute_col = st.columns([5, 1, 1.3])
        cancel = cancel_col.button("Cancel", key="cancel", use_container_width=True)
        execute = execute_col.button("Execute", key="execute", type="primary", use_container_width=True)

    if cancel:
        st.session_state.pop("result", None)
        st.session_state.pop("show_es", None)
        st.session_state["form_id"] = form_id + 1
        st.rerun()
    if execute:
        try:
            upload = (uploaded.name, uploaded.getvalue()) if uploaded is not None else None
            name, data = resolve_input(url, upload)
            with st.spinner("Scanning and classifying..."):
                result = process_upload(name, data, Agent())
            History.default().record(result[0], Path(name).suffix)
            st.session_state["result"] = (name, *result)
        except (FileExistsError, ValueError, ClassifierError) as exc:
            st.session_state.pop("result", None)
            st.error(str(exc))
    if "result" in st.session_state:
        show_result(*st.session_state["result"])


def main() -> None:
    """Render the app: welcome, then the scan tab and the dashboard tab."""
    load_env()
    st.markdown(CARD_CSS + wall_css(), unsafe_allow_html=True)
    st.title("AI Data Guardian Lite")
    st.caption("Scans TXT/CSV files for emails, ID numbers and API keys. Only masked values reach the AI.")
    show_welcome()
    scan_tab, dashboard_tab, benchmark_tab = st.tabs(["Scan · Revisar", "Dashboard · Panel", "Comparison · Comparación"])
    with scan_tab:
        show_scan()
    with dashboard_tab:  # drawn after the scan, so a fresh review is already counted
        show_dashboard(History.default())
    with benchmark_tab:
        show_benchmark()


if __name__ == "__main__":
    main()
