from datetime import date

from keptwise import reports
from keptwise.importer import ParsedTransaction, import_transactions


def add(conn, rows):
    txs = [ParsedTransaction(d, t, desc, amt, None) for d, desc, amt, t in rows]
    import_transactions(conn, txs, filename="t.csv", source="csv", sha256="x")


def test_tax_year_boundaries():
    assert reports.tax_year_of(date(2026, 4, 5)) == 2025
    assert reports.tax_year_of(date(2026, 4, 6)) == 2026
    assert reports.tax_year_label(2026) == "2026/27"
    assert reports.tax_year_label(2099) == "2099/00"


def test_isa_allowance_counts_payments_in(conn):
    add(
        conn,
        [
            (date(2026, 4, 5), "VANGUARD", -1000_00, "DEB"),  # previous tax year
            (date(2026, 4, 7), "VANGUARD", -500_00, "DEB"),
            (date(2026, 5, 7), "VANGUARD", -300_00, "DEB"),
            (date(2026, 6, 1), "VANGUARD ASSET MAN", 200_00, "FPI"),  # withdrawal
            (date(2026, 5, 7), "COINBASE UK", -50_00, "FPO"),  # Bitcoin: not an ISA
        ],
    )
    ty = reports.tax_year_summary(conn, 2026)
    assert ty.isa_names == ["Fund"]
    assert ty.isa_contributions == 800_00
    assert ty.isa_left == reports.ISA_ALLOWANCE - 800_00
    assert ty.invested == {"Fund": 800_00, "Bitcoin": 50_00}
    assert ty.withdrawn["Fund"] == 200_00
    assert reports.tax_years(conn) == [2025, 2026]


def test_days_left():
    ty = reports.TaxYear(2026, {}, [], {}, {}, 0, [], None, None)
    assert ty.days_left(date(2027, 4, 4)) == 1
    assert ty.days_left(date(2027, 4, 6)) is None
