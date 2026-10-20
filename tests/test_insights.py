from datetime import date

from keptwise import insights
from keptwise.importer import ParsedTransaction, import_transactions


def add(conn, rows):
    """rows: (date, description, amount_pence, tx_type)"""
    txs = [ParsedTransaction(d, t, desc, amt, None) for d, desc, amt, t in rows]
    import_transactions(conn, txs, filename="test.csv", source="csv", sha256="x")


def monthly(conn, months, make):
    rows = []
    for year, month in months:
        rows += make(year, month)
    add(conn, rows)


MONTHS = [(2026, m) for m in range(1, 8)]  # Jan..Jul


def full_month(year, month, extra=()):
    return [
        (date(year, month, 1), "ARRIVA NEWTOWN", -200, "DEB"),
        (date(year, month, 10), "HOMES LETTINGS RENT", -79000, "FPO"),
        (date(year, month, 20), "VIRGIN MEDIA PYMTS", -2700, "DD"),
        (date(year, month, 26), "ACME LTD SALARY", 200000, "FPI"),
        (date(year, month, 28), "ARRIVA NEWTOWN", -200, "DEB"),
        *extra,
    ]


def test_normal_range_ignores_one_offs():
    normal = insights.normal_range([100_00, 110_00, 90_00, 105_00, 95_00, 900_00])
    assert normal.median == 102_50
    assert normal.high < 300_00  # the £900 month doesn't drag "normal" up
    assert not normal.income
    assert not normal.occasional


def test_income_ranges_are_positive():
    normal = insights.normal_range([-2000_00, -2000_00, -2100_00, -1900_00])
    assert normal.income
    assert normal.median == 2000_00


def test_occasional_categories_need_a_record_month():
    normal = insights.normal_range([0, 0, 0, 60_00, 0, 0])
    assert normal.occasional
    assert insights.compare(80_00, normal) == "normal"  # small: not worth a flag
    assert insights.compare(500_00, normal) == "above"


def test_flags_unusually_high_spending(conn):
    def make(y, m):
        tesco = 6000 if (y, m) != (2026, 7) else 30000
        return full_month(y, m, [(date(y, m, 15), "TESCO STORES 1", -tesco, "DEB")])

    monthly(conn, MONTHS, make)
    flags = insights.month_flags(conn, "2026-07")
    groceries = [f for f in flags if f.name == "Groceries"]
    assert groceries and groceries[0].kind == "above"
    assert "£300.00" in groceries[0].message


def test_missing_bill(conn):
    def make(y, m):
        rows = full_month(y, m)
        if (y, m) == (2026, 7):
            rows = [r for r in rows if r[1] != "HOMES LETTINGS RENT"]
        return rows

    monthly(conn, MONTHS, make)
    flags = {f.name: f for f in insights.month_flags(conn, "2026-07")}
    assert flags["Rent or mortgage"].kind == "missing"
    assert "£790.00 around the 10th" in flags["Rent or mortgage"].message


def test_changed_bill(conn):
    def make(y, m):
        rows = full_month(y, m)
        if (y, m) == (2026, 7):
            rows = [(d, desc, -3150 if "VIRGIN" in desc else a, t) for d, desc, a, t in rows]
        return rows

    monthly(conn, MONTHS, make)
    flags = {f.name: f for f in insights.month_flags(conn, "2026-07")}
    assert flags["Internet"].kind == "changed"
    assert flags["Internet"].message == "Internet went up from £27.00 to £31.50."


def test_quiet_month_has_no_flags(conn):
    monthly(conn, MONTHS, full_month)
    assert insights.month_flags(conn, "2026-07") == []


def test_part_month_is_not_called_low(conn):
    def make(y, m):
        rows = full_month(y, m, [(date(y, m, 15), "TESCO STORES 1", -6000, "DEB")])
        if (y, m) == (2026, 7):
            rows = [r for r in rows if r[0].day <= 5]  # statement only up to the 5th
        return rows

    monthly(conn, MONTHS, make)
    kinds = {f.kind for f in insights.month_flags(conn, "2026-07")}
    assert "below" not in kinds
    assert "missing" not in kinds  # rent is due on the 10th, not yet
