"""Orchestration of the scan/classify/recommend/anonymize/audit tools with a strict allowlist."""

from pathlib import Path
from typing import Any, Callable

import anthropic
from pydantic import BaseModel

from data_guardian.anonymizer import anonymize_file, output_path
from data_guardian.auditor import AuditReport, audit_file
from data_guardian.classifier import (
    Action,
    Classification,
    ClassifierError,
    Recommendation,
    Risk,
    classify_findings,
    recommend_actions,
)
from data_guardian.scanner import Finding, FindingKind, mask_value, scan_file

ALLOWED_TOOLS = ("scan", "classify", "recommend", "anonymize", "audit")


class ToolNotAllowedError(Exception):
    """Raised when a tool outside the allowlist is requested."""


class ReportItem(BaseModel):
    """One finding in the report. Only the masked value is included."""

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
    """Result of a run. Never contains real sensitive values."""

    source: str
    output: str | None
    items: list[ReportItem]
    audit: AuditReport | None = None  # verdict on the copy (or on the original when no copy was made)
    audit_error: str | None = None  # why the audit failed (never contains sensitive values)


def _must_anonymize(finding: Finding, recommendation: Recommendation) -> bool:
    """API keys are always anonymized: the AI cannot choose to leave a secret in the copy."""
    return finding.kind == FindingKind.API_KEY or recommendation.action != Action.KEEP


class Agent:
    """Runs the fixed workflow, calling only the 5 allowed tools."""

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client
        self._tools: dict[str, Callable[..., Any]] = {
            "scan": scan_file,
            "classify": lambda findings: classify_findings(findings, self._client),
            "recommend": lambda findings, classifications: recommend_actions(findings, classifications, self._client),
            "anonymize": anonymize_file,
            "audit": lambda path: audit_file(path, self._client),
        }

    def call_tool(self, name: str, **kwargs: Any) -> Any:
        """Run an allowed tool by name. Any other tool is rejected."""
        if name not in ALLOWED_TOOLS:
            raise ToolNotAllowedError(f"Tool '{name}' is not allowed.")
        return self._tools[name](**kwargs)

    def run(self, path: str | Path) -> Report:
        """Scan, classify, recommend, anonymize and audit a file. The original is never modified."""
        source = Path(path)
        target = output_path(source)
        if target.exists():
            raise FileExistsError(f"Anonymized copy already exists: {target}")

        findings: list[Finding] = self.call_tool("scan", path=source)
        if not findings:  # the regex saw nothing, but the auditor may still catch names, phones, etc.
            audit, audit_error = self._audit(source)
            return Report(source=str(source), output=None, items=[], audit=audit, audit_error=audit_error)

        classifications: list[Classification] = self.call_tool("classify", findings=findings)
        recommendations: list[Recommendation] = self.call_tool(
            "recommend", findings=findings, classifications=classifications
        )

        must_anonymize = [_must_anonymize(f, r) for f, r in zip(findings, recommendations)]
        to_anonymize = [f for f, flag in zip(findings, must_anonymize) if flag]
        copy = self.call_tool("anonymize", path=source, findings=to_anonymize) if to_anonymize else None

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
        audit, audit_error = self._audit(copy or source)
        return Report(
            source=str(source), output=str(copy) if copy else None, items=items, audit=audit, audit_error=audit_error
        )

    def _audit(self, path: Path) -> tuple[AuditReport | None, str | None]:
        """Audit a file (read-only). A failure is returned as a message so the report is not lost."""
        try:
            return self.call_tool("audit", path=path), None
        except ClassifierError as exc:  # the copy already exists: keep the report and say why the audit failed
            return None, str(exc)
