"""Monthly summaries and the investment plan.

How a month adds up:

* every transaction belongs to one bucket: Regulars, Cash, Investments or Ad hoc
  (uncategorised transactions count as Ad hoc);
* the month's *surplus* is the net of Regulars + Cash + Ad hoc, i.e. what the bank
  balance did ignoring money moved into investments;
* next month, the surplus is split between investment categories by the
  shares set in Settings. A negative surplus means nothing to invest.
"""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from .db import BUCKETS
from .money import share


@dataclass
class BucketTotals:
    key: str
    label: str
    money_in: int = 0
    money_out: int = 0  # positive number

    @property
    def net(self) -> int:
        return self.money_in - self.money_out


@dataclass
class Allocation:
    category_id: int
    name: str
    share: float
    amount: int  # what to invest next month


@dataclass
class MonthSummary:
    month: str  # YYYY-MM
    buckets: dict[str, BucketTotals]
    allocations: list[Allocation]
    invested: dict[str, int]  # investment category -> money put in this month
    withdrawn: dict[str, int]  # investment category -> money taken out this month
    transaction_count: int
    uncategorised_count: int
    first_date: date | None
    last_date: date | None
    opening_balance: int | None = None
    closing_balance: int | None = None
    category_spend: list[CategorySpend] = field(default_factory=list)

    @property
    def label(self) -> str:
        return month_label(self.month)

    @property
    def next_label(self) -> str:
        return month_label(shift_month(self.month, 1))

    @property
    def surplus(self) -> int:
        return sum(self.buckets[k].net for k in ("regular", "cash", "adhoc"))

    @property
    def to_invest(self) -> int:
        return sum(a.amount for a in self.allocations)

    @property
    def total_in(self) -> int:
        return sum(b.money_in for k, b in self.buckets.items() if k != "investment")

    @property
    def total_out(self) -> int:
        return sum(b.money_out for k, b in self.buckets.items() if k != "investment")

    @property
    def bank_net(self) -> int:
        return sum(b.net for b in self.buckets.values())

    @property
    def total_invested(self) -> int:
        return sum(self.invested.values())

    is_first_month: bool = False
    is_last_month: bool = False

    @property
    def is_partial(self) -> bool:
        """True if the statement looks like it stops short at either end.

        Only the first and last imported months can be partial: in between, the
        neighbouring months prove the data is continuous.
        """
        if not self.first_date or not self.last_date:
            return True
        year, mon = map(int, self.month.split("-"))
        last_day = calendar.monthrange(year, mon)[1]
        starts_late = self.is_first_month and self.first_date.day > 4
        ends_early = self.is_last_month and self.last_date.day < last_day - 3
        return starts_late or ends_early


@dataclass
class CategorySpend:
    category_id: int | None
    name: str
    bucket: str
    money_in: int
    money_out: int
    count: int

    @property
    def net_spend(self) -> int:
        """Spending after refunds and money paid back (positive = spent)."""
        return self.money_out - self.money_in


def month_label(month: str, fmt: str = "%B %Y") -> str:
    year, mon = map(int, month.split("-"))
    return date(year, mon, 1).strftime(fmt)


def shift_month(month: str, delta: int) -> str:
    year, mon = map(int, month.split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def months(conn: sqlite3.Connection) -> list[str]:
    """All months with data, oldest first."""
    rows = conn.execute(
        "SELECT DISTINCT substr(date, 1, 7) AS m FROM transactions ORDER BY m"
    ).fetchall()
    return [row["m"] for row in rows]


def investment_categories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, name, COALESCE(invest_share, 0) AS share, url, is_isa FROM categories "
        "WHERE bucket = 'investment' ORDER BY share DESC, name"
    ).fetchall()


def category_spend(
    conn: sqlite3.Connection,
    month: str | None = None,
    bucket: str | None = None,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[CategorySpend]:
    sql = """
        SELECT t.category_id, COALESCE(c.name, 'No category') AS name,
               COALESCE(c.bucket, 'adhoc') AS bucket,
               SUM(CASE WHEN t.amount > 0 THEN t.amount ELSE 0 END) AS money_in,
               SUM(CASE WHEN t.amount < 0 THEN -t.amount ELSE 0 END) AS money_out,
               COUNT(*) AS count
        FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
        WHERE 1 = 1
    """
    params: list[str] = []
    if month:
        sql += " AND substr(t.date, 1, 7) = ?"
        params.append(month)
    if bucket:
        sql += " AND COALESCE(c.bucket, 'adhoc') = ?"
        params.append(bucket)
    if date_from:
        sql += " AND t.date >= ?"
        params.append(date_from.isoformat())
    if date_to:
        sql += " AND t.date <= ?"
        params.append(date_to.isoformat())
    sql += " GROUP BY t.category_id ORDER BY (money_out - money_in) DESC"
    return [CategorySpend(**dict(row)) for row in conn.execute(sql, params).fetchall()]


def summarise(conn: sqlite3.Connection, month: str) -> MonthSummary:
    spend = category_spend(conn, month)
    buckets = {key: BucketTotals(key, label) for key, label in BUCKETS.items()}
    invested: dict[str, int] = {row["name"]: 0 for row in investment_categories(conn)}
    withdrawn: dict[str, int] = dict.fromkeys(invested, 0)
    for item in spend:
        totals = buckets[item.bucket]
        totals.money_in += item.money_in
        totals.money_out += item.money_out
        if item.bucket == "investment":
            invested[item.name] = item.money_out
            withdrawn[item.name] = item.money_in

    stats = conn.execute(
        "SELECT COUNT(*) AS n, SUM(category_id IS NULL) AS uncategorised, "
        "MIN(date) AS first, MAX(date) AS last FROM transactions "
        "WHERE substr(date, 1, 7) = ?",
        (month,),
    ).fetchone()

    summary = MonthSummary(
        month=month,
        buckets=buckets,
        allocations=[],
        invested=invested,
        withdrawn=withdrawn,
        transaction_count=stats["n"],
        uncategorised_count=stats["uncategorised"] or 0,
        first_date=date.fromisoformat(stats["first"]) if stats["first"] else None,
        last_date=date.fromisoformat(stats["last"]) if stats["last"] else None,
        category_spend=spend,
    )
    surplus = max(summary.surplus, 0)
    summary.allocations = [
        Allocation(row["id"], row["name"], row["share"], share(surplus, row["share"]))
        for row in investment_categories(conn)
    ]
    summary.opening_balance, summary.closing_balance = _balances(conn, month)
    all_months = months(conn)
    summary.is_first_month = bool(all_months) and month == all_months[0]
    summary.is_last_month = bool(all_months) and month == all_months[-1]
    return summary


def _balances(conn: sqlite3.Connection, month: str) -> tuple[int | None, int | None]:
    """Balance at the start and end of the month.

    Statements list same-day transactions in no reliable order, so the closing balance
    is found by following the running balance: on the last day, the final transaction
    is the one no other transaction continues from.
    """
    rows = conn.execute(
        "SELECT date, amount, balance FROM transactions WHERE substr(date,1,7) = ? "
        "ORDER BY date, id",
        (month,),
    ).fetchall()
    if not rows or any(r["balance"] is None for r in rows):
        return None, None
    last_day = [r for r in rows if r["date"] == rows[-1]["date"]]
    starts = {r["balance"] - r["amount"] for r in last_day}
    ends = [r["balance"] for r in last_day if r["balance"] not in starts]
    closing = ends[0] if len(ends) == 1 else last_day[-1]["balance"]
    opening = closing - sum(r["amount"] for r in rows)
    return opening, closing


def history(conn: sqlite3.Connection) -> list[MonthSummary]:
    return [summarise(conn, m) for m in months(conn)]


@dataclass
class PlanRow:
    """What a month's surplus said to invest, against what was put in the month after."""

    month: str  # the month the surplus was earned
    surplus: int
    planned: dict[str, int]
    actual: dict[str, int]  # put in during the following month
    withdrawn: dict[str, int]  # taken out during the following month
    actual_known: bool  # False if the following month has no data yet

    @property
    def label(self) -> str:
        return month_label(self.month, "%b %Y")

    @property
    def invest_month_label(self) -> str:
        return month_label(shift_month(self.month, 1), "%b %Y")

    @property
    def planned_total(self) -> int:
        return sum(self.planned.values())

    @property
    def actual_total(self) -> int:
        return sum(self.actual.values())


def investment_plan(conn: sqlite3.Connection) -> list[PlanRow]:
    summaries = {s.month: s for s in history(conn)}
    rows = []
    for month, summary in summaries.items():
        following = summaries.get(shift_month(month, 1))
        rows.append(
            PlanRow(
                month=month,
                surplus=summary.surplus,
                planned={a.name: a.amount for a in summary.allocations},
                actual=dict(following.invested) if following else {},
                withdrawn=dict(following.withdrawn) if following else {},
                actual_known=following is not None,
            )
        )
    return rows


def top_transactions(
    conn: sqlite3.Connection, month: str, bucket: str = "adhoc", limit: int = 8
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT t.*, COALESCE(c.name, 'No category') AS category
        FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
        WHERE substr(t.date, 1, 7) = ? AND COALESCE(c.bucket, 'adhoc') = ? AND t.amount < 0
        ORDER BY t.amount ASC LIMIT ?
        """,
        (month, bucket, limit),
    ).fetchall()


# ------------------------------------------------------------------------ tax years

ISA_ALLOWANCE = 20_000_00  # per tax year


def tax_year_of(day: date) -> int:
    """The year a UK tax year starts in: 6 April 2026 to 5 April 2027 is 2026."""
    return day.year if (day.month, day.day) >= (4, 6) else day.year - 1


def tax_year_label(start_year: int) -> str:
    return f"{start_year}/{(start_year + 1) % 100:02d}"


@dataclass
class TaxYear:
    start_year: int
    buckets: dict[str, BucketTotals]
    categories: list[CategorySpend]
    invested: dict[str, int]
    withdrawn: dict[str, int]
    isa_contributions: int
    isa_names: list[str]
    first_date: date | None
    last_date: date | None

    @property
    def label(self) -> str:
        return tax_year_label(self.start_year)

    @property
    def start(self) -> date:
        return date(self.start_year, 4, 6)

    @property
    def end(self) -> date:
        return date(self.start_year + 1, 4, 5)

    @property
    def kept(self) -> int:
        return sum(self.buckets[k].net for k in ("regular", "cash", "adhoc"))

    @property
    def total_in(self) -> int:
        return sum(b.money_in for k, b in self.buckets.items() if k != "investment")

    @property
    def total_out(self) -> int:
        return sum(b.money_out for k, b in self.buckets.items() if k != "investment")

    @property
    def isa_left(self) -> int:
        return max(ISA_ALLOWANCE - self.isa_contributions, 0)

    def days_left(self, today: date | None = None) -> int | None:
        today = today or date.today()
        if not self.start <= today <= self.end:
            return None
        return (self.end - today).days

    @property
    def covers_whole_year(self) -> bool:
        return bool(
            self.first_date
            and self.last_date
            and (self.first_date - self.start).days <= 4
            and (self.end - self.last_date).days <= 4
        )


def tax_years(conn: sqlite3.Connection) -> list[int]:
    """Start years of every tax year with data, oldest first."""
    row = conn.execute("SELECT MIN(date), MAX(date) FROM transactions").fetchone()
    if not row[0]:
        return []
    first = tax_year_of(date.fromisoformat(row[0]))
    last = tax_year_of(date.fromisoformat(row[1]))
    return list(range(first, last + 1))


def tax_year_summary(conn: sqlite3.Connection, start_year: int) -> TaxYear:
    start, end = date(start_year, 4, 6), date(start_year + 1, 4, 5)
    spend = category_spend(conn, date_from=start, date_to=end)
    buckets = {key: BucketTotals(key, label) for key, label in BUCKETS.items()}
    targets = conn.execute(
        "SELECT name, is_isa FROM categories WHERE bucket = 'investment' "
        "ORDER BY COALESCE(invest_share, 0) DESC, name"
    ).fetchall()
    invested = {t["name"]: 0 for t in targets}
    withdrawn = dict.fromkeys(invested, 0)
    isa_names = [t["name"] for t in targets if t["is_isa"]]
    for item in spend:
        totals = buckets[item.bucket]
        totals.money_in += item.money_in
        totals.money_out += item.money_out
        if item.bucket == "investment":
            invested[item.name] = item.money_out
            withdrawn[item.name] = item.money_in
    bounds = conn.execute(
        "SELECT MIN(date), MAX(date) FROM transactions WHERE date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    return TaxYear(
        start_year=start_year,
        buckets=buckets,
        categories=spend,
        invested=invested,
        withdrawn=withdrawn,
        # Withdrawals don't give allowance back unless the ISA is flexible, so this
        # counts money paid in.
        isa_contributions=sum(invested[n] for n in isa_names),
        isa_names=isa_names,
        first_date=date.fromisoformat(bounds[0]) if bounds[0] else None,
        last_date=date.fromisoformat(bounds[1]) if bounds[1] else None,
    )


def investment_payments(
    conn: sqlite3.Connection,
    category_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[sqlite3.Row]:
    """Every payment to or from one investment, oldest first."""
    sql = "SELECT * FROM transactions WHERE category_id = ?"
    params: list = [category_id]
    if date_from:
        sql += " AND date >= ?"
        params.append(date_from.isoformat())
    if date_to:
        sql += " AND date <= ?"
        params.append(date_to.isoformat())
    return conn.execute(sql + " ORDER BY date, id", params).fetchall()


def average_spend(conn: sqlite3.Connection, month: str, lookback: int = 6) -> dict[int | None, int]:
    """Average monthly net spend per category over the months before ``month``."""
    previous = [m for m in months(conn) if m < month][-lookback:]
    if not previous:
        return {}
    totals: dict[int | None, int] = {}
    for m in previous:
        for item in category_spend(conn, m):
            totals[item.category_id] = totals.get(item.category_id, 0) + item.net_spend
    return {k: round(v / len(previous)) for k, v in totals.items()}
