from __future__ import annotations

from pathlib import Path

import pytest

from keptwise import db as storage
from keptwise.investments import Platform, save_platforms
from keptwise.seed import seed

DATA = Path(__file__).parent / "data"

# Two made-up investment platforms used throughout the tests.
PLATFORMS = [
    Platform(None, "Fund", 0.75, "vanguardinvestor.co.uk", True, ["VANGUARD"]),
    Platform(None, "Bitcoin", 0.25, "coinbase.com", False, ["COINBASE"]),
]


def configure(conn) -> None:
    """Investments plus a rule for the fake 'own savings account' transfers."""
    with conn:
        save_platforms(conn, [Platform(**vars(p)) for p in PLATFORMS])
        transfers = conn.execute(
            "SELECT id FROM categories WHERE name = 'Transfers between my accounts'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO rules(pattern, category_id, priority) VALUES ('A N OTHER SAVINGS', ?, 20)",
            (transfers,),
        )


@pytest.fixture
def conn():
    connection = storage.connect(":memory:")
    seed(connection)
    configure(connection)
    yield connection
    connection.close()


@pytest.fixture
def bare_conn():
    """A freshly seeded database with no investments set up."""
    connection = storage.connect(":memory:")
    seed(connection)
    yield connection
    connection.close()


@pytest.fixture
def may_csv() -> Path:
    return DATA / "statement_may.csv"


@pytest.fixture
def june_overlap_csv() -> Path:
    return DATA / "statement_june_overlap.csv"
