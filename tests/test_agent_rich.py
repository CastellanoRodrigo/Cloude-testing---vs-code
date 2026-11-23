"""Pruebas del agente con PDF e imágenes (extraer, escanear, clasificar, anonimizar y auditar). Solo datos falsos."""

import json
import shutil
from types import SimpleNamespace

import pytest
from PIL import Image

from data_guardian import extractor
from data_guardian.agent import ALLOWED_TOOLS, Agent, ToolNotAllowedError
from data_guardian.anonymizer import output_path
from test_extractor import FAKE_TEXT, make_blank_pdf, make_text_image, make_text_pdf

EMAIL = "ana@ejemplo.com"
DOC = "12345678Z"
HAS_TESSERACT = shutil.which("tesseract") is not None


class FakeClient:
    """Cliente falso de la API: responde con herramientas válidas y guarda lo que recibe."""

    def __init__(self) -> None:
        self.payloads: list[str] = []
        self.tools: list[str] = []
        self.messages = self

    def create(self, **kwargs):
        tool = kwargs["tools"][0]["name"]
        payload = kwargs["messages"][0]["content"]
        self.payloads.append(payload)
        self.tools.append(tool)
        if tool == "report_classification":
            data = {"items": [{"id": i["id"], "risk": "high", "reason": "dato personal"} for i in json.loads(payload)]}
        elif tool == "report_recommendations":
            data = {"items": [{"id": i["id"], "action": "anonymize", "reason": "es personal"} for i in json.loads(payload)]}
        else:
            data = {"findings": [], "recommendation": ""}
        block = SimpleNamespace(type="tool_use", name=tool, input=data)
        return SimpleNamespace(stop_reason="tool_use", content=[block])


@pytest.fixture
def fake_ocr(monkeypatch):
    """OCR falso que devuelve un texto con un correo y un documento, y cuenta las llamadas."""
    calls = []

    def fake(image):
        calls.append(image.size)
        return f"leído: {EMAIL}\nid {DOC}"

    monkeypatch.setattr(extractor, "_ocr", fake)
    return calls


def assert_no_real_values(client: FakeClient) -> None:
    """Regla 1: a la API solo llegan valores enmascarados."""
    for payload in client.payloads:
        assert EMAIL not in payload
        assert DOC not in payload


def test_six_allowed_tools_and_others_rejected():
    assert ALLOWED_TOOLS == ("extract", "scan", "classify", "recommend", "anonymize", "audit")
    with pytest.raises(ToolNotAllowedError):
        Agent().call_tool("shell", command="ls")


def test_output_path_is_txt_for_pdf_and_images_only(tmp_path):
    assert output_path(tmp_path / "a.pdf").name == "a_anonymized.txt"
    assert output_path(tmp_path / "a.PNG").name == "a_anonymized.txt"
    assert output_path(tmp_path / "a.jpg").name == "a_anonymized.txt"
    assert output_path(tmp_path / "a.csv").name == "a_anonymized.csv"
    assert output_path(tmp_path / "a.txt").name == "a_anonymized.txt"


def test_pdf_with_text_is_anonymized_into_a_txt_copy(tmp_path):
    path = tmp_path / "datos.pdf"
    path.write_bytes(make_text_pdf([FAKE_TEXT]))
    original = path.read_bytes()
    client = FakeClient()
    report = Agent(client).run(path)

    assert report.extraction_method == "text"
    assert [item.kind.value for item in report.items] == ["email", "document_id"]
    copy = tmp_path / "datos_anonymized.txt"
    assert report.output == str(copy)
    content = copy.read_text(encoding="utf-8")
    assert "[EMAIL_REDACTED]" in content and "[DOCUMENT_ID_REDACTED]" in content
    assert EMAIL not in content and DOC not in content
    assert path.read_bytes() == original
    assert report.audit is not None and report.audit.approved
    assert client.tools == ["report_classification", "report_recommendations", "report_audit"]
    assert_no_real_values(client)
    assert EMAIL not in report.model_dump_json() and DOC not in report.model_dump_json()


@pytest.mark.parametrize("name", ["foto.png", "foto.jpg"])
def test_image_is_read_with_ocr_and_anonymized(tmp_path, fake_ocr, name):
    path = make_text_image(tmp_path, name, "hola")
    client = FakeClient()
    report = Agent(client).run(path)

    assert report.extraction_method == "ocr"
    assert len(fake_ocr) == 1
    content = (tmp_path / "foto_anonymized.txt").read_text(encoding="utf-8")
    assert content == "leído: [EMAIL_REDACTED]\nid [DOCUMENT_ID_REDACTED]"
    assert_no_real_values(client)


def test_scanned_pdf_goes_through_the_same_flow(tmp_path, fake_ocr):
    client = FakeClient()
    report = Agent(client).run(make_blank_pdf(tmp_path, "escaneado.pdf"))
    assert report.extraction_method == "ocr"
    assert (tmp_path / "escaneado_anonymized.txt").is_file()
    assert_no_real_values(client)


def test_pdf_without_findings_creates_no_copy_and_audits_the_extracted_text(tmp_path):
    path = tmp_path / "limpio.pdf"
    path.write_bytes(make_text_pdf(["texto sin datos sensibles aqui"]))
    client = FakeClient()
    report = Agent(client).run(path)

    assert report.items == [] and report.output is None
    assert report.extraction_method == "text"
    assert client.tools == ["report_audit"]
    assert report.audit is not None and report.audit.approved
    assert not (tmp_path / "limpio_anonymized.txt").exists()


def test_blank_scanned_pdf_reports_no_data(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "_ocr", lambda image: "  \n ")
    client = FakeClient()
    report = Agent(client).run(make_blank_pdf(tmp_path, "vacio.pdf"))
    assert report.output is None and report.items == []
    assert report.audit is not None and report.audit.no_data
    assert client.tools == []


def test_existing_copy_fails_before_extracting(tmp_path, fake_ocr):
    path = make_blank_pdf(tmp_path, "escaneado.pdf")
    (tmp_path / "escaneado_anonymized.txt").write_text("ya existe", encoding="utf-8")
    with pytest.raises(FileExistsError):
        Agent(FakeClient()).run(path)
    assert fake_ocr == []
    assert (tmp_path / "escaneado_anonymized.txt").read_text(encoding="utf-8") == "ya existe"


def test_unreadable_pdf_raises_a_clear_error(tmp_path):
    path = tmp_path / "roto.pdf"
    path.write_bytes(f"esto no es un pdf {FAKE_TEXT}".encode())
    with pytest.raises(ValueError) as error:
        Agent(FakeClient()).run(path)
    assert EMAIL not in str(error.value)


def test_txt_flow_is_unchanged(tmp_path):
    path = tmp_path / "notas.txt"
    path.write_text(f"mail {EMAIL}\n", encoding="utf-8")
    client = FakeClient()
    report = Agent(client).run(path)
    assert report.extraction_method is None
    assert (tmp_path / "notas_anonymized.txt").read_text(encoding="utf-8") == "mail [EMAIL_REDACTED]\n"
    assert_no_real_values(client)


@pytest.mark.skipif(not HAS_TESSERACT, reason="Tesseract no está instalado")
def test_end_to_end_with_real_ocr(tmp_path):
    path = make_text_image(tmp_path, "real.png", f"{EMAIL} {DOC}")
    client = FakeClient()
    report = Agent(client).run(path)
    assert report.extraction_method == "ocr"
    content = (tmp_path / "real_anonymized.txt").read_text(encoding="utf-8")
    assert "[EMAIL_REDACTED]" in content and "[DOCUMENT_ID_REDACTED]" in content
    assert_no_real_values(client)
