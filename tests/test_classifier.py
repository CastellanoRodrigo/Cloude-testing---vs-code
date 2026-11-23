"""Pruebas del clasificador con un cliente falso (sin llamadas reales a la API, solo datos falsos)."""

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from data_guardian.classifier import (
    Action,
    ClassifierError,
    Risk,
    classify_findings,
    recommend_actions,
)
from data_guardian.scanner import mask_value, scan_text

TEXT = "mail ana@example.com id 12345678Z"
FINDINGS = scan_text(TEXT)


class FakeClient:
    """Minimal stand-in for anthropic.Anthropic that records requests."""

    def __init__(self, content: list[Any], stop_reason: str = "tool_use") -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = SimpleNamespace(content=content, stop_reason=stop_reason)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._response


def tool_block(name: str, items: list[dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input={"items": items})


def test_classify_returns_validated_items_and_sends_only_masked_values() -> None:
    items = [
        {"id": 1, "risk": "critical", "reason": "national id"},
        {"id": 0, "risk": "medium", "reason": "email"},
    ]
    client = FakeClient([tool_block("report_classification", items)])
    result = classify_findings(FINDINGS, client)
    assert [(c.id, c.risk) for c in result] == [(0, Risk.MEDIUM), (1, Risk.CRITICAL)]
    sent = json.dumps(client.calls[0])
    assert "ana@example.com" not in sent and "12345678Z" not in sent
    assert "an" in sent and "****" in sent


def test_recommend_returns_actions_and_sends_only_masked_values() -> None:
    client = FakeClient(
        [tool_block("report_recommendations", [
            {"id": 0, "action": "anonymize", "reason": "r"},
            {"id": 1, "action": "review", "reason": "r"},
        ])]
    )
    classifications = classify_findings(
        FINDINGS,
        FakeClient([tool_block("report_classification", [
            {"id": 0, "risk": "low", "reason": "r"},
            {"id": 1, "risk": "high", "reason": "r"},
        ])]),
    )
    result = recommend_actions(FINDINGS, classifications, client)
    assert [r.action for r in result] == [Action.ANONYMIZE, Action.REVIEW]
    sent = json.dumps(client.calls[0])
    assert "ana@example.com" not in sent and "12345678Z" not in sent
    assert '"risk": "high"' in client.calls[0]["messages"][0]["content"]


class LimitedClient:
    """Answers every finding it receives, but its reply is cut off (max_tokens) above 60 findings per call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        tool = kwargs["tools"][0]["name"]
        items = json.loads(kwargs["messages"][0]["content"])
        if len(items) > 60:
            return SimpleNamespace(content=[tool_block(tool, [])], stop_reason="max_tokens")
        if tool == "report_classification":
            answer = [{"id": i["id"], "risk": "high", "reason": "r"} for i in items]
        else:
            answer = [{"id": i["id"], "action": "anonymize", "reason": "r"} for i in items]
        return SimpleNamespace(content=[tool_block(tool, answer)], stop_reason="tool_use")


MANY = scan_text("".join(f"user{i:04d}@example.com\n" for i in range(130)))


def test_many_findings_are_classified_and_recommended_in_batches() -> None:
    client = LimitedClient()
    classifications = classify_findings(MANY, client)
    assert [c.id for c in classifications] == list(range(130))
    recommendations = recommend_actions(MANY, classifications, client)
    assert [r.id for r in recommendations] == list(range(130))
    sizes = [len(json.loads(call["messages"][0]["content"])) for call in client.calls]
    assert sizes == [50, 50, 30] * 2
    sent = json.dumps(client.calls)
    assert "user0000@example.com" not in sent and mask_value("user0000@example.com") in sent


def test_recommend_sends_each_batch_the_risk_of_its_own_findings() -> None:
    client = LimitedClient()
    classifications = classify_findings(MANY, client)
    classifications[55] = classifications[55].model_copy(update={"risk": Risk.LOW})
    recommend_actions(MANY, classifications, client)
    second_batch = json.loads(client.calls[4]["messages"][0]["content"])  # 3 classify calls, then recommend 50-99
    assert [i["risk"] for i in second_batch][5] == "low" and second_batch[4]["risk"] == "high"


def test_cut_off_response_gives_a_clear_message_not_a_mismatch() -> None:
    client = FakeClient([tool_block("report_classification", [])], stop_reason="max_tokens")
    with pytest.raises(ClassifierError, match="se cortó"):
        classify_findings(FINDINGS, client)


def test_no_findings_makes_no_call() -> None:
    client = FakeClient([])
    assert classify_findings([], client) == []
    assert recommend_actions([], [], client) == []
    assert client.calls == []


def test_missing_tool_call_raises() -> None:
    client = FakeClient([SimpleNamespace(type="text", text="hello")])
    with pytest.raises(ClassifierError, match="no llamó"):
        classify_findings(FINDINGS, client)


def connection_error_client() -> SimpleNamespace:
    def create(**kwargs: Any) -> Any:
        raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://example.com"))

    return SimpleNamespace(messages=SimpleNamespace(create=create))


def test_connection_error_with_whitespace_in_key_gives_specific_hint(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  \nsk-fake-not-a-real-key")
    with pytest.raises(ClassifierError, match="espacios o saltos de línea") as info:
        classify_findings(FINDINGS, connection_error_client())
    assert "sk-fake" not in str(info.value)


def test_connection_error_with_clean_key_gives_network_hint(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-a-real-key")
    with pytest.raises(ClassifierError, match="No se pudo conectar") as info:
        classify_findings(FINDINGS, connection_error_client())
    assert "sk-fake" not in str(info.value)


def test_refusal_raises() -> None:
    client = FakeClient([], stop_reason="refusal")
    with pytest.raises(ClassifierError, match="rechazó"):
        classify_findings(FINDINGS, client)


def test_incomplete_ids_raise() -> None:
    client = FakeClient([tool_block("report_classification", [{"id": 0, "risk": "low", "reason": "r"}])])
    with pytest.raises(ClassifierError, match="no coincide"):
        classify_findings(FINDINGS, client)


def test_invalid_risk_value_raises() -> None:
    items = [{"id": i, "risk": "extreme", "reason": "r"} for i in range(2)]
    client = FakeClient([tool_block("report_classification", items)])
    with pytest.raises(ClassifierError, match="no válida"):
        classify_findings(FINDINGS, client)


def type_error_client(message: str) -> SimpleNamespace:
    def create(**kwargs: Any) -> Any:
        raise TypeError(message)

    return SimpleNamespace(messages=SimpleNamespace(create=create))


def test_missing_credentials_type_error_gives_a_clear_message() -> None:
    client = type_error_client('"Could not resolve authentication method. Expected one of api_key..."')
    with pytest.raises(ClassifierError, match="Faltan las credenciales"):
        classify_findings(FINDINGS, client)


def test_unrelated_type_error_is_not_hidden() -> None:
    with pytest.raises(TypeError, match="a real bug"):
        classify_findings(FINDINGS, type_error_client("a real bug"))


def test_el_error_de_la_api_incluye_el_motivo() -> None:
    import anthropic

    from data_guardian.classifier import _call_tool

    class Boom(anthropic.APIStatusError):
        def __init__(self) -> None:
            self.status_code = 400
            self.body = {"error": {"type": "invalid_request_error", "message": "Saldo   insuficiente."}}

    def fail(**_):
        raise Boom()

    client = SimpleNamespace(messages=SimpleNamespace(create=fail))
    with pytest.raises(ClassifierError, match=r"HTTP 400\): Saldo insuficiente\.$"):
        _call_tool(client, "sys", {"name": "t"}, "{}")
