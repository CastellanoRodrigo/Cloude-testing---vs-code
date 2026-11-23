"""Orquestación de las herramientas extraer/escanear/clasificar/recomendar/anonimizar/auditar con una lista de permitidas estricta."""

from pathlib import Path
from typing import Any, Callable

import anthropic
from pydantic import BaseModel

from data_guardian.anonymizer import anonymize_file, output_path, write_anonymized_text
from data_guardian.auditor import AuditReport, audit_file, audit_text
from data_guardian.classifier import (
    Action,
    Classification,
    ClassifierError,
    Recommendation,
    Risk,
    classify_findings,
    recommend_actions,
)
from data_guardian.extractor import RICH_EXTENSIONS, Extraction, extract_text
from data_guardian.scanner import Finding, FindingKind, mask_value, scan_file, scan_text

ALLOWED_TOOLS = ("extract", "scan", "classify", "recommend", "anonymize", "audit")


class ToolNotAllowedError(Exception):
    """Se lanza cuando se pide una herramienta que no está en la lista de permitidas."""


class ReportItem(BaseModel):
    """Un hallazgo del informe. Solo incluye el valor enmascarado."""

    id: int
    kind: FindingKind
    line: int
    masked: str
    risk: Risk
    risk_reason: str
    action: Action
    action_reason: str
    anonymized: bool


class Report(BaseModel):
    """Resultado de una ejecución. Nunca contiene valores sensibles reales."""

    source: str
    output: str | None
    items: list[ReportItem]
    audit: AuditReport | None = None  # veredicto sobre la copia (o sobre el original si no se hizo copia)
    audit_error: str | None = None  # por qué falló la auditoría (nunca contiene valores sensibles)
    extraction_method: str | None = None  # "text", "ocr" o "mixed" si el archivo era un PDF o una imagen; si no, None


def _must_anonymize(finding: Finding, recommendation: Recommendation) -> bool:
    """Las claves de API siempre se anonimizan: la IA no puede decidir dejar un secreto en la copia."""
    return finding.kind == FindingKind.API_KEY or recommendation.action != Action.KEEP


class Agent:
    """Ejecuta el flujo fijo, llamando solo a las 6 herramientas permitidas."""

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client
        self._tools: dict[str, Callable[..., Any]] = {
            "extract": extract_text,  # solo en local: sin red ni API
            "scan": lambda path=None, text=None: scan_file(path) if text is None else scan_text(text),
            "classify": lambda findings: classify_findings(findings, self._client),
            "recommend": lambda findings, classifications: recommend_actions(findings, classifications, self._client),
            "anonymize": lambda path, findings, text=None: (
                anonymize_file(path, findings) if text is None else write_anonymized_text(path, text, findings)
            ),
            "audit": lambda path=None, text=None: (
                audit_file(path, self._client) if text is None else audit_text(text, ".txt", self._client)
            ),
        }

    def call_tool(self, name: str, **kwargs: Any) -> Any:
        """Ejecuta una herramienta permitida por su nombre. Cualquier otra se rechaza."""
        if name not in ALLOWED_TOOLS:
            raise ToolNotAllowedError(f"La herramienta '{name}' no está permitida.")
        return self._tools[name](**kwargs)

    def run(self, path: str | Path) -> Report:
        """Extrae (PDF e imágenes), escanea, clasifica, recomienda, anonimiza y audita un archivo. El original nunca se modifica."""
        source = Path(path)
        target = output_path(source)
        if target.exists():
            raise FileExistsError(f"La copia anonimizada ya existe: {target}")

        rich = source.suffix.lower() in RICH_EXTENSIONS  # PDF o imagen: primero se extrae el texto plano
        text: str | None = None
        method: str | None = None
        if rich:
            extraction: Extraction = self.call_tool("extract", path=source)
            text, method = extraction.text, extraction.method
            findings: list[Finding] = self.call_tool("scan", text=text)
        else:
            findings = self.call_tool("scan", path=source)
        if not findings:  # el regex no vio nada, pero el auditor aún puede detectar nombres, teléfonos, etc.
            audit, audit_error = self._audit(source, text)
            return Report(
                source=str(source), output=None, items=[], audit=audit, audit_error=audit_error, extraction_method=method
            )

        classifications: list[Classification] = self.call_tool("classify", findings=findings)
        recommendations: list[Recommendation] = self.call_tool(
            "recommend", findings=findings, classifications=classifications
        )

        must_anonymize = [_must_anonymize(f, r) for f, r in zip(findings, recommendations)]
        to_anonymize = [f for f, flag in zip(findings, must_anonymize) if flag]
        extra = {"text": text} if rich else {}
        copy = self.call_tool("anonymize", path=source, findings=to_anonymize, **extra) if to_anonymize else None

        items = [
            ReportItem(
                id=index,
                kind=finding.kind,
                line=finding.line,
                masked=mask_value(finding.value),
                risk=classification.risk,
                risk_reason=classification.reason,
                action=recommendation.action,
                action_reason=recommendation.reason,
                anonymized=flag,
            )
            for index, (finding, classification, recommendation, flag) in enumerate(
                zip(findings, classifications, recommendations, must_anonymize)
            )
        ]
        # Con copia, se audita la copia (un .txt); sin copia, el original o, si era PDF/imagen, su texto extraído.
        audit, audit_error = self._audit(copy or source, text if copy is None else None)
        return Report(
            source=str(source),
            output=str(copy) if copy else None,
            items=items,
            audit=audit,
            audit_error=audit_error,
            extraction_method=method,
        )

    def _audit(self, path: Path, text: str | None = None) -> tuple[AuditReport | None, str | None]:
        """Audita un archivo (solo lectura) o, si se da `text`, el texto extraído. Un fallo se devuelve como mensaje."""
        try:
            if text is None:
                return self.call_tool("audit", path=path), None
            return self.call_tool("audit", text=text), None
        except ClassifierError as exc:  # la copia ya existe: se conserva el informe y se explica por qué falló la auditoría
            return None, str(exc)
