from datetime import date

from keptwise import db as storage
from keptwise.dates import strftime


def test_day_without_leading_zero():
    assert strftime(date(2026, 3, 5), "%-d %B %Y") == "5 March 2026"
    assert strftime(date(2026, 3, 15), "%a %-d %b") == "Sun 15 Mar"


def test_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("KEPTWISE_DB", str(tmp_path / "a.db"))
    monkeypatch.setenv("BOOKKEEPING_DB", str(tmp_path / "b.db"))
    assert storage.default_db_path() == tmp_path / "a.db"


def test_old_env_var_still_works(monkeypatch, tmp_path):
    monkeypatch.delenv("KEPTWISE_DB", raising=False)
    monkeypatch.setenv("BOOKKEEPING_DB", str(tmp_path / "b.db"))
    assert storage.default_db_path() == tmp_path / "b.db"


def test_source_checkout_keeps_a_database_from_before_the_rename(monkeypatch, tmp_path):
    monkeypatch.delenv("KEPTWISE_DB", raising=False)
    monkeypatch.delenv("BOOKKEEPING_DB", raising=False)
    (tmp_path / "pyproject.toml").write_text("")
    monkeypatch.setattr(storage, "PROJECT_ROOT", tmp_path)
    assert storage.default_db_path() == tmp_path / "data" / "keptwise.db"
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "bookkeeping.db").write_bytes(b"")
    assert storage.default_db_path() == tmp_path / "data" / "bookkeeping.db"
