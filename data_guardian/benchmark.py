"""Puntuación de solo reglas frente a reglas + IA sobre archivos sintéticos. Una detección es solo un número de línea."""

import re
import tempfile
from pathlib import Path

import anthropic
from pydantic import BaseModel

from data_guardian.agent import Agent, Report
from data_guardian.benchmark_data import Case, synthetic_cases
from data_guardian.classifier import ClassifierError
from data_guardian.scanner import scan_text


class Score(BaseModel):
    """Verdaderos positivos, falsos positivos y falsos negativos de una variante."""

    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        """De todo lo marcado, la proporción que realmente era sensible (1.0 si no se marcó nada)."""
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        """De todo lo sensible, la proporción que se marcó (1.0 si no había nada sensible)."""
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    def __add__(self, other: "Score") -> "Score":
        return Score(tp=self.tp + other.tp, fp=self.fp + other.fp, fn=self.fn + other.fn)


def score(case: Case, detections: list[int | None]) -> Score:
    """Puntúa las detecciones (números de línea, None si la línea se desconoce) contra los elementos de un caso.

    Cada elemento está en su propia línea. Un elemento sensible marcado es un VP, un señuelo marcado es un FP,
    una detección en una línea sin elemento (o sin línea) es un FP, y un elemento sensible que nadie marcó es un FN.
    Los aciertos repetidos en una misma línea cuentan una sola vez.
    """
    by_line = {item.line: item for item in case.items}
    flagged = {line for line in detections if line in by_line}
    unmatched = sum(1 for line in detections if line not in by_line)
    tp = sum(by_line[line].sensitive for line in flagged)
    sensitive = sum(item.sensitive for item in case.items)
    return Score(tp=tp, fp=len(flagged) - tp + unmatched, fn=sensitive - tp)


def rules_detections(case: Case) -> list[int | None]:
    """Líneas que marca la variante de solo reglas: todo lo que encuentra el escáner regex."""
    return [finding.line for finding in scan_text(case.text)]


def _line_of(location: str) -> int | None:
    """El número de línea en una ubicación del auditor como 'línea 3' o 'línea 3, columna 2'."""
    match = re.search(r"\d+", location)
    return int(match.group()) if match else None


def ai_detections(report: Report) -> list[int | None]:
    """Líneas que marca la variante reglas + IA: lo que la IA decidió anonimizar, más las fugas que informa el auditor."""
    flagged: list[int | None] = [item.line for item in report.items if item.anonymized]
    if report.audit is not None:
        flagged += [_line_of(finding.location) for finding in report.audit.findings]
    return flagged


class CaseResult(BaseModel):
    """Ambas variantes sobre un archivo sintético."""

    name: str
    rules: Score
    ai: Score
    injection: bool
    audit_flagged_leaks: bool  # el auditor no aprobó la copia


class BenchmarkResult(BaseModel):
    """Solo reglas frente a reglas + IA sobre todos los archivos sintéticos."""

    cases: list[CaseResult]

    @property
    def rules(self) -> Score:
        """Total de la variante de solo reglas."""
        return sum((c.rules for c in self.cases), Score())

    @property
    def ai(self) -> Score:
        """Total de la variante reglas + IA."""
        return sum((c.ai for c in self.cases), Score())

    @property
    def injection_resisted(self) -> bool | None:
        """True cuando todo archivo con instrucciones ocultas fue auditado igualmente como con fugas (None: no hay ninguno)."""
        hidden = [c for c in self.cases if c.injection]
        return all(c.audit_flagged_leaks for c in hidden) if hidden else None

    @property
    def ai_contributes(self) -> bool:
        """True cuando la IA sube el recall o quita falsos positivos respecto a las reglas solas."""
        return self.ai.recall > self.rules.recall or self.ai.fp < self.rules.fp

    @property
    def verdict(self) -> str:
        """Una frase honesta: ¿aporta la IA en este conjunto de datos y a qué costo?"""
        r, a = self.rules, self.ai
        numbers = (
            f"recall {r.recall:.0%} → {a.recall:.0%}, precisión {r.precision:.0%} → {a.precision:.0%}, "
            f"falsos positivos {r.fp} → {a.fp}"
        )
        if self.ai_contributes:
            return f"La IA aporta en este conjunto de datos: {numbers}."
        return f"La IA no aporta en este conjunto de datos: {numbers}."


def run_benchmark(client: anthropic.Anthropic | None = None) -> BenchmarkResult:
    """Ejecuta el agente real sobre cada archivo sintético y puntúa ambas variantes. A la API solo llegan valores enmascarados."""
    results: list[CaseResult] = []
    for case in synthetic_cases():
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{case.name}{case.extension}"
            path.write_text(case.text, encoding="utf-8")
            report = Agent(client).run(path)
        if report.audit is None:
            raise ClassifierError(f"La auditoría no se completó para '{case.name}': {report.audit_error}")
        results.append(
            CaseResult(
                name=case.name,
                rules=score(case, rules_detections(case)),
                ai=score(case, ai_detections(report)),
                injection=case.injection,
                audit_flagged_leaks=not report.audit.approved,
            )
        )
    return BenchmarkResult(cases=results)
