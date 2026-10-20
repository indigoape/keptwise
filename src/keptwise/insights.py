"""What's unusual this month, worked out from your own history.

Normal ranges
-------------
For each category we look at the previous (up to) 12 complete months and take the
*median* (the typical month) and the *median absolute deviation* (how much it usually
moves). Both ignore one-off extremes, so a single big month doesn't distort "normal".
A month is flagged only when it falls clearly outside that range, so categories that
always swing around aren't flagged for ordinary ups and downs.

Bills
-----
Regular bills are also checked one by one: a bill that has gone out nearly every
month but hasn't this month is flagged as missing, and a fixed bill whose amount
changes (say £27.00 to £31.50) is flagged as changed.
"""

from __future__ import annotations

import calendar
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import date

from .money import fmt
from .reports import months as all_months

LOOKBACK = 12  # months of history used for "normal"
MIN_HISTORY = 4  # months needed before we judge anything
Z = 2.0  # how far outside normal before flagging (in robust standard deviations)
MIN_GAP = 15_00  # ignore differences smaller than £15
OCCASIONAL_MIN = 100_00  # occasional categories need at least £100 to be flagged
MAD_TO_SD = 1.4826  # scales MAD to a standard deviation for normally distributed data


@dataclass(frozen=True)
class Normal:
    """A category's typical month. Amounts are *spend* (positive) or, for income
    categories, money received (positive)."""

    median: int
    low: int
    high: int
    months: int
    income: bool
    occasional: bool  # used in fewer than half of months
    highest: int  # biggest month in the history

    def describe(self) -> str:
        if self.occasional:
            return f"your biggest month before was {pounds(self.highest)}"
        if self.low == 0:
            return f"usually up to {pounds(self.high)}"
        return f"usually {pounds(self.low)} to {pounds(self.high)}"


def pounds(pence: int) -> str:
    """Whole pounds for ranges: £1,240."""
    return f"£{round(pence / 100):,}"


@dataclass(frozen=True)
class Flag:
    kind: str  # above | below | missing | changed
    category_id: int | None
    name: str
    bucket: str
    amount: int
    message: str
    good: bool  # True when it's good news (spent less, earned more)


def complete_months(conn: sqlite3.Connection) -> list[str]:
    """Months whose data covers the whole month (drops a partial first/last month)."""
    ms = all_months(conn)
    if not ms:
        return []
    bounds = {
        row["m"]: (date.fromisoformat(row["first"]), date.fromisoformat(row["last"]))
        for row in conn.execute(
            "SELECT substr(date,1,7) AS m, MIN(date) AS first, MAX(date) AS last "
            "FROM transactions GROUP BY m"
        )
    }
    result = []
    for i, m in enumerate(ms):
        first, last = bounds[m]
        days = calendar.monthrange(first.year, first.month)[1]
        if i == 0 and first.day > 4:
            continue
        if i == len(ms) - 1 and last.day < days - 3:
            continue
        result.append(m)
    return result


def monthly_net(conn: sqlite3.Connection) -> dict[tuple[int | None, str], int]:
    """(category_id, month) -> net spend (money out minus money in) in pence."""
    rows = conn.execute(
        "SELECT category_id, substr(date,1,7) AS m, -SUM(amount) AS spend "
        "FROM transactions GROUP BY category_id, m"
    )
    return {(r["category_id"], r["m"]): r["spend"] for r in rows}


def normal_range(values: list[int]) -> Normal | None:
    """Typical range from monthly *signed spend* values (income is negative)."""
    if len(values) < MIN_HISTORY:
        return None
    income = statistics.median(values) < 0
    vals = [-v for v in values] if income else list(values)
    med = statistics.median(vals)
    mad = statistics.median(abs(v - med) for v in vals)
    spread = max(MAD_TO_SD * mad, 0.15 * abs(med), 10_00)
    low = max(0, round(med - Z * spread))
    high = round(med + Z * spread)
    occasional = sum(1 for v in vals if v) < len(vals) / 2
    return Normal(round(med), low, high, len(vals), income, occasional, max(vals))


def normals(conn: sqlite3.Connection, month: str) -> dict[int | None, Normal]:
    """Normal range for every non-investment category, using months before ``month``."""
    history = [m for m in complete_months(conn) if m < month][-LOOKBACK:]
    if len(history) < MIN_HISTORY:
        return {}
    net = monthly_net(conn)
    cats = conn.execute("SELECT id, bucket FROM categories WHERE bucket != 'investment'").fetchall()
    ids: list[int | None] = [c["id"] for c in cats] + [None]
    result = {}
    for cid in ids:
        values = [net.get((cid, m), 0) for m in history]
        if not any(values):
            continue
        normal = normal_range(values)
        if normal:
            result[cid] = normal
    return result


def compare(value: int, normal: Normal) -> str:
    """'above', 'below' or 'normal' for a month's signed spend against its range."""
    amount = -value if normal.income else value
    if normal.occasional:
        # Things you only spend on now and then: flag only a clearly record month.
        big = amount >= OCCASIONAL_MIN and amount > 1.5 * normal.highest
        return "above" if big else "normal"
    if amount < 0:
        return "normal"  # more came in than went out (refunds, people paying back)
    if amount > normal.high and amount - normal.median >= MIN_GAP:
        return "above"
    if amount < normal.low and normal.median - amount >= MIN_GAP:
        return "below"
    return "normal"


def month_flags(conn: sqlite3.Connection, month: str) -> list[Flag]:
    """Everything worth a look in ``month``: unusual categories and bill problems."""
    complete = month in complete_months(conn)
    last_day_seen = conn.execute(
        "SELECT MAX(date) FROM transactions WHERE substr(date,1,7) = ?", (month,)
    ).fetchone()[0]
    if last_day_seen is None:
        return []
    seen_until = date.fromisoformat(last_day_seen)
    flags: list[Flag] = []
    flags += _bill_flags(conn, month, complete, seen_until)

    ranges = normals(conn, month)
    net = monthly_net(conn)
    names = {
        r["id"]: (r["name"], r["bucket"])
        for r in conn.execute("SELECT id, name, bucket FROM categories")
    }
    flagged = {f.category_id for f in flags}
    for cid, normal in ranges.items():
        if cid is None or cid in flagged:
            continue  # uncategorised spending has its own prompt
        name, bucket = names.get(cid, ("No category", "adhoc"))
        value = net.get((cid, month), 0)
        verdict = compare(value, normal)
        # Part months naturally look low, so only "above" counts until the month is in.
        if verdict == "normal" or (verdict == "below" and not complete):
            continue
        amount = -value if normal.income else value
        if normal.income:
            word = "more" if verdict == "above" else "less"
            message = f"{name}: {fmt(amount)} came in, {word} than normal ({normal.describe()})."
            good = verdict == "above"
        else:
            word = "more" if verdict == "above" else "less"
            message = f"{name}: {fmt(amount)}, {word} than normal ({normal.describe()})."
            good = verdict == "below"
        flags.append(Flag(verdict, cid, name, bucket, amount, message, good))

    order = {"missing": 0, "changed": 1, "above": 2, "below": 3}
    flags.sort(key=lambda f: (order[f.kind], -abs(f.amount)))
    return flags


def _bill_flags(
    conn: sqlite3.Connection, month: str, complete: bool, seen_until: date
) -> list[Flag]:
    history = [m for m in complete_months(conn) if m < month][-6:]
    if len(history) < 3:
        return []
    rows = conn.execute(
        "SELECT t.category_id, c.name, substr(t.date,1,7) AS m, t.date, t.amount "
        "FROM transactions t JOIN categories c ON c.id = t.category_id "
        "WHERE c.bucket = 'regular' AND t.amount < 0 ORDER BY t.date"
    ).fetchall()
    payments: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        payments.setdefault(r["category_id"], []).append(r)

    flags = []
    for cid, items in payments.items():
        name = items[0]["name"]
        by_month: dict[str, list[sqlite3.Row]] = {}
        for r in items:
            by_month.setdefault(r["m"], []).append(r)
        months_paid = [m for m in history if m in by_month]
        if len(months_paid) < len(history) - 1:
            continue  # not a reliable monthly bill
        this_month = by_month.get(month, [])

        if not this_month:
            usual_day = statistics.median(
                date.fromisoformat(by_month[m][0]["date"]).day for m in months_paid
            )
            if complete or seen_until.day > usual_day + 5:
                usual = statistics.median(
                    -sum(r["amount"] for r in by_month[m]) for m in months_paid
                )
                flags.append(
                    Flag(
                        "missing",
                        cid,
                        name,
                        "regular",
                        round(usual),
                        f"{name} hasn't gone out this month. It's usually "
                        f"{fmt(round(usual))} around the {_ordinal(round(usual_day))}.",
                        False,
                    )
                )
            continue

        # A fixed bill (same amount the last three times) that has changed.
        if len(this_month) != 1 or any(len(by_month[m]) != 1 for m in months_paid[-3:]):
            continue
        previous = [-by_month[m][0]["amount"] for m in months_paid[-3:]]
        current = -this_month[0]["amount"]
        if len(set(previous)) == 1 and current != previous[-1]:
            went = "up" if current > previous[-1] else "down"
            flags.append(
                Flag(
                    "changed",
                    cid,
                    name,
                    "regular",
                    current,
                    f"{name} went {went} from {fmt(previous[-1])} to {fmt(current)}.",
                    went == "down",
                )
            )
    return flags


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
