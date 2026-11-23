"""Interfaz de línea de comandos con Typer (comando scan)."""

import os
from pathlib import Path

import typer

from data_guardian.agent import Agent, Report
from data_guardian.classifier import ClassifierError
from data_guardian.history import History
from data_guardian.i18n import VALUES_ES

app = typer.Typer(help="AI Data Guardian Lite: analiza archivos en busca de datos sensibles y anonimiza una copia.")


def load_env(path: str | Path = ".env") -> None:
    """Carga las líneas CLAVE=VALOR de un archivo .env sin pisar las variables que ya existan."""
    env_file = Path(path)
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if value.strip():
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def format_report(report: Report) -> str:
    """Muestra el informe como texto (solo valores enmascarados)."""
    note = (
        ["Nota: el texto se leyó con OCR local; puede tener errores y la copia (.txt) pierde el formato original."]
        if report.extraction_method in ("ocr", "mixed")
        else []
    )
    if not report.items:
        lines = [f"No se encontraron datos sensibles en {report.source}. No se creó ninguna copia."]
    else:
        lines = [f"Hallazgos en {report.source}:"]
        width = max(len(item.masked) for item in report.items)
        for item in report.items:
            lines.append(
                f"  línea {item.line:<4} {VALUES_ES[item.kind.value]:<24} {item.masked:<{width}} "
                f"riesgo={VALUES_ES[item.risk.value]:<8} acción={VALUES_ES[item.action.value]}"
                f"{'' if item.anonymized else ' (conservado)'}"
            )
        lines.append(f"Copia anonimizada: {report.output}" if report.output else "No se creó ninguna copia.")
    lines = note + lines
    if report.audit is not None:
        lines += ["", report.audit.headline]
        lines += [f"  {finding.line}" for finding in report.audit.findings]
        if report.audit.recommendation and not report.audit.approved:
            lines.append(report.audit.recommendation)
    elif report.audit_error:
        lines += ["", f"Auditoría no completada: {report.audit_error}"]
    return "\n".join(lines)


@app.callback()
def main() -> None:
    """AI Data Guardian Lite."""


@app.command()
def scan(file: Path = typer.Argument(..., help="Archivo TXT, CSV, PDF o imagen (png/jpg) a analizar.")) -> None:
    """Analiza FILE, clasifica los hallazgos con IA y guarda una copia anonimizada."""
    load_env()
    try:
        report = Agent().run(file)
        History.default().record(report, file.suffix)
    except (FileNotFoundError, FileExistsError, ValueError, ClassifierError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(format_report(report))


if __name__ == "__main__":
    app()
