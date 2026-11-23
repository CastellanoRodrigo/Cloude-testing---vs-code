"""Pruebas del agente de extracción. Solo datos falsos."""

import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter

from data_guardian import extractor
from data_guardian.extractor import ExtractionError, extract_text

FAKE_TEXT = "contacto ana@ejemplo.com documento 12345678Z"
HAS_TESSERACT = shutil.which("tesseract") is not None


def make_text_pdf(lines: list[str]) -> bytes:
    """Un PDF mínimo de una página con capa de texto (fuente Helvetica)."""
    content = "BT /F1 14 Tf 72 720 Td 18 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def make_text_image(tmp_path: Path, name: str, text: str) -> Path:
    """Una imagen con texto negro grande sobre fondo blanco."""
    image = Image.new("RGB", (1400, 220), "white")
    ImageDraw.Draw(image).text((30, 60), text, fill="black", font=ImageFont.load_default(size=64))
    path = tmp_path / name
    image.save(path)
    return path


def make_blank_pdf(tmp_path: Path, name: str, pages: int = 1) -> Path:
    """Un PDF solo de imagen (sin capa de texto), como un documento escaneado."""
    images = [Image.new("RGB", (600, 800), "white") for _ in range(pages)]
    path = tmp_path / name
    images[0].save(path, "PDF", save_all=True, append_images=images[1:])
    return path


@pytest.fixture
def fake_ocr(monkeypatch):
    """Reemplaza el OCR por uno falso que cuenta las llamadas."""
    calls = []

    def fake(image):
        calls.append(image.size)
        return "texto  leído por OCR \r\n  segunda línea  "

    monkeypatch.setattr(extractor, "_ocr", fake)
    return calls


def test_pdf_with_text_layer_needs_no_ocr(tmp_path, fake_ocr):
    path = tmp_path / "datos.pdf"
    path.write_bytes(make_text_pdf([FAKE_TEXT]))
    result = extract_text(path)
    assert "ana@ejemplo.com" in result.text
    assert "12345678Z" in result.text
    assert (result.pages, result.ocr_pages, result.method) == (1, 0, "text")
    assert fake_ocr == []


def test_scanned_pdf_goes_through_ocr(tmp_path, fake_ocr):
    result = extract_text(make_blank_pdf(tmp_path, "escaneado.pdf"))
    assert result.text == "texto  leído por OCR\n  segunda línea"
    assert (result.pages, result.ocr_pages, result.method) == (1, 1, "ocr")
    assert len(fake_ocr) == 1


def test_mixed_pdf_only_ocrs_pages_without_text(tmp_path, fake_ocr):
    text_pdf = tmp_path / "texto.pdf"
    text_pdf.write_bytes(make_text_pdf([FAKE_TEXT]))
    writer = PdfWriter()
    writer.append(PdfReader(str(text_pdf)))
    writer.append(PdfReader(str(make_blank_pdf(tmp_path, "escaneado.pdf"))))
    mixed = tmp_path / "mixto.pdf"
    with open(mixed, "wb") as handle:
        writer.write(handle)
    result = extract_text(mixed)
    assert (result.pages, result.ocr_pages, result.method) == (2, 1, "mixed")
    assert "ana@ejemplo.com" in result.text
    assert "texto  leído por OCR" in result.text
    assert len(fake_ocr) == 1


@pytest.mark.parametrize("name", ["foto.png", "foto.jpg", "FOTO.JPEG"])
def test_images_go_through_ocr(tmp_path, fake_ocr, name):
    path = make_text_image(tmp_path, name, "hola")
    if path.suffix.lower() == ".jpeg":
        path = path.rename(path.with_suffix(".jpeg"))
    result = extract_text(path)
    assert result.text == "texto  leído por OCR\n  segunda línea"
    assert (result.pages, result.ocr_pages, result.method) == (1, 1, "ocr")


@pytest.mark.skipif(not HAS_TESSERACT, reason="Tesseract no está instalado")
def test_real_ocr_reads_an_image(tmp_path):
    result = extract_text(make_text_image(tmp_path, "real.png", "Hola ejemplo 12345"))
    assert "ejemplo" in result.text.lower()
    assert "12345" in result.text


@pytest.mark.skipif(not HAS_TESSERACT, reason="Tesseract no está instalado")
def test_real_ocr_reads_a_scanned_pdf(tmp_path):
    image_path = make_text_image(tmp_path, "pagina.png", "Hola ejemplo 12345")
    pdf = tmp_path / "escaneado.pdf"
    Image.open(image_path).convert("RGB").save(pdf, "PDF")
    result = extract_text(pdf)
    assert result.method == "ocr"
    assert "12345" in result.text


def test_unsupported_type(tmp_path):
    path = tmp_path / "notas.docx"
    path.write_bytes(b"x")
    with pytest.raises(ExtractionError, match="no admitido"):
        extract_text(path)


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_text(tmp_path / "no_existe.pdf")


def test_file_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "MAX_BYTES", 10)
    path = tmp_path / "grande.pdf"
    path.write_bytes(make_text_pdf([FAKE_TEXT]))
    with pytest.raises(ExtractionError, match="supera"):
        extract_text(path)


def test_too_many_pages(tmp_path, monkeypatch, fake_ocr):
    monkeypatch.setattr(extractor, "MAX_PAGES", 1)
    with pytest.raises(ExtractionError, match="más de 1 páginas"):
        extract_text(make_blank_pdf(tmp_path, "largo.pdf", pages=2))
    assert fake_ocr == []


def test_image_too_large(tmp_path, monkeypatch, fake_ocr):
    monkeypatch.setattr(extractor, "MAX_PIXELS", 100)
    with pytest.raises(ExtractionError, match="demasiado grande"):
        extract_text(make_text_image(tmp_path, "enorme.png", "hola"))
    assert fake_ocr == []


def test_encrypted_pdf(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt("clave-falsa")
    path = tmp_path / "protegido.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)
    with pytest.raises(ExtractionError, match="contraseña"):
        extract_text(path)


def test_corrupt_pdf_error_hides_content(tmp_path):
    path = tmp_path / "roto.pdf"
    path.write_bytes(f"esto no es un pdf {FAKE_TEXT}".encode())
    with pytest.raises(ExtractionError) as error:
        extract_text(path)
    assert "ana@ejemplo.com" not in str(error.value)
    assert "12345678Z" not in str(error.value)


def test_corrupt_image_error_hides_content(tmp_path):
    path = tmp_path / "rota.png"
    path.write_bytes(f"no es una imagen {FAKE_TEXT}".encode())
    with pytest.raises(ExtractionError) as error:
        extract_text(path)
    assert "ana@ejemplo.com" not in str(error.value)


def test_missing_tesseract_gives_clear_message(tmp_path, monkeypatch):
    monkeypatch.setenv(extractor.ENV_TESSERACT_CMD, str(tmp_path / "no_existe_tesseract"))
    with pytest.raises(ExtractionError, match="No se encontró Tesseract"):
        extract_text(make_blank_pdf(tmp_path, "escaneado.pdf"))


def test_original_is_not_modified(tmp_path, fake_ocr):
    path = make_blank_pdf(tmp_path, "escaneado.pdf")
    before = path.read_bytes()
    extract_text(path)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["escaneado.pdf"]
