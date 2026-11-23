"""Fixed English/Spanish labels for the report. The AI's reasons are not translated."""

from typing import Literal

from data_guardian.agent import Report

Lang = Literal["en", "es"]

COLUMNS: dict[Lang, dict[str, str]] = {
    "en": {
        "line": "Line",
        "kind": "Kind",
        "masked": "Masked value",
        "risk": "Risk",
        "action": "Action",
        "anonymized": "Anonymized",
        "reason": "Reason",
    },
    "es": {
        "line": "Línea",
        "kind": "Tipo",
        "masked": "Valor enmascarado",
        "risk": "Riesgo",
        "action": "Acción",
        "anonymized": "Anonimizado",
        "reason": "Razón",
    },
}

# English shows the raw enum value; Spanish translates it.
VALUES_ES: dict[str, str] = {
    "email": "correo electrónico",
    "document_id": "documento de identidad",
    "api_key": "clave de API",
    "low": "bajo",
    "medium": "medio",
    "high": "alto",
    "critical": "crítico",
    "anonymize": "anonimizar",
    "review": "revisar",
    "keep": "conservar",
}

TEXTS: dict[Lang, dict[str, str]] = {
    "en": {
        "findings": "Findings in {name}",
        "none": "No sensitive data found in {name}. No copy was created.",
        "yes": "yes",
        "no": "no (kept)",
        "reasons_note": "",
        "pdf_title": "AI Data Guardian Lite - Report",
        "pdf_file": "File: {name}",
        "pdf_date": "Generated: {date}",
        "pdf_summary": "{total} findings, {done} anonymized",
    },
    "es": {
        "findings": "Hallazgos en {name}",
        "none": "No se encontraron datos sensibles en {name}. No se creó ninguna copia.",
        "yes": "sí",
        "no": "no (conservado)",
        "reasons_note": "Las razones las redacta la IA y se muestran en inglés.",
        "pdf_title": "AI Data Guardian Lite - Informe",
        "pdf_file": "Archivo: {name}",
        "pdf_date": "Generado: {date}",
        "pdf_summary": "{total} hallazgos, {done} anonimizados",
    },
}


def text(lang: Lang, key: str, **values: str) -> str:
    """Return a fixed message in the language, with `{name}`-style values filled in."""
    return TEXTS[lang][key].format(**values)


def _value(lang: Lang, raw: str) -> str:
    """Translate an enum value; English keeps it as is."""
    return VALUES_ES.get(raw, raw) if lang == "es" else raw


def report_rows(report: Report, lang: Lang) -> list[dict[str, str | int]]:
    """Build the report table rows in the language. Masked values only."""
    columns = COLUMNS[lang]
    return [
        {
            columns["line"]: item.line,
            columns["kind"]: _value(lang, item.kind.value),
            columns["masked"]: item.masked,
            columns["risk"]: _value(lang, item.risk.value),
            columns["action"]: _value(lang, item.action.value),
            columns["anonymized"]: text(lang, "yes" if item.anonymized else "no"),
            columns["reason"]: item.action_reason,
        }
        for item in report.items
    ]
