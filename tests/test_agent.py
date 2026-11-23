"""Pruebas del agente con un cliente falso (sin llamadas reales a la API, solo datos falsos)."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from data_guardian.agent import ALLOWED_TOOLS, Agent, ToolNotAllowedError
from data_guardian.history import History

FAKE_KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
CONTENT = f"mail ana@example.com id 12345678Z key {FAKE_KEY}\n"


class FakeClient:
    """Answers classify/recommend/audit calls; recommends 'keep' for document IDs; the audit finds nothing."""

    audit_findings: list[dict[str, Any]] = []

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        tool = kwargs["tools"][0]["name"]
        if tool == "report_audit":
            answer = {"findings": self.audit_findings, "recommendation": "ok"}
            block = SimpleNamespace(type="tool_use", name=tool, input=answer)
            return SimpleNamespace(content=[block], stop_reason="tool_use")
        items = json.loads(kwargs["messages"][0]["content"])
        if tool == "report_classification":
            result = [{"id": i["id"], "risk": "high", "reason": "r"} for i in items]
        else:
            result = [
                {"id": i["id"], "action": "keep" if i["kind"] == "document_id" else "anonymize", "reason": "r"}
                for i in items
            ]
        block = SimpleNamespace(type="tool_use", name=tool, input={"items": result})
        return SimpleNamespace(content=[block], stop_reason="tool_use")


def test_allowlist_has_exactly_six_tools() -> None:
    assert ALLOWED_TOOLS == ("extract", "scan", "classify", "recommend", "anonymize", "audit")


def test_disallowed_tool_is_rejected() -> None:
    with pytest.raises(ToolNotAllowedError):
        Agent(FakeClient()).call_tool("shell", command="dir")


def test_full_run(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    client = FakeClient()
    report = Agent(client).run(original)

    copy = tmp_path / "data_anonymized.txt"
    assert report.output == str(copy)
    assert copy.read_text(encoding="utf-8") == (
        "mail [EMAIL_REDACTED] id 12345678Z key [API_KEY_REDACTED]\n"
    )
    assert original.read_text(encoding="utf-8") == CONTENT
    assert [i.anonymized for i in report.items] == [True, False, True]

    dumped = report.model_dump_json() + json.dumps(client.calls)
    assert "ana@example.com" not in dumped and "12345678Z" not in dumped and FAKE_KEY not in dumped


LEAK = {
    "id": 0,
    "location": "línea 1",
    "kind": "teléfono",
    "masked": "11******66",
    "risk": "alto",
    "suggested_label": "[PHONE_REDACTED]",
    "reason": "r",
}


def test_run_audits_the_copy_with_masked_text_only(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    client = FakeClient()
    report = Agent(client).run(original)
    assert report.audit is not None and report.audit.approved and report.audit_error is None
    audit_call = client.calls[-1]
    assert audit_call["tools"][0]["name"] == "report_audit"
    sent = audit_call["messages"][0]["content"]
    assert "12345678Z" not in sent and "[EMAIL_REDACTED]" in sent and "[API_KEY_REDACTED]" in sent


def test_audit_leaks_are_reported_but_the_copy_is_not_touched(tmp_path) -> None:
    class LeakyClient(FakeClient):
        audit_findings = [LEAK]

    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    report = Agent(LeakyClient()).run(original)
    assert report.audit is not None and report.audit.leaks == 1
    assert report.audit.headline == "VERIFICACIÓN: CON FUGAS (1)"
    copy = (tmp_path / "data_anonymized.txt").read_text(encoding="utf-8")
    assert copy == "mail [EMAIL_REDACTED] id 12345678Z key [API_KEY_REDACTED]\n"


def test_failed_audit_keeps_the_report_and_explains_why(tmp_path) -> None:
    class BrokenAuditClient(FakeClient):
        audit_findings = [{**LEAK, "risk": "extremo"}]

    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    report = Agent(BrokenAuditClient()).run(original)
    assert report.audit is None and report.audit_error == "El modelo devolvió una auditoría no válida."
    assert report.output is not None and len(report.items) == 3


def test_no_copy_audits_the_original_with_masked_text_only(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text("id 12345678Z\n", encoding="utf-8")
    client = FakeClient()
    report = Agent(client).run(original)
    assert report.output is None and report.audit is not None and report.audit.approved
    sent = client.calls[-1]["messages"][0]["content"]
    assert client.calls[-1]["tools"][0]["name"] == "report_audit" and "12345678Z" not in sent
    assert original.read_text(encoding="utf-8") == "id 12345678Z\n"


def test_existing_copy_fails_before_any_api_call(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    (tmp_path / "data_anonymized.txt").write_text("old", encoding="utf-8")
    client = FakeClient()
    with pytest.raises(FileExistsError):
        Agent(client).run(original)
    assert client.calls == []


def test_no_findings_makes_no_copy_and_only_the_audit_call(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text("nothing sensitive", encoding="utf-8")
    client = FakeClient()
    report = Agent(client).run(original)
    assert report.items == [] and report.output is None and report.audit is not None
    assert [c["tools"][0]["name"] for c in client.calls] == ["report_audit"]
    assert not (tmp_path / "data_anonymized.txt").exists()


class KeepEverythingClient(FakeClient):
    """A misbehaving model that recommends 'keep' for every finding."""

    def _create(self, **kwargs: Any) -> Any:
        response = super()._create(**kwargs)
        for item in response.content[0].input.get("items", []):  # the audit answer has no items
            if "action" in item:
                item["action"] = "keep"
        return response


def test_api_keys_are_anonymized_even_if_the_model_says_keep(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    report = Agent(KeepEverythingClient()).run(original)

    copy = (tmp_path / "data_anonymized.txt").read_text(encoding="utf-8")
    assert FAKE_KEY not in copy and "[API_KEY_REDACTED]" in copy
    assert "ana@example.com" in copy  # other kinds still follow the model
    assert [i.anonymized for i in report.items] == [False, False, True]


def test_agent_has_no_history_and_writes_no_extra_files(tmp_path) -> None:
    with pytest.raises(TypeError):
        Agent(FakeClient(), history=History.default())  # type: ignore[call-arg]
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    Agent(FakeClient()).run(original)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data.txt", "data_anonymized.txt"]
    assert not History.default().path.exists()
