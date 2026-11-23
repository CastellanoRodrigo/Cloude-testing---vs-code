"""Typer command-line interface (scan command)."""

import os
from pathlib import Path

import typer

from data_guardian.agent import Agent, Report
from data_guardian.classifier import ClassifierError
from data_guardian.history import History

app = typer.Typer(help="AI Data Guardian Lite: scan files for sensitive data and anonymize a copy.")


def load_env(path: str | Path = ".env") -> None:
    """Load KEY=VALUE lines from a .env file without overriding existing variables."""
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
    """Render the report as text (masked values only)."""
    if not report.items:
        lines = [f"No sensitive data found in {report.source}. No copy was created."]
    else:
        lines = [f"Findings in {report.source}:"]
        width = max(len(item.masked) for item in report.items)
        for item in report.items:
            lines.append(
                f"  line {item.line:<4} {item.kind.value:<12} {item.masked:<{width}} "
                f"risk={item.risk.value:<8} action={item.action.value}"
                f"{'' if item.anonymized else ' (kept)'}"
            )
        lines.append(f"Anonymized copy: {report.output}" if report.output else "No copy was created.")
    if report.audit is not None:
        lines += ["", report.audit.headline]
        lines += [f"  {finding.line}" for finding in report.audit.findings]
        if report.audit.recommendation and not report.audit.approved:
            lines.append(report.audit.recommendation)
    elif report.audit_error:
        lines += ["", f"Audit not completed: {report.audit_error}"]
    return "\n".join(lines)


@app.callback()
def main() -> None:
    """AI Data Guardian Lite."""


@app.command()
def scan(file: Path = typer.Argument(..., help="TXT or CSV file to scan.")) -> None:
    """Scan FILE, classify findings with AI and save an anonymized copy."""
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
