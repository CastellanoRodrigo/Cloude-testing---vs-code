"""Pruebas del conjunto sintético y de la puntuación (solo datos falsos, sin llamadas a la API)."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from data_guardian.auditor import mask_text
from data_guardian.benchmark import Score, rules_detections, run_benchmark, score
from data_guardian.benchmark_data import Case, Item, synthetic_cases
from data_guardian.classifier import ClassifierError
from tests.test_agent import FakeClient

CASES = synthetic_cases()


def case_of(*items: Item) -> Case:
    """A case with one item per line, lines 1, 2, 3..."""
    for number, item in enumerate(items, start=1):
        item.line = number
    return Case(name="x", extension=".txt", text="\n".join(i.value for i in items), items=list(items))


def test_dataset_has_twelve_files_and_every_value_is_on_its_own_line() -> None:
    assert len(CASES) == 12
    for case in CASES:
        lines = case.text.splitlines()
        assert case.items and all(item.value in lines[item.line - 1] for item in case.items)
        assert len({item.line for item in case.items}) == len(case.items)  # one item per line


def test_dataset_covers_what_regex_misses_decoys_and_hidden_instructions() -> None:
    items = [item for case in CASES for item in case.items]
    assert {"name", "phone", "financial", "address", "health"} <= {i.kind for i in items if i.sensitive}
    assert any(not i.sensitive for i in items)
    assert [c.name for c in CASES if c.injection] == ["injection", "injection_csv"]


def test_score_counts_tp_fp_and_fn() -> None:
    case = case_of(
        Item(value="alpha@example.com", kind="email", sensitive=True),
        Item(value="noreply@example.com", kind="email", sensitive=False),
        Item(value="GARCIA Mariana", kind="name", sensitive=True),
    )
    result = score(case, [1, 2, 9, None])
    assert (result.tp, result.fp, result.fn) == (1, 3, 1)  # decoy + unknown line + no line; the name was missed


def test_repeated_hits_on_one_line_count_once() -> None:
    case = case_of(Item(value="GARCIA Mariana", kind="name", sensitive=True))
    assert (score(case, [1, 1, 1]).tp, score(case, [1, 1, 1]).fp) == (1, 0)


def test_precision_and_recall_with_empty_denominators() -> None:
    assert Score().precision == 1.0 and Score().recall == 1.0
    assert Score(tp=3, fp=1, fn=1).precision == 0.75 and Score(tp=3, fp=1, fn=1).recall == 0.75
    assert (Score(tp=1) + Score(fp=2, fn=3)) == Score(tp=1, fp=2, fn=3)


def test_rules_only_baseline_on_the_dataset() -> None:
    total = Score()
    for case in CASES:
        total = total + score(case, rules_detections(case))
    assert (total.tp, total.fp, total.fn) == (10, 5, 23)
    assert round(total.precision, 2) == 0.67 and round(total.recall, 2) == 0.30


FIRST_LINES = {mask_text(case.text.splitlines()[0]): case for case in CASES}
REGEX_KINDS = {"email", "document_id", "api_key"}
assert len(FIRST_LINES) == len(CASES)  # the fake auditor tells the files apart by their first line


def block(tool: str, answer: dict[str, Any]) -> Any:
    return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name=tool, input=answer)], stop_reason="tool_use")


class SmartClient(FakeClient):
    """Keeps decoys, and its auditor reports every sensitive value the regex cannot see (a good AI)."""

    def _create(self, **kwargs: Any) -> Any:
        tool = kwargs["tools"][0]["name"]
        if tool == "report_audit":
            self.calls.append(kwargs)
            payload = json.loads(kwargs["messages"][0]["content"])
            case = FIRST_LINES[payload["lines"][0]["text"]]
            findings = [
                {"id": n, "location": f"línea {item.line}", "kind": "otro", "masked": "xx", "risk": "alto",
                 "suggested_label": "[X_REDACTED]", "reason": "r"}
                for n, item in enumerate(i for i in case.items if i.sensitive and i.kind not in REGEX_KINDS)
            ]
            return block(tool, {"findings": findings, "recommendation": "r"})
        if tool == "report_recommendations":
            self.calls.append(kwargs)
            items = json.loads(kwargs["messages"][0]["content"])
            decoy = ("no", "in", "55", "00")  # first 2 characters of noreply@, info@, 555-12-3456 and 000-00-0000
            answer = [{"id": i["id"], "action": "keep" if i["masked"][:2] in decoy else "anonymize", "reason": "r"}
                      for i in items]
            return block(tool, {"items": answer})
        return super()._create(**kwargs)


class UselessClient(FakeClient):
    """Anonymizes everything the regex finds and its auditor never reports anything (a useless AI)."""

    def _create(self, **kwargs: Any) -> Any:
        if kwargs["tools"][0]["name"] == "report_recommendations":
            self.calls.append(kwargs)
            items = json.loads(kwargs["messages"][0]["content"])
            return block("report_recommendations",
                         {"items": [{"id": i["id"], "action": "anonymize", "reason": "r"} for i in items]})
        return super()._create(**kwargs)


def test_good_ai_beats_the_rules_and_resists_the_hidden_instructions() -> None:
    result = run_benchmark(SmartClient())
    assert (result.rules.tp, result.rules.fp, result.rules.fn) == (10, 5, 23)
    assert (result.ai.tp, result.ai.fp, result.ai.fn) == (33, 1, 0)  # only the sk-learn tag is forced out: API keys always go
    assert result.ai_contributes and result.injection_resisted is True
    assert result.verdict == (
        "La IA aporta en este conjunto de datos: recall 30% → 100%, precisión 67% → 97%, falsos positivos 5 → 1."
    )


def test_useless_ai_is_reported_as_not_contributing() -> None:
    result = run_benchmark(UselessClient())
    assert result.ai == result.rules and not result.ai_contributes
    assert result.verdict.startswith("La IA no aporta")
    assert result.injection_resisted is False  # it approved the file with hidden instructions


def test_benchmark_sends_only_masked_values_to_the_api() -> None:
    client = SmartClient()
    run_benchmark(client)
    sent = json.dumps(client.calls, ensure_ascii=False)
    for case in CASES:
        for item in case.items:
            assert item.value not in sent


def test_benchmark_fails_clearly_when_an_audit_fails() -> None:
    class BrokenClient(FakeClient):
        audit_findings = [{"id": 0, "risk": "extremo"}]

    with pytest.raises(ClassifierError, match="La auditoría no se completó"):
        run_benchmark(BrokenClient())
