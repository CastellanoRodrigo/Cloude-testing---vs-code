"""Etiquetas fijas en español para el informe. Las razones de la IA ya se escriben en español."""

from typing import Literal

from data_guardian.agent import Report

Lang = Literal["es"]

COLUMNS: dict[Lang, dict[str, str]] = {
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

# Traducción de los valores internos (enumeraciones) que se muestran al usuario.
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
    "es": {
        "findings": "Hallazgos en {name}",
        "none": "No se encontraron datos sensibles en {name}. No se creó ninguna copia.",
        "yes": "sí",
        "no": "no (conservado)",
        "reasons_note": "",
        "pdf_title": "AI Data Guardian Lite - Informe",
        "pdf_file": "Archivo: {name}",
        "pdf_date": "Generado: {date}",
        "pdf_summary": "{total} hallazgos, {done} anonimizados",
    },
}


def text(lang: Lang, key: str, **values: str) -> str:
    """Devuelve un mensaje fijo en el idioma indicado, con los valores tipo `{name}` rellenados."""
    return TEXTS[lang][key].format(**values)


def _value(lang: Lang, raw: str) -> str:
    """Traduce un valor de enumeración al español."""
    return VALUES_ES.get(raw, raw)


def report_rows(report: Report, lang: Lang) -> list[dict[str, str | int]]:
    """Construye las filas de la tabla del informe en el idioma indicado. Solo valores enmascarados."""
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
