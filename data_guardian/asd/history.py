"""Local scan history for the dashboard: dates, file types and counts only, never names or values."""

import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from data_guardian.scanner import FindingKind

if TYPE_CHECKING:
    from data_guardian.agent import Report

Period = Literal["day", "week", "month"]

ENV_VAR = "DATA_GUARDIAN_HISTORY"
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at TEXT NOT NULL,
    extension TEXT NOT NULL,
    total INTEGER NOT NULL,
    emails INTEGER NOT NULL,
    document_ids INTEGER NOT NULL,
    api_keys INTEGER NOT NULL,
    anonymized INTEGER NOT NULL
)
"""


class HistoryError(Exception):
    """Raised when the history cannot be read."""


@dataclass(frozen=True)
class Bucket:
    """Totals for one day, week (starting Monday) or month."""

    start: date
    label: str
    files: int = 0
    emails: int = 0
    document_ids: int = 0
    api_keys: int = 0
    anonymized: int = 0

    @property
    def findings(self) -> int:
        """All findings in the bucket."""
        return self.emails + self.document_ids + self.api_keys


def bucket_start(day: date, period: Period) -> date:
    """First day of the day/week/month that contains `day`."""
    if period == "week":
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    return day


def _shift(start: date, period: Period, back: int) -> date:
    """The bucket start `back` periods before `start`."""
    if period == "day":
        return start - timedelta(days=back)
    if period == "week":
        return start - timedelta(weeks=back)
    months = start.year * 12 + start.month - 1 - back
    return date(months // 12, months % 12 + 1, 1)


def _label(start: date, period: Period) -> str:
    """ISO labels sort in time order: 2026-10-28, or 2026-10 for months."""
    return start.strftime("%Y-%m") if period == "month" else start.isoformat()


class History:
    """SQLite store of one row per reviewed file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def default(cls) -> "History":
        """The history at $DATA_GUARDIAN_HISTORY, else data/history.db in the project."""
        return cls(os.environ.get(ENV_VAR) or DEFAULT_PATH)

    def record(self, report: "Report", extension: str, when: datetime | None = None) -> bool:
        """Save one reviewed file. Never raises: returns False if it could not be saved."""
        counts = {kind: 0 for kind in FindingKind}
        for item in report.items:
            counts[item.kind] += 1
        row = (
            (when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
            extension.lower(),
            len(report.items),
            counts[FindingKind.EMAIL],
            counts[FindingKind.DOCUMENT_ID],
            counts[FindingKind.API_KEY],
            sum(item.anonymized for item in report.items),
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path)
            try:
                with connection:
                    connection.execute(SCHEMA)
                    connection.execute(
                        "INSERT INTO scans (scanned_at, extension, total, emails, document_ids, api_keys, anonymized) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        row,
                    )
            finally:
                connection.close()
        except (sqlite3.Error, OSError):
            return False
        return True

    def series(self, period: Period, count: int, today: date | None = None) -> list[Bucket]:
        """The last `count` buckets, oldest first, with zeros where nothing was reviewed."""
        current = bucket_start(today or date.today(), period)
        starts = [_shift(current, period, back) for back in range(count - 1, -1, -1)]
        totals = {start: [0, 0, 0, 0, 0] for start in starts}
        for scanned_at, emails, document_ids, api_keys, anonymized in self._rows(starts[0]):
            start = bucket_start(datetime.strptime(scanned_at, "%Y-%m-%d %H:%M:%S").date(), period)
            if start in totals:
                for index, value in enumerate((1, emails, document_ids, api_keys, anonymized)):
                    totals[start][index] += value
        return [Bucket(start, _label(start, period), *totals[start]) for start in starts]

    def _rows(self, since: date) -> list[tuple[str, int, int, int, int]]:
        """Rows from `since` on; none if the history does not exist yet."""
        if not self.path.is_file():
            return []
        try:
            connection = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
            try:
                return connection.execute(
                    "SELECT scanned_at, emails, document_ids, api_keys, anonymized FROM scans WHERE scanned_at >= ?",
                    (since.isoformat(),),
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.Error:
            raise HistoryError(f"Could not read the history file: {self.path}") from None
