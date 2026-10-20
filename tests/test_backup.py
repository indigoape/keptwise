import sqlite3

from keptwise import db as storage


def test_backup_once_a_day_and_pruned(tmp_path):
    path = tmp_path / "bookkeeping.db"
    storage.connect(path).close()

    first = storage.backup(path)
    assert first is not None and first.exists()
    assert storage.backup(path) is None  # already backed up today

    for _ in range(5):
        storage.backup(path, keep=3, force=True)
    copies = list((tmp_path / "backups").glob("bookkeeping-*.db"))
    assert len(copies) <= 3

    # A backup is a working database
    conn = sqlite3.connect(copies[0])
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(storage.MIGRATIONS)


def test_no_backup_without_database(tmp_path):
    assert storage.backup(tmp_path / "missing.db") is None


def test_migration_adds_investment_links(tmp_path):
    """Databases from before links existed get them for the original two platforms."""
    import sqlite3

    path = tmp_path / "x.db"
    raw = sqlite3.connect(path)
    raw.executescript(storage.MIGRATIONS[0])
    raw.executescript(
        "INSERT INTO categories(name, bucket, invest_share) VALUES "
        "('Index', 'investment', 0.8), ('BTC', 'investment', 0.2);"
        "PRAGMA user_version = 1;"
    )
    raw.close()
    conn = storage.connect(path)
    rows = {
        r["name"]: (r["url"], r["is_isa"])
        for r in conn.execute("SELECT name, url, is_isa FROM categories WHERE bucket='investment'")
    }
    assert rows["Index"] == ("https://secure.vanguardinvestor.co.uk/", 1)
    assert rows["BTC"][0].startswith("https://www.kraken.com")
    assert rows["BTC"][1] == 0


def test_new_database_starts_without_investments(tmp_path):
    from keptwise.seed import seed

    conn = storage.connect(tmp_path / "new.db")
    seed(conn)
    count = conn.execute("SELECT COUNT(*) FROM categories WHERE bucket='investment'").fetchone()[0]
    assert count == 0


def test_simplify_merges_old_categories(tmp_path):
    """Databases made before October 2026 get the smaller set of categories."""
    import sqlite3

    from keptwise.categories import simplify

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.MIGRATIONS[0])
    conn.executescript(
        """
        INSERT INTO categories(id, name, bucket) VALUES
          (1, 'Eating & drinking out', 'adhoc'), (2, 'Coffee & snacks', 'adhoc'),
          (3, 'Cash machine', 'adhoc'), (4, 'Selling stuff', 'adhoc');
        INSERT INTO rules(pattern, category_id) VALUES ('GREGGS', 2), ('BAR', 1);
        INSERT INTO transactions(fingerprint, date, description, amount, category_id, manual)
          VALUES ('a', '2026-01-01', 'GREGGS', -300, 2, 1), ('b', '2026-01-02', 'BAR', -900, 1, 0);
        """
    )
    with conn:
        simplify(conn)
    names = {r["name"]: r["bucket"] for r in conn.execute("SELECT name, bucket FROM categories")}
    assert names == {"Eating & drinking": "adhoc", "Cash machine": "cash", "Selling stuff": "adhoc"}
    assert {r[0] for r in conn.execute("SELECT category_id FROM transactions")} == {1}
    assert conn.execute("SELECT manual FROM transactions WHERE fingerprint='a'").fetchone()[0] == 1
    assert {r[0] for r in conn.execute("SELECT category_id FROM rules")} == {1}
    with conn:
        simplify(conn)  # running again changes nothing
    assert len(names) == conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]


def test_fresh_database_has_nine_ad_hoc_categories(tmp_path):
    from keptwise.seed import seed

    conn = storage.connect(tmp_path / "new.db")
    seed(conn)
    adhoc = [r[0] for r in conn.execute("SELECT name FROM categories WHERE bucket='adhoc'")]
    assert len(adhoc) == 9
    assert "Apps & subscriptions" in adhoc and "Selling stuff" in adhoc


def test_upgrade_takes_a_backup_first(tmp_path):
    import sqlite3

    path = tmp_path / "bookkeeping.db"
    conn = sqlite3.connect(path)
    conn.executescript(storage.MIGRATIONS[0])
    conn.execute("PRAGMA user_version = 1")
    conn.close()

    storage.connect(path).close()
    copies = list((tmp_path / "backups").glob("bookkeeping-*.db"))
    assert len(copies) == 1
    assert sqlite3.connect(copies[0]).execute("PRAGMA user_version").fetchone()[0] == 1
    storage.connect(path).close()  # already upgraded: no second copy
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1
