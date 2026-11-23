"""Pruebas del historial de revisiones (solo datos falsos, bases de datos temporales)."""

import sqlite3
from datetime import date, datetime

import pytest

from data_guardian.agent import Agent, Report
from data_guardian.history import DEFAULT_PATH, History, HistoryError, bucket_start
from tests.test_agent import CONTENT, FakeClient

TODAY = date(2026, 10, 28)  # a Wednesday


@pytest.fixture
def history(tmp_path) -> History:
    return History(tmp_path / "history.db")


def make_report(tmp_path) -> Report:
    source = tmp_path / "make.txt"
    source.write_text(CONTENT, encoding="utf-8")
    return Agent(FakeClient()).run(source)


def empty_report() -> Report:
    return Report(source="x.txt", output=None, items=[])


def test_record_counts_findings_by_kind_and_anonymized(history, tmp_path) -> None:
    assert history.record(make_report(tmp_path), ".TXT", datetime(2026, 10, 28, 9, 0))
    [today] = history.series("day", 1, TODAY)
    assert (today.files, today.emails, today.document_ids, today.api_keys) == (1, 1, 1, 1)
    assert (today.findings, today.anonymized) == (3, 2)


def test_a_file_without_findings_still_counts_as_reviewed(history) -> None:
    history.record(empty_report(), ".csv", datetime(2026, 10, 28, 9, 0))
    [today] = history.series("day", 1, TODAY)
    assert (today.files, today.findings, today.anonymized) == (1, 0, 0)


def test_daily_series_is_zero_filled_oldest_first(history) -> None:
    history.record(empty_report(), ".txt", datetime(2026, 10, 26, 10, 0))
    history.record(empty_report(), ".txt", datetime(2026, 10, 28, 10, 0))
    history.record(empty_report(), ".txt", datetime(2026, 10, 28, 11, 0))
    series = history.series("day", 4, TODAY)
    assert [b.label for b in series] == ["2026-10-25", "2026-10-26", "2026-10-27", "2026-10-28"]
    assert [b.files for b in series] == [0, 1, 0, 2]


def test_weeks_start_on_monday(history) -> None:
    history.record(empty_report(), ".txt", datetime(2026, 10, 25, 23, 59))  # Sunday
    history.record(empty_report(), ".txt", datetime(2026, 10, 26, 0, 1))  # Monday
    series = history.series("week", 2, TODAY)
    assert [b.label for b in series] == ["2026-10-19", "2026-10-26"]
    assert [b.files for b in series] == [1, 1]
    assert bucket_start(date(2026, 10, 25), "week") == date(2026, 10, 19)


def test_months_cross_the_year_boundary(history) -> None:
    history.record(empty_report(), ".txt", datetime(2025, 12, 31, 12, 0))
    history.record(empty_report(), ".txt", datetime(2026, 1, 1, 12, 0))
    series = history.series("month", 3, date(2026, 1, 15))
    assert [b.label for b in series] == ["2025-11", "2025-12", "2026-01"]
    assert [b.files for b in series] == [0, 1, 1]


def test_rows_outside_the_range_are_ignored(history) -> None:
    history.record(empty_report(), ".txt", datetime(2026, 9, 1, 12, 0))
    assert sum(b.files for b in history.series("day", 7, TODAY)) == 0


def test_series_without_a_history_file_is_all_zeros_and_creates_nothing(tmp_path) -> None:
    missing = History(tmp_path / "nope" / "history.db")
    assert [b.files for b in missing.series("week", 3, TODAY)] == [0, 0, 0]
    assert not (tmp_path / "nope").exists()


def test_history_stores_no_names_and_no_values(history, tmp_path) -> None:
    history.record(make_report(tmp_path), ".txt")
    connection = sqlite3.connect(history.path)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(scans)")]
    dump = repr(connection.execute("SELECT * FROM scans").fetchall()) + history.path.read_bytes().decode("latin-1")
    connection.close()
    assert columns == ["id", "scanned_at", "extension", "total", "emails", "document_ids", "api_keys", "anonymized"]
    for secret in ("ana@example.com", "12345678Z", "sk-", "make.txt", "data.txt"):
        assert secret not in dump


def test_record_never_raises_and_reports_failure(tmp_path) -> None:
    broken = History(tmp_path)  # a directory is not a database file
    assert broken.record(empty_report(), ".txt") is False


def test_unreadable_history_raises_a_clear_error(tmp_path) -> None:
    bad = tmp_path / "history.db"
    bad.write_bytes(b"this is not a sqlite database" * 50)
    with pytest.raises(HistoryError, match="No se pudo leer el archivo de historial"):
        History(bad).series("day", 3, TODAY)


def test_default_path_follows_the_environment(monkeypatch, tmp_path) -> None:
    assert History.default().path != DEFAULT_PATH  # the conftest redirects it
    monkeypatch.delenv("DATA_GUARDIAN_HISTORY")
    assert History.default().path == DEFAULT_PATH
    assert DEFAULT_PATH.parent.name == "data"


def test_record_counts_each_reviewed_file(history, tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    history.record(Agent(FakeClient()).run(original), ".txt")
    clean = tmp_path / "clean.csv"
    clean.write_text("nothing here", encoding="utf-8")
    history.record(Agent(FakeClient()).run(clean), ".csv")
    [today] = history.series("day", 1)
    assert (today.files, today.findings, today.anonymized) == (2, 3, 2)


def test_agent_without_history_records_nothing(tmp_path) -> None:
    original = tmp_path / "data.txt"
    original.write_text(CONTENT, encoding="utf-8")
    Agent(FakeClient()).run(original)
    assert not History.default().path.exists()
