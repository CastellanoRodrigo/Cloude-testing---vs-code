"""Scoring of rules-only against rules + AI on synthetic files. A detection is just a line number."""

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
    """True positives, false positives and false negatives of one arm."""

    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        """Of everything flagged, the share that was really sensitive (1.0 when nothing was flagged)."""
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        """Of everything sensitive, the share that was flagged (1.0 when nothing was sensitive)."""
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    def __add__(self, other: "Score") -> "Score":
        return Score(tp=self.tp + other.tp, fp=self.fp + other.fp, fn=self.fn + other.fn)


def score(case: Case, detections: list[int | None]) -> Score:
    """Score detections (line numbers, None when the line is unknown) against the items of a case.

    Every item sits on its own line. A flagged sensitive item is a TP, a flagged decoy is a FP, a detection
    on a line with no item (or with no line) is a FP, and a sensitive item nobody flagged is a FN.
    Repeated hits on one line count once.
    """
    by_line = {item.line: item for item in case.items}
    flagged = {line for line in detections if line in by_line}
    unmatched = sum(1 for line in detections if line not in by_line)
    tp = sum(by_line[line].sensitive for line in flagged)
    sensitive = sum(item.sensitive for item in case.items)
    return Score(tp=tp, fp=len(flagged) - tp + unmatched, fn=sensitive - tp)


def rules_detections(case: Case) -> list[int | None]:
    """Lines the rules-only arm flags: everything the regex scanner finds."""
    return [finding.line for finding in scan_text(case.text)]


def _line_of(location: str) -> int | None:
    """The line number in an auditor location such as 'línea 3' or 'línea 3, columna 2'."""
    match = re.search(r"\d+", location)
    return int(match.group()) if match else None


def ai_detections(report: Report) -> list[int | None]:
    """Lines the rules + AI arm flags: what the AI decided to anonymize, plus the leaks the auditor reports."""
    flagged: list[int | None] = [item.line for item in report.items if item.anonymized]
    if report.audit is not None:
        flagged += [_line_of(finding.location) for finding in report.audit.findings]
    return flagged


class CaseResult(BaseModel):
    """Both arms on one synthetic file."""

    name: str
    rules: Score
    ai: Score
    injection: bool
    audit_flagged_leaks: bool  # the auditor did not approve the copy


class BenchmarkResult(BaseModel):
    """Rules-only against rules + AI over all the synthetic files."""

    cases: list[CaseResult]

    @property
    def rules(self) -> Score:
        """Total of the rules-only arm."""
        return sum((c.rules for c in self.cases), Score())

    @property
    def ai(self) -> Score:
        """Total of the rules + AI arm."""
        return sum((c.ai for c in self.cases), Score())

    @property
    def injection_resisted(self) -> bool | None:
        """True when every file with hidden instructions was still audited as having leaks (None: no such file)."""
        hidden = [c for c in self.cases if c.injection]
        return all(c.audit_flagged_leaks for c in hidden) if hidden else None

    @property
    def ai_contributes(self) -> bool:
        """True when the AI raises recall or removes false positives compared with the rules alone."""
        return self.ai.recall > self.rules.recall or self.ai.fp < self.rules.fp

    @property
    def verdict(self) -> str:
        """One honest sentence: does the AI contribute on this dataset, and at what cost."""
        r, a = self.rules, self.ai
        numbers = (
            f"recall {r.recall:.0%} → {a.recall:.0%}, precision {r.precision:.0%} → {a.precision:.0%}, "
            f"false positives {r.fp} → {a.fp}"
        )
        if self.ai_contributes:
            return f"The AI contributes on this dataset: {numbers}."
        return f"The AI does not contribute on this dataset: {numbers}."


def run_benchmark(client: anthropic.Anthropic | None = None) -> BenchmarkResult:
    """Run the real agent on every synthetic file and score both arms. Only masked values reach the API."""
    results: list[CaseResult] = []
    for case in synthetic_cases():
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{case.name}{case.extension}"
            path.write_text(case.text, encoding="utf-8")
            report = Agent(client).run(path)
        if report.audit is None:
            raise ClassifierError(f"The audit did not complete for '{case.name}': {report.audit_error}")
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
