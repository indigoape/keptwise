"""Reading bank CSV exports and loading them into the database.

The importer recognises the column names UK banks commonly use, so most exports work
as they are:

* Lloyds, Halifax, Bank of Scotland: Transaction Date, Transaction Type, Transaction
  Description, Debit Amount, Credit Amount, Balance
* Nationwide-style: Date, Description, Paid out, Paid in, Balance (with a few lines of
  account details above the header)
* Monzo / Starling-style: Date, Name or Counter Party, Amount (signed), Balance

It needs a date, a description and either a signed amount or money in/out columns.
Dates may be dd/mm/yyyy, dd/mm/yy, yyyy-mm-dd or "01 Oct 2026".

Re-importing is always safe: every transaction gets a fingerprint and duplicates are
skipped, so overlapping exports can be uploaded without double counting.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .dates import strftime
from .money import to_pence
from .rules import categorise

# Column names banks use, most specific first. Names are compared after lower-casing
# and turning punctuation into spaces, so "Amount (GBP)" matches "amount gbp".
COLUMN_ALIASES: dict[str, list[str]] = {
    "date": [
        "transaction date",
        "date",
        "posting date",
        "booking date",
        "completed date",
        "date of transaction",
        "value date",
        "started date",
    ],
    "description": [
        "transaction description",
        "description",
        "transaction details",
        "details",
        "narrative",
        "counter party",
        "counterparty",
        "payee",
        "name",
        "merchant",
        "memo",
        "reference",
    ],
    "tx_type": ["transaction type", "type"],
    "amount": ["amount", "amount gbp", "transaction amount", "value", "amount local currency"],
    "debit": ["debit amount", "debit", "paid out", "money out", "withdrawals", "amount out", "out"],
    "credit": ["credit amount", "credit", "paid in", "money in", "deposits", "amount in", "in"],
    "balance": ["balance", "running balance", "balance gbp", "available balance"],
}

DATE_FORMATS = (
    "%d/%m/%Y",
    "%d/%m/%y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d %b %y",
    "%d-%b-%y",
)


class ImportProblem(ValueError):
    """Raised when a file can't be read. The message is shown to the user."""


@dataclass(frozen=True)
class ParsedTransaction:
    date: date
    tx_type: str
    description: str
    amount: int  # pence; credits positive, debits negative
    balance: int | None


@dataclass
class ImportResult:
    filename: str
    rows_total: int = 0
    rows_added: int = 0
    rows_duplicate: int = 0
    first_date: date | None = None
    last_date: date | None = None
    import_id: int | None = None
    added_ids: list[int] = field(default_factory=list)
    already_imported: bool = False

    @property
    def summary(self) -> str:
        if self.rows_total == 0:
            return f"{self.filename}: no transactions found."
        span = ""
        if self.first_date and self.last_date:
            fmt = "%-d %b %Y"
            span = f" ({strftime(self.first_date, fmt)} – {strftime(self.last_date, fmt)})"
        dupes = f", {self.rows_duplicate} already imported" if self.rows_duplicate else ""
        return f"{self.filename}: {self.rows_added} new transactions{dupes}{span}."


def parse_date(text: str) -> date:
    text = text.strip()
    iso = re.match(r"^(\d{4}-\d{2}-\d{2})[T ]", text)  # 2026-10-01T09:30:00Z
    if iso:
        text = iso.group(1)
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ImportProblem(f"Couldn't read the date {text!r}. Expected something like 31/12/2026.")


def parse_amount(text: str) -> int | None:
    """Like to_pence, but also understands (12.34), 12.34 DR/CR and a unicode minus."""
    text = (text or "").strip().replace("\u2212", "-")
    if not text:
        return None
    sign = 1
    if text.startswith("(") and text.endswith(")"):
        text, sign = text[1:-1], -1
    suffix = re.search(r"\s*(CR|DR)$", text, re.IGNORECASE)
    if suffix:
        text = text[: suffix.start()]
        sign = -1 if suffix.group(1).upper() == "DR" else 1
    value = to_pence(text)
    return None if value is None else sign * value


def _clean_description(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def find_columns(header: list[str]) -> dict[str, int] | None:
    """Map our fields to column positions, or None if this isn't a header row."""
    names = [_normalise(h) for h in header]
    found: dict[str, int] = {}
    for key, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in names and names.index(alias) not in found.values():
                found[key] = names.index(alias)
                break
    has_money = "amount" in found or "debit" in found or "credit" in found
    if "date" in found and "description" in found and has_money:
        return found
    return None


def _decode(data: bytes | str) -> str:
    if isinstance(data, str):
        return data.lstrip("\ufeff")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def parse_csv(data: bytes | str) -> list[ParsedTransaction]:
    """Parse a bank CSV export into transactions (the file's order is preserved)."""
    text = _decode(data)
    if not text.strip():
        raise ImportProblem("The file is empty.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))

    columns, header_at = None, 0
    for i, row in enumerate(rows[:30]):  # some banks put account details above the header
        columns = find_columns(row)
        if columns:
            header_at = i
            break
    if columns is None:
        raise ImportProblem(
            "Couldn't find the transaction columns. The file needs a date, a description "
            "and an amount (or money in and money out) column."
        )

    def cell(row: list[str], key: str) -> str:
        i = columns.get(key)
        return row[i] if i is not None and i < len(row) else ""

    signed = "amount" in columns and not ("debit" in columns and "credit" in columns)
    transactions = []
    for line_no, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
        if not any(c.strip() for c in row) or not cell(row, "date").strip():
            continue
        try:
            if signed:
                amount = parse_amount(cell(row, "amount")) or 0
            else:
                debit = abs(parse_amount(cell(row, "debit")) or 0)
                credit = abs(parse_amount(cell(row, "credit")) or 0)
                amount = credit - debit
            transactions.append(
                ParsedTransaction(
                    date=parse_date(cell(row, "date")),
                    tx_type=cell(row, "tx_type").strip().upper(),
                    description=_clean_description(cell(row, "description")),
                    amount=amount,
                    balance=parse_amount(cell(row, "balance")),
                )
            )
        except (ValueError, ImportProblem) as exc:
            raise ImportProblem(f"Line {line_no}: {exc}") from exc
    return transactions


def fingerprint(tx: ParsedTransaction, occurrence: int) -> str:
    """Stable identity for a transaction across overlapping exports.

    The running balance makes otherwise identical rows (two £2 bus fares on the same
    day) distinct; ``occurrence`` covers the rare case where even that repeats.
    """
    key = "|".join(
        [
            tx.date.isoformat(),
            str(tx.amount),
            "" if tx.balance is None else str(tx.balance),
            tx.description.upper(),
            str(occurrence),
        ]
    )
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def import_transactions(
    conn: sqlite3.Connection,
    transactions: list[ParsedTransaction],
    *,
    filename: str,
    source: str,
    sha256: str,
) -> ImportResult:
    """Insert new transactions, skip duplicates, and categorise what was added."""
    result = ImportResult(filename=filename, rows_total=len(transactions))
    if transactions:
        result.first_date = min(t.date for t in transactions)
        result.last_date = max(t.date for t in transactions)

    seen: Counter[tuple] = Counter()
    with conn:
        cur = conn.execute(
            "INSERT INTO imports(filename, sha256, source, rows_total, rows_added, "
            "rows_duplicate, first_date, last_date) VALUES (?,?,?,?,0,0,?,?)",
            (
                filename,
                sha256,
                source,
                len(transactions),
                result.first_date and result.first_date.isoformat(),
                result.last_date and result.last_date.isoformat(),
            ),
        )
        result.import_id = cur.lastrowid
        # Insert oldest first so ids follow time order. Bank exports list newest first,
        # so flip those before the (stable) sort to keep same-day rows in order too.
        ordered = list(transactions)
        if len(ordered) > 1 and ordered[0].date > ordered[-1].date:
            ordered.reverse()
        for tx in sorted(ordered, key=lambda t: t.date):
            ident = (tx.date, tx.amount, tx.balance, tx.description.upper())
            seen[ident] += 1
            fp = fingerprint(tx, seen[ident])
            cur = conn.execute(
                "INSERT OR IGNORE INTO transactions"
                "(fingerprint, date, tx_type, description, amount, balance, import_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    fp,
                    tx.date.isoformat(),
                    tx.tx_type,
                    tx.description,
                    tx.amount,
                    tx.balance,
                    result.import_id,
                ),
            )
            if cur.rowcount:
                result.added_ids.append(cur.lastrowid)
        result.rows_added = len(result.added_ids)
        result.rows_duplicate = result.rows_total - result.rows_added
        conn.execute(
            "UPDATE imports SET rows_added = ?, rows_duplicate = ? WHERE id = ?",
            (result.rows_added, result.rows_duplicate, result.import_id),
        )
        if result.added_ids:
            categorise(conn, ids=result.added_ids)
    return result


def import_file(conn: sqlite3.Connection, data: bytes, filename: str) -> ImportResult:
    """Import an uploaded bank CSV."""
    if not filename.lower().endswith((".csv", ".txt")):
        raise ImportProblem("Only CSV files can be imported. Download your statement as CSV.")
    sha = hashlib.sha256(data).hexdigest()
    transactions = parse_csv(data)
    return import_transactions(conn, transactions, filename=filename, source="csv", sha256=sha)


def import_path(conn: sqlite3.Connection, path: Path | str) -> ImportResult:
    path = Path(path)
    return import_file(conn, path.read_bytes(), path.name)
