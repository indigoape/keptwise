"""SQLite storage with versioned schema migrations (``PRAGMA user_version``)."""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path

BUCKETS: dict[str, str] = {
    "regular": "Regulars",
    "cash": "Cash",
    "investment": "Investments",
    "adhoc": "Ad hoc",
}

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def default_db_path() -> Path:
    """Where the database lives.

    1. ``$KEPTWISE_DB`` (or the older ``$BOOKKEEPING_DB``) if set.
    2. ``data/keptwise.db`` inside the project, when running from a source checkout.
       A ``data/bookkeeping.db`` from before the rename is used if it exists.
    3. Otherwise the usual per-user data folder for the operating system.
    """
    env = os.environ.get("KEPTWISE_DB") or os.environ.get("BOOKKEEPING_DB")
    if env:
        return Path(env).expanduser()
    if (PROJECT_ROOT / "pyproject.toml").exists():
        legacy = PROJECT_ROOT / "data" / "bookkeeping.db"
        return legacy if legacy.exists() else PROJECT_ROOT / "data" / "keptwise.db"
    return user_data_dir() / "keptwise.db"


def user_data_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Keptwise"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Keptwise"
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "keptwise"


MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE categories (
        id           INTEGER PRIMARY KEY,
        name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
        bucket       TEXT NOT NULL CHECK (bucket IN ('regular','cash','investment','adhoc')),
        invest_share REAL CHECK (invest_share IS NULL OR (invest_share >= 0 AND invest_share <= 1))
    );

    CREATE TABLE rules (
        id          INTEGER PRIMARY KEY,
        pattern     TEXT NOT NULL,
        match_type  TEXT NOT NULL DEFAULT 'contains'
                    CHECK (match_type IN ('contains','starts_with','exact','regex')),
        direction   TEXT NOT NULL DEFAULT 'any' CHECK (direction IN ('any','in','out')),
        tx_type     TEXT,
        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        priority    INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE imports (
        id             INTEGER PRIMARY KEY,
        filename       TEXT NOT NULL,
        sha256         TEXT NOT NULL,
        source         TEXT NOT NULL,
        imported_at    TEXT NOT NULL DEFAULT (datetime('now')),
        rows_total     INTEGER NOT NULL,
        rows_added     INTEGER NOT NULL,
        rows_duplicate INTEGER NOT NULL,
        first_date     TEXT,
        last_date      TEXT
    );

    CREATE TABLE transactions (
        id          INTEGER PRIMARY KEY,
        fingerprint TEXT NOT NULL UNIQUE,
        date        TEXT NOT NULL,
        tx_type     TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL,
        amount      INTEGER NOT NULL,
        balance     INTEGER,
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        rule_id     INTEGER REFERENCES rules(id) ON DELETE SET NULL,
        manual      INTEGER NOT NULL DEFAULT 0,
        note        TEXT NOT NULL DEFAULT '',
        import_id   INTEGER REFERENCES imports(id) ON DELETE SET NULL
    );
    CREATE INDEX ix_transactions_date ON transactions(date);
    CREATE INDEX ix_transactions_category ON transactions(category_id);

    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # 2: links to investment accounts, and which investment is an ISA
    """
    ALTER TABLE categories ADD COLUMN url TEXT;
    ALTER TABLE categories ADD COLUMN is_isa INTEGER NOT NULL DEFAULT 0;
    UPDATE categories SET url = 'https://secure.vanguardinvestor.co.uk/', is_isa = 1
        WHERE name = 'Index' AND bucket = 'investment';
    UPDATE categories SET url = 'https://www.kraken.com/sign-in'
        WHERE name = 'BTC' AND bucket = 'investment';
    """,
    # 3: fewer ad hoc categories (see categories.simplify)
    "keptwise.categories:simplify",
]


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (and migrate) the database. ``":memory:"`` is supported for tests."""
    path = default_db_path() if path is None else path
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if _needs_upgrade(Path(path)):
            backup(path, force=True)  # always keep a copy from before an upgrade
    conn = sqlite3.connect(str(path), detect_types=0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    if str(path) != ":memory:":
        make_private(Path(path))
    return conn


def make_private(path: Path) -> None:
    """Let only you read a file (or folder), not other accounts on this computer."""
    if sys.platform == "win32":
        return  # files in your user folder are already private on Windows
    # Ignore files that aren't ours to change, or file systems without permissions.
    with suppress(OSError):
        path.chmod(0o700 if path.is_dir() else 0o600)


def _needs_upgrade(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    conn = sqlite3.connect(str(path))
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    return 0 < version < len(MIGRATIONS)


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for index, script in enumerate(MIGRATIONS[version:], start=version + 1):
        if script.startswith("keptwise."):
            # A Python migration, written as "module:function".
            import importlib

            module, func = script.split(":")
            with conn:
                getattr(importlib.import_module(module), func)(conn)
                conn.execute(f"PRAGMA user_version = {index}")
            continue
        with conn:
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {index}")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on error."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def backup(path: Path | str | None = None, *, keep: int = 30, force: bool = False) -> Path | None:
    """Copy the database into ``backups/`` next to it, at most once a day.

    Uses SQLite's online backup, so it's safe while the app is running. Keeps the
    newest ``keep`` copies. Returns the new backup's path, or None if none was made.
    """
    path = Path(default_db_path() if path is None else path)
    if str(path) == ":memory:" or not path.exists():
        return None
    folder = path.parent / "backups"
    folder.mkdir(exist_ok=True)
    make_private(folder)
    existing = sorted(folder.glob(f"{path.stem}-*.db"))
    today = datetime.now().strftime("%Y%m%d")
    if not force and any(p.stem.split("-")[-2] == today for p in existing):
        return None
    target = folder / f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S}.db"
    source = sqlite3.connect(str(path))
    dest = sqlite3.connect(str(target))
    try:
        with dest:
            source.backup(dest)
    finally:
        dest.close()
        source.close()
    make_private(target)
    for old in sorted(folder.glob(f"{path.stem}-*.db"))[:-keep]:
        old.unlink()
    return target
