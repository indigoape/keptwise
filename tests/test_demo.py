from datetime import date

import pytest
from click.testing import CliRunner

from keptwise import demo
from keptwise.cli import app
from keptwise.reports import months


def test_demo_fills_an_empty_database(bare_conn):
    added = demo.build(bare_conn, months=12, today=date(2026, 10, 20))
    assert added > 200
    assert months(bare_conn)[0] == "2025-10"
    assert months(bare_conn)[-1] == "2026-09"
    uncategorised = bare_conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE category_id IS NULL"
    ).fetchone()[0]
    assert uncategorised == 1  # left on purpose, to show that feature


def test_demo_is_the_same_every_time(bare_conn):
    from keptwise import db as storage
    from keptwise.seed import seed

    other = storage.connect(":memory:")
    seed(other)
    today = date(2026, 10, 20)
    demo.build(bare_conn, today=today)
    demo.build(other, today=today)
    query = "SELECT date, description, amount FROM transactions ORDER BY id"
    assert [tuple(r) for r in bare_conn.execute(query)] == [tuple(r) for r in other.execute(query)]


def test_demo_refuses_a_database_with_transactions(conn, may_csv):
    from keptwise.importer import import_path

    import_path(conn, may_csv)
    with pytest.raises(ValueError):
        demo.build(conn)


def test_demo_command_never_touches_a_real_database(tmp_path, may_csv):
    real = tmp_path / "real.db"
    runner = CliRunner()
    assert runner.invoke(app, ["--db", str(real), "import", str(may_csv)]).exit_code == 0
    result = runner.invoke(app, ["--db", str(real), "demo", "--no-serve"])
    assert result.exit_code != 0
    assert "already has transactions" in result.output


def test_months_back():
    assert demo.months_back(date(2026, 1, 15), 2) == [date(2025, 11, 1), date(2025, 12, 1)]


def test_demo_ignores_the_database_environment_variable(tmp_path, monkeypatch):
    from keptwise import db as storage

    real = tmp_path / "real.db"
    monkeypatch.setenv("KEPTWISE_DB", str(real))
    monkeypatch.setattr(storage, "default_db_path", lambda: real)
    result = CliRunner().invoke(app, ["demo", "--no-serve"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "demo.db").exists()
    assert not real.exists()
