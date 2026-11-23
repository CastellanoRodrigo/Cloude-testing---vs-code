"""Agente de extracción: convierte PDF e imágenes en texto plano. Todo ocurre en local, sin red y sin la API."""

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
RICH_EXTENSIONS = {".pdf"} | IMAGE_EXTENSIONS

MAX_BYTES = 10 * 1024 * 1024  # tamaño máximo del archivo
MAX_PAGES = 20  # páginas máximas de un PDF
MAX_PIXELS = 50_000_000  # píxeles máximos de una imagen (o de una página dibujada para el OCR)
MIN_TEXT_CHARS = 20  # una página con menos texto que esto se trata como escaneada y pasa por OCR
RENDER_SCALE = 3.0  # factor para dibujar las páginas del PDF antes del OCR (unos 216 ppp)
OCR_LANGUAGES = ("spa", "eng")  # idiomas que se usan en el OCR, si Tesseract los tiene instalados
ENV_TESSERACT_CMD = "TESSERACT_CMD"  # ruta opcional del programa Tesseract


class ExtractionError(ValueError):
    """Se lanza cuando no se puede extraer el texto. Los mensajes nunca contienen el contenido del archivo."""


class Extraction(BaseModel):
    """Texto plano extraído de un archivo. `text` es contenido real: nunca lo registres ni lo envíes a la API."""

    text: str
    pages: int
    ocr_pages: int

    @property
    def method(self) -> str:
        """Cómo se obtuvo el texto: "text" (capa de texto), "ocr" (todo con OCR) o "mixed" (ambos)."""
        if self.ocr_pages == 0:
            return "text"
        return "ocr" if self.ocr_pages == self.pages else "mixed"


def _check_file(path: str | Path) -> Path:
    """Comprueba el tipo, que exista y su tamaño. Devuelve la ruta."""
    file = Path(path)
    if file.suffix.lower() not in RICH_EXTENSIONS:
        raise ExtractionError(f"Tipo de archivo no admitido '{file.suffix}'. Usa .pdf, .png o .jpg.")
    if not file.is_file():
        raise FileNotFoundError(f"No se encontró el archivo: {file}")
    if file.stat().st_size > MAX_BYTES:
        raise ExtractionError(f"El archivo supera los {MAX_BYTES // (1024 * 1024)} MB.")
    return file


def _ocr_language(pytesseract: Any) -> str:
    """Los idiomas de OCR disponibles entre los preferidos; inglés si no se puede consultar."""
    try:
        available = set(pytesseract.get_languages(config=""))
    except Exception:  # sin Tesseract o sin lista: el error real sale después, al leer
        return "eng"
    chosen = [lang for lang in OCR_LANGUAGES if lang in available]
    return "+".join(chosen) or "eng"


def _ocr(image: Any) -> str:
    """Lee el texto de una imagen (PIL) con Tesseract, en local."""
    try:
        import pytesseract
    except ImportError:
        raise ExtractionError(
            "Falta la biblioteca pytesseract. Instala las dependencias con pip install -r requirements.txt."
        ) from None
    if command := os.environ.get(ENV_TESSERACT_CMD):
        pytesseract.pytesseract.tesseract_cmd = command
    try:
        return pytesseract.image_to_string(image, lang=_ocr_language(pytesseract))
    except pytesseract.TesseractNotFoundError:
        raise ExtractionError(
            "No se encontró Tesseract. Instálalo y, si hace falta, define TESSERACT_CMD con su ruta."
        ) from None
    except pytesseract.TesseractError:
        raise ExtractionError("Tesseract no pudo leer el contenido del archivo.") from None


def _clean(text: str) -> str:
    """Normaliza saltos de línea y quita los espacios al final de cada línea."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip("\n")


def _extract_image(file: Path) -> Extraction:
    """OCR de una imagen PNG o JPG."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise ExtractionError(
            "Falta la biblioteca Pillow. Instala las dependencias con pip install -r requirements.txt."
        ) from None
    try:
        with Image.open(file) as opened:
            width, height = opened.size  # se lee de la cabecera, antes de decodificar toda la imagen
            if width * height > MAX_PIXELS:
                raise ExtractionError("La imagen es demasiado grande.")
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("No se pudo leer la imagen.") from None
    return Extraction(text=_clean(_ocr(image)), pages=1, ocr_pages=1)


def _page_to_image(document: Any, index: int) -> Any:
    """Dibuja una página del PDF como imagen PIL para pasarla por OCR."""
    try:
        page = document[index]
        width, height = page.get_size()
        if width * RENDER_SCALE * height * RENDER_SCALE > MAX_PIXELS:
            raise ExtractionError("Una página del PDF es demasiado grande para el OCR.")
        return page.render(scale=RENDER_SCALE).to_pil().convert("RGB")
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("No se pudo dibujar una página del PDF para el OCR.") from None


def _extract_pdf(file: Path) -> Extraction:
    """Texto de un PDF: la capa de texto de cada página y, si una página casi no tiene, OCR."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ExtractionError(
            "Falta la biblioteca pypdf. Instala las dependencias con pip install -r requirements.txt."
        ) from None
    try:
        reader = PdfReader(str(file))
        if reader.is_encrypted:
            raise ExtractionError("El PDF está protegido con contraseña.")
        total = len(reader.pages)
        if total > MAX_PAGES:
            raise ExtractionError(f"El PDF tiene más de {MAX_PAGES} páginas.")
        texts = [(page.extract_text() or "") for page in reader.pages]
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("No se pudo leer el PDF.") from None

    needs_ocr = [index for index, text in enumerate(texts) if len(text.strip()) < MIN_TEXT_CHARS]
    if needs_ocr:
        try:
            import pypdfium2
        except ImportError:
            raise ExtractionError(
                "Falta la biblioteca pypdfium2. Instala las dependencias con pip install -r requirements.txt."
            ) from None
        try:
            document = pypdfium2.PdfDocument(str(file))
        except Exception:
            raise ExtractionError("No se pudo abrir el PDF para el OCR.") from None
        try:
            for index in needs_ocr:
                texts[index] = _ocr(_page_to_image(document, index))
        finally:
            document.close()
    return Extraction(text=_clean("\n\n".join(texts)), pages=total, ocr_pages=len(needs_ocr))


def extract_text(path: str | Path) -> Extraction:
    """Extrae el texto plano de un PDF, PNG o JPG, abierto en solo lectura. Los errores nunca incluyen su contenido."""
    file = _check_file(path)
    if file.suffix.lower() == ".pdf":
        return _extract_pdf(file)
    return _extract_image(file)
