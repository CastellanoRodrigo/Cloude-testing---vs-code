"""Registro local del uso de tokens de la API: solo números y nombres de herramienta, nunca contenido ni valores."""

import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from data_guardian.history import History, HistoryError

# Precio estimado en USD por millón de tokens (entrada, salida). Editable: los precios pueden cambiar.
PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (3.0, 15.0),
    "claude-haiku-5-5": (1.0, 5.0),  # aproximado, sin verificar
}
DEFAULT_PRICE = (3.0, 15.0)

ENV_BUDGET = "TOKEN_BUDGET"  # límite mensual de tokens (opcional, en el .env)
DEFAULT_BUDGET = 1_000_000
WARNING_AT = 0.70  # a partir de este porcentaje del presupuesto: ámbar
CRITICAL_AT = 0.90  # a partir de este porcentaje: rojo

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    used_at TEXT NOT NULL,
    tool TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL
)
"""

SETTINGS_SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
BUDGET_KEY = "token_budget"


class UsageRecord(BaseModel):
    """Una llamada a la API: qué herramienta la hizo y cuántos tokens usó."""

    used_at: datetime
    tool: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        """Tokens de entrada más salida."""
        return self.input_tokens + self.output_tokens

    @property
    def cost(self) -> float:
        """Costo estimado en USD."""
        return estimate_cost(self.model, self.input_tokens, self.output_tokens)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Costo estimado en USD según la tabla de precios (o el precio por defecto si el modelo no está)."""
    price_in, price_out = PRICES_PER_MILLION.get(model, DEFAULT_PRICE)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


class UsageLog:
    """Almacén SQLite de uso de tokens, en el mismo archivo que el historial."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def default(cls) -> "UsageLog":
        """El mismo archivo que usa `History.default()`."""
        return cls(History.default().path)

    def record(self, record: UsageRecord) -> bool:
        """Guarda una llamada. Nunca lanza excepciones: devuelve False si no se pudo guardar."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path)
            try:
                with connection:
                    connection.execute(SCHEMA)
                    connection.execute(
                        "INSERT INTO usage (used_at, tool, model, input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?)",
                        (
                            record.used_at.strftime("%Y-%m-%d %H:%M:%S"),
                            record.tool,
                            record.model,
                            record.input_tokens,
                            record.output_tokens,
                        ),
                    )
            finally:
                connection.close()
        except (sqlite3.Error, OSError):
            return False
        return True

    def saved_budget(self) -> int | None:
        """El presupuesto mensual guardado desde la interfaz, o None si no hay (o no se puede leer)."""
        if not self.path.is_file():
            return None
        try:
            connection = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
            try:
                row = connection.execute("SELECT value FROM settings WHERE key = ?", (BUDGET_KEY,)).fetchone()
            finally:
                connection.close()
        except sqlite3.Error:
            return None
        return int(row[0]) if row and row[0].isdigit() and int(row[0]) > 0 else None

    def save_budget(self, limit: int) -> bool:
        """Guarda el presupuesto mensual. Nunca lanza excepciones: devuelve False si no se pudo guardar."""
        if limit <= 0:
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path)
            try:
                with connection:
                    connection.execute(SETTINGS_SCHEMA)
                    connection.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (BUDGET_KEY, str(limit)),
                    )
            finally:
                connection.close()
        except (sqlite3.Error, OSError):
            return False
        return True

    def records(self, since: date | None = None) -> list[UsageRecord]:
        """Las llamadas desde `since` (todas si es None); ninguna si el registro aún no existe."""
        if not self.path.is_file():
            return []
        try:
            connection = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
            try:
                rows = connection.execute(
                    "SELECT used_at, tool, model, input_tokens, output_tokens FROM usage WHERE used_at >= ? ORDER BY used_at",
                    ((since or date.min).isoformat(),),
                ).fetchall()
            except sqlite3.OperationalError:  # la tabla aún no existe: no hubo llamadas
                return []
            finally:
                connection.close()
        except sqlite3.Error:
            raise HistoryError(f"No se pudo leer el archivo de uso de tokens: {self.path}") from None
        return [
            UsageRecord(
                used_at=datetime.strptime(used_at, "%Y-%m-%d %H:%M:%S"),
                tool=tool,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            for used_at, tool, model, input_tokens, output_tokens in rows
        ]


def record_response_usage(tool: str, model: str, response: Any, log: UsageLog | None = None) -> bool:
    """Registra el uso de una respuesta de la API. Nunca lanza excepciones; False si no hay datos de uso."""
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return False
    try:
        record = UsageRecord(
            used_at=datetime.now(), tool=tool, model=model, input_tokens=input_tokens, output_tokens=output_tokens
        )
        return (log or UsageLog.default()).record(record)
    except ValueError:
        return False


class Level(str, Enum):
    """Estado del presupuesto según el porcentaje usado."""

    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class UsageSummary:
    """Totales de un conjunto de llamadas."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0

    @property
    def total_tokens(self) -> int:
        """Tokens de entrada más salida."""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class BudgetStatus:
    """Cuánto del presupuesto mensual se ha usado."""

    used: int
    limit: int
    level: Level

    @property
    def fraction(self) -> float:
        """Porcentaje usado como fracción (puede pasar de 1.0)."""
        return self.used / self.limit


def budget_limit(log: UsageLog | None = None) -> int:
    """Presupuesto mensual: el guardado desde la interfaz, si no TOKEN_BUDGET (entero positivo), si no 1 000 000."""
    saved = (log or UsageLog.default()).saved_budget()
    if saved:
        return saved
    raw = os.environ.get(ENV_BUDGET, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_BUDGET


def budget_level(used: int, limit: int) -> Level:
    """Nivel de alerta: ámbar desde el 70 %, rojo desde el 90 %."""
    fraction = used / limit
    if fraction >= CRITICAL_AT:
        return Level.CRITICAL
    return Level.WARNING if fraction >= WARNING_AT else Level.OK


def summarize(records: list[UsageRecord]) -> UsageSummary:
    """Suma las llamadas."""
    return UsageSummary(
        calls=len(records),
        input_tokens=sum(r.input_tokens for r in records),
        output_tokens=sum(r.output_tokens for r in records),
        cost=sum(r.cost for r in records),
    )


def by_tool(records: list[UsageRecord]) -> dict[str, UsageSummary]:
    """Totales por herramienta, de mayor a menor uso de tokens."""
    groups: dict[str, list[UsageRecord]] = {}
    for record in records:
        groups.setdefault(record.tool, []).append(record)
    summaries = {tool: summarize(group) for tool, group in groups.items()}
    return dict(sorted(summaries.items(), key=lambda item: item[1].total_tokens, reverse=True))


def daily(records: list[UsageRecord], days: int, today: date | None = None) -> list[tuple[date, UsageSummary]]:
    """Los últimos `days` días, del más antiguo al más reciente, con ceros donde no hubo llamadas."""
    end = today or date.today()
    starts = [end - timedelta(days=back) for back in range(days - 1, -1, -1)]
    return [(day, summarize([r for r in records if r.used_at.date() == day])) for day in starts]


def month_status(log: UsageLog | None = None, today: date | None = None, limit: int | None = None) -> BudgetStatus:
    """Estado del presupuesto del mes en curso."""
    day = today or date.today()
    log = log or UsageLog.default()
    limit = limit or budget_limit(log)
    used = summarize(log.records(since=day.replace(day=1))).total_tokens
    return BudgetStatus(used=used, limit=limit, level=budget_level(used, limit))
