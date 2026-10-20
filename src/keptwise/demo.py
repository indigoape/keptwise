"""A made-up year of transactions, for trying the app and for screenshots.

Everything here is invented: the employer, landlord, amounts and dates. Only well-known
public company names are used, so the starter rules recognise them as they would on a
real statement.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta

from .db import set_setting
from .importer import ParsedTransaction, import_transactions
from .investments import Platform, save_platforms

PLATFORMS = [
    Platform(None, "Index fund", 0.8, "https://www.vanguardinvestor.co.uk/", True, ["VANGUARD"]),
    Platform(None, "Bitcoin", 0.2, "https://www.coinbase.com/", False, ["COINBASE"]),
]

GROCERIES = ["TESCO STORES 2231", "ALDI 114", "LIDL GB NEWTOWN", "SAINSBURYS S/MKT", "CO-OP GROUP"]
EATING = ["GREGGS PLC", "COSTA COFFEE", "NANDOS NEWTOWN", "THE OLD OAK PUB", "PRET A MANGER"]
SHOPPING = ["AMAZON.CO.UK*AB12CD", "ARGOS LTD", "BOOTS 0423", "PRIMARK NEWTOWN", "IKEA LTD"]
TRANSPORT = ["STAGECOACH BUS", "TRAINLINE.COM", "GWR TICKETS", "UBER *TRIP"]
OUTINGS = ["ODEON CINEMAS", "TICKETMASTER UK", "NATIONAL TRUST", "BOWLING WORLD"]


def months_back(today: date, count: int) -> list[date]:
    """The first day of each of the ``count`` whole months before ``today``'s month."""
    first = today.replace(day=1)
    result = []
    for _ in range(count):
        first = (first - timedelta(days=1)).replace(day=1)
        result.append(first)
    return list(reversed(result))


def _month_end(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _pence(rng: random.Random, low: float, high: float) -> int:
    return round(rng.uniform(low, high) * 100)


def month_transactions(
    first: date, rng: random.Random, index: int, last: bool
) -> list[tuple[date, str, str, int]]:
    """One month's made-up spending and income, without investments."""
    end = _month_end(first)
    days = end.day

    def on(day: int) -> date:
        return first.replace(day=min(day, days))

    tx: list[tuple[date, str, str, int]] = [
        (on(1), "FPO", "HOMES LETTINGS RENT", -72500),
        (on(2), "DD", "OCTOPUS ENERGY", -6400 if index < 7 else -7150),  # a price rise
        (on(4), "DD", "THAMES WATER", -3150),
        (on(8), "DD", "VIRGIN MEDIA", -3000),
        (on(9), "DD", "GIFFGAFF", -1000),
        (on(12), "DD", "PUREGYM LTD", -2499),
        (on(14), "DEB", "NETFLIX.COM", -1099),
        (on(18), "DEB", "SPOTIFY", -1199),
        (on(25), "FPI", "ACME LTD SALARY", 215000 + (12000 if index >= 9 else 0)),
    ]
    if first.month not in (2, 3):  # council tax is paid over ten months
        tx.append((on(1), "DD", "NEWTOWN CITY COUNCIL", -11800))
    for week in range(4):
        day = 2 + week * 7
        tx.append((on(day + rng.randint(0, 2)), "DEB", rng.choice(GROCERIES), -_pence(rng, 18, 55)))
        tx.append((on(day + rng.randint(0, 5)), "DEB", rng.choice(EATING), -_pence(rng, 3, 24)))
        tx.append((on(day + rng.randint(0, 5)), "DEB", rng.choice(TRANSPORT), -_pence(rng, 2, 9)))
    for _ in range(rng.randint(1, 3)):
        tx.append((on(rng.randint(1, days)), "DEB", rng.choice(SHOPPING), -_pence(rng, 6, 60)))
    if rng.random() < 0.6:
        tx.append((on(rng.randint(1, days)), "DEB", rng.choice(OUTINGS), -_pence(rng, 9, 40)))
    if rng.random() < 0.5:
        tx.append((on(rng.randint(1, days)), "CPT", "NOTEMACHINE NEWTOWN", -2000))
    if rng.random() < 0.4:
        tx.append((on(rng.randint(1, days)), "FPI", "VINTED", _pence(rng, 8, 30)))
    if rng.random() < 0.4:
        tx.append((on(rng.randint(1, days)), "FPO", "A FRIEND", -2000))
    if first.month == 8:  # a summer trip
        tx.append((on(3), "DEB", "EASYJET", -18900))
        tx.append((on(3), "DEB", "AIRBNB * HMXYZ", -32400))
    if last:  # something for the "needs a category" view
        tx.append((on(20), "DEB", "SUMUP *MARKET STALL", -1250))
    return tx


def build(conn: sqlite3.Connection, *, months: int = 12, today: date | None = None) -> int:
    """Fill an empty, seeded database with ``months`` months of made-up data.

    Returns the number of transactions added.
    """
    if conn.execute("SELECT 1 FROM transactions LIMIT 1").fetchone():
        raise ValueError("The demo needs an empty database.")
    rng = random.Random(2026)  # the same demo every time
    with conn:
        save_platforms(conn, [Platform(**vars(p)) for p in PLATFORMS])
        set_setting(conn, "bank_name", "your bank")
        set_setting(conn, "setup_done", "1")

    balance = 120000
    kept_last_month: int | None = None
    added = 0
    starts = months_back(today or date.today(), months)
    for index, first in enumerate(starts):
        rows = month_transactions(first, rng, index, last=index == len(starts) - 1)
        kept = sum(amount for _, _, _, amount in rows)
        invest = max(kept_last_month if kept_last_month is not None else 30000, 0)
        if invest:
            day = first.replace(day=3)
            for platform, text in ((PLATFORMS[0], "VANGUARD"), (PLATFORMS[1], "COINBASE")):
                rows.append((day, "DEB", text, -round(invest * platform.share)))
        kept_last_month = kept

        parsed = []
        for when, tx_type, description, amount in sorted(rows, key=lambda r: r[0]):
            balance += amount
            parsed.append(ParsedTransaction(when, tx_type, description, amount, balance))
        name = f"demo-{first:%Y-%m}.csv"
        result = import_transactions(conn, parsed, filename=name, source="demo", sha256=name)
        added += result.rows_added
    return added
