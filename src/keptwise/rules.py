"""Categorisation rules.

A rule says "transactions whose description matches PATTERN go in CATEGORY". Rules can
be narrowed to money in or out, and to a bank transaction type (DD, FPO, DEB ...).
The highest-priority matching rule wins; ties go to the longest (most specific)
pattern. Transactions you categorise by hand are never touched by rules.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cached_property

MATCH_TYPES = {
    "contains": "contains",
    "starts_with": "starts with",
    "exact": "is exactly",
    "regex": "matches regex",
}
DIRECTIONS = {"any": "In or out", "in": "Money in", "out": "Money out"}


@dataclass(frozen=True)
class Rule:
    id: int
    pattern: str
    match_type: str
    direction: str
    tx_type: str | None
    category_id: int
    priority: int

    @cached_property
    def _regex(self) -> re.Pattern[str]:
        return re.compile(self.pattern, re.IGNORECASE)

    def matches(self, description: str, amount: int, tx_type: str = "") -> bool:
        if self.direction == "in" and amount <= 0:
            return False
        if self.direction == "out" and amount >= 0:
            return False
        if self.tx_type and self.tx_type.upper() != (tx_type or "").upper():
            return False
        desc = description.casefold()
        pattern = self.pattern.casefold()
        if self.match_type == "contains":
            return pattern in desc
        if self.match_type == "starts_with":
            return desc.startswith(pattern)
        if self.match_type == "exact":
            return desc == pattern
        if self.match_type == "regex":
            return bool(self._regex.search(description))
        return False


def validate_pattern(pattern: str, match_type: str) -> str | None:
    """Return an error message, or None if the pattern is usable."""
    if not pattern.strip():
        return "Enter some text to match."
    if match_type not in MATCH_TYPES:
        return "Unknown match type."
    if match_type == "regex":
        try:
            re.compile(pattern)
        except re.error as exc:
            return f"That regular expression isn't valid: {exc}."
    return None


def load_rules(conn: sqlite3.Connection) -> list[Rule]:
    rows = conn.execute(
        "SELECT id, pattern, match_type, direction, tx_type, category_id, priority FROM rules "
        "ORDER BY priority DESC, length(pattern) DESC, id"
    ).fetchall()
    return [Rule(**dict(row)) for row in rows]


def first_match(
    rules: Sequence[Rule], description: str, amount: int, tx_type: str = ""
) -> Rule | None:
    for rule in rules:
        if rule.matches(description, amount, tx_type):
            return rule
    return None


def categorise(conn: sqlite3.Connection, ids: Iterable[int] | None = None) -> int:
    """(Re)apply rules to non-manual transactions. Returns how many changed.

    Pass ``ids`` to limit to specific transactions (e.g. a fresh import).
    """
    rules = load_rules(conn)
    sql = "SELECT id, description, amount, tx_type, category_id, rule_id FROM transactions "
    sql += "WHERE manual = 0"
    params: list[int] = []
    if ids is not None:
        id_list = list(ids)
        if not id_list:
            return 0
        sql += f" AND id IN ({','.join('?' * len(id_list))})"
        params = id_list
    changed = 0
    updates = []
    for row in conn.execute(sql, params).fetchall():
        rule = first_match(rules, row["description"], row["amount"], row["tx_type"])
        new_category = rule.category_id if rule else None
        new_rule = rule.id if rule else None
        if (new_category, new_rule) != (row["category_id"], row["rule_id"]):
            updates.append((new_category, new_rule, row["id"]))
            changed += 1
    if updates:
        conn.executemany(
            "UPDATE transactions SET category_id = ?, rule_id = ? WHERE id = ?", updates
        )
    return changed


def suggest_pattern(description: str) -> str:
    """Suggest a reusable pattern from one description.

    Card payments carry store numbers and references ("TESCO STORES 2231",
    "eBay O*12-34567-89"); cutting at the first digit or ``*`` gives a pattern that
    matches every visit.
    """

    def cut(text: str) -> str:
        return re.split(r"[\d*]", text, maxsplit=1)[0].strip(" -_.")

    # Card processors put their own name first: "SUMUP *CORNER CAFE", "SQ *ACME BAKERY",
    # "ZETTLE_*MARKET STALL". Keep the processor and the merchant, drop the reference.
    prefix, star, rest = description.partition("*")
    if star and len(prefix.strip()) <= 8:
        merchant = cut(rest)
        if len(merchant) >= 3:
            start = len(prefix) + 1 + len(rest) - len(rest.lstrip(" -_."))
            return description[: start + len(merchant)]
    head = cut(description)
    return head if len(head) >= 3 else description.strip()


def count_matches(conn: sqlite3.Connection) -> dict[int, int]:
    """How many transactions each rule is currently responsible for."""
    rows = conn.execute(
        "SELECT rule_id, COUNT(*) AS n FROM transactions WHERE rule_id IS NOT NULL GROUP BY rule_id"
    ).fetchall()
    return {row["rule_id"]: row["n"] for row in rows}
