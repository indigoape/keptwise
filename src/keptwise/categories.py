"""Merging and regrouping categories."""

from __future__ import annotations

import sqlite3


def category_id(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def merge(conn: sqlite3.Connection, source_id: int, target_id: int) -> int:
    """Move everything from one category into another, then delete the source.

    Transactions (including ones you categorised by hand) and rules all move across.
    Returns how many transactions moved. Doesn't commit.
    """
    if source_id == target_id:
        return 0
    moved = conn.execute(
        "UPDATE transactions SET category_id = ? WHERE category_id = ?", (target_id, source_id)
    ).rowcount
    conn.execute("UPDATE rules SET category_id = ? WHERE category_id = ?", (target_id, source_id))
    conn.execute("DELETE FROM categories WHERE id = ?", (source_id,))
    return moved


def merge_by_name(conn: sqlite3.Connection, sources: list[str], target: str) -> None:
    """Merge ``sources`` into ``target``, creating ``target`` by renaming the first
    source that exists. Missing names are skipped, so it's safe to run twice."""
    present = [(n, category_id(conn, n)) for n in sources]
    present = [(n, cid) for n, cid in present if cid is not None]
    target_id = category_id(conn, target)
    if target_id is None:
        if not present:
            return
        name, target_id = present.pop(0)
        conn.execute("UPDATE categories SET name = ? WHERE id = ?", (target, target_id))
    for _, cid in present:
        if cid != target_id:
            merge(conn, cid, target_id)


# October 2026: fewer ad hoc categories.
SIMPLER_CATEGORIES: list[tuple[list[str], str]] = [
    (["Eating & drinking out", "Coffee & snacks"], "Eating & drinking"),
    (["Transport", "Car & charging"], "Transport"),
    (["Shopping", "Health & beauty"], "Shopping"),
    (["Going out & events", "Trips & stays"], "Going out & trips"),
    (["Other bills & fees", "Charity"], "Other"),
]


def simplify(conn: sqlite3.Connection) -> None:
    """Database migration 3: merge the starter ad hoc categories into fewer groups
    and treat cash-machine withdrawals as Cash."""
    for sources, target in SIMPLER_CATEGORIES:
        merge_by_name(conn, sources, target)
    conn.execute("UPDATE categories SET bucket = 'cash' WHERE name = 'Cash machine'")
