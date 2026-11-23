"""Reemplazo determinista de los valores sensibles por etiquetas de redacción."""

from pathlib import Path

from data_guardian.extractor import RICH_EXTENSIONS
from data_guardian.scanner import SUPPORTED_EXTENSIONS, Finding, FindingKind, scan_text

LABELS: dict[FindingKind, str] = {
    FindingKind.EMAIL: "[EMAIL_REDACTED]",
    FindingKind.DOCUMENT_ID: "[DOCUMENT_ID_REDACTED]",
    FindingKind.API_KEY: "[API_KEY_REDACTED]",
}


def anonymize_text(text: str, findings: list[Finding]) -> str:
    """Reemplaza cada hallazgo del texto por su etiqueta (sin IA)."""
    result = text
    for finding in sorted(findings, key=lambda f: f.start, reverse=True):
        result = result[: finding.start] + LABELS[finding.kind] + result[finding.end :]
    return result


def output_path(path: str | Path) -> Path:
    """Devuelve la ruta de la copia anonimizada: <nombre>_anonymized.<ext>, junto al original.

    Si el original es un PDF o una imagen, la copia es de texto plano: <nombre>_anonymized.txt.
    """
    original = Path(path)
    suffix = ".txt" if original.suffix.lower() in RICH_EXTENSIONS else original.suffix
    return original.with_name(f"{original.stem}_anonymized{suffix}")


def write_anonymized_text(path: str | Path, text: str, findings: list[Finding]) -> Path:
    """Escribe la copia anonimizada (.txt) del texto extraído de un PDF o una imagen y devuelve su ruta.

    El original no se toca. Falla si la copia ya existe.
    """
    target = output_path(path)
    if target.exists():
        raise FileExistsError(f"La copia anonimizada ya existe: {target}")
    with open(target, "x", encoding="utf-8", newline="") as handle:
        handle.write(anonymize_text(text, findings))
    return target


def anonymize_file(path: str | Path, findings: list[Finding] | None = None) -> Path:
    """Escribe una copia anonimizada de un archivo TXT/CSV y devuelve su ruta.

    El original se abre en solo lectura y nunca se modifica. Falla si la copia ya existe.
    Si no se dan los hallazgos, primero se escanea el contenido del archivo.
    """
    original = Path(path)
    if original.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Tipo de archivo no admitido '{original.suffix}'. Usa .txt o .csv.")
    if not original.is_file():
        raise FileNotFoundError(f"No se encontró el archivo: {original}")
    target = output_path(original)
    if target.exists():
        raise FileExistsError(f"La copia anonimizada ya existe: {target}")
    try:
        with open(original, "r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except UnicodeDecodeError as exc:
        raise ValueError(f"El archivo no es texto UTF-8 válido: {original}") from exc
    if findings is None:
        findings = scan_text(text)
    with open(target, "x", encoding="utf-8", newline="") as handle:
        handle.write(anonymize_text(text, findings))
    return target
