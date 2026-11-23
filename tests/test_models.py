"""Pruebas de la elección de modelo por paso: Haiku para clasificar y recomendar, Sonnet para auditar."""

from data_guardian.agent import Agent
from data_guardian.classifier import DEFAULT_AUDIT_MODEL, DEFAULT_CLASSIFIER_MODEL, audit_model, classifier_model
from tests.test_agent import CONTENT, FakeClient


class RecordingClient(FakeClient):
    """Guarda el modelo y los parámetros de cada llamada."""

    def _create(self, **kwargs):
        self.seen = getattr(self, "seen", [])
        self.seen.append((kwargs["tools"][0]["name"], kwargs["model"], "output_config" in kwargs))
        return super()._create(**kwargs)


def run(tmp_path, monkeypatch, **env) -> list:
    for name in ("CLASSIFIER_MODEL", "AUDIT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    client = RecordingClient()
    Agent(client).run(original)
    return client.seen


def test_defaults_are_haiku_for_simple_steps_and_sonnet_for_the_audit(tmp_path, monkeypatch) -> None:
    seen = run(tmp_path, monkeypatch)
    assert [(tool, model) for tool, model, _ in seen] == [
        ("report_classification", DEFAULT_CLASSIFIER_MODEL),
        ("report_recommendations", DEFAULT_CLASSIFIER_MODEL),
        ("report_audit", DEFAULT_AUDIT_MODEL),
    ]
    assert "haiku" in DEFAULT_CLASSIFIER_MODEL and "sonnet" in DEFAULT_AUDIT_MODEL


def test_effort_is_not_sent_to_haiku_but_is_sent_to_sonnet(tmp_path, monkeypatch) -> None:
    seen = run(tmp_path, monkeypatch)
    assert [effort for _, _, effort in seen] == [False, False, True]


def test_models_can_be_changed_from_the_environment(tmp_path, monkeypatch) -> None:
    seen = run(tmp_path, monkeypatch, CLASSIFIER_MODEL="claude-sonnet-5-5", AUDIT_MODEL="otro-modelo")
    assert [model for _, model, _ in seen] == ["claude-sonnet-5-5", "claude-sonnet-5-5", "otro-modelo"]


def test_blank_environment_values_fall_back_to_the_defaults(monkeypatch) -> None:
    monkeypatch.setenv("CLASSIFIER_MODEL", "  ")
    monkeypatch.setenv("AUDIT_MODEL", "")
    assert classifier_model() == DEFAULT_CLASSIFIER_MODEL and audit_model() == DEFAULT_AUDIT_MODEL
