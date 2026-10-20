from datetime import date
from pathlib import Path

import pytest

from keptwise.importer import ImportProblem, import_path, parse_csv

DATA = Path(__file__).parent / "data"


def test_parse_lloyds_csv(may_csv):
    rows = parse_csv(may_csv.read_bytes())
    assert len(rows) == 12
    first = rows[0]
    assert first.date == date(2026, 5, 31)
    assert first.description == "TESCO STORES 1234"
    assert first.amount == -2550
    assert first.balance == 163200
    wages = next(r for r in rows if r.tx_type == "FPI")
    assert wages.amount == 200000


def test_parse_collapses_whitespace(may_csv):
    rows = parse_csv(may_csv.read_bytes())
    assert any(r.description == "SUMUP *COFFEE PLACE" for r in rows)


def test_two_digit_years_and_bare_amounts(june_overlap_csv):
    rows = parse_csv(june_overlap_csv.read_bytes())
    assert rows[0].date == date(2026, 6, 2)
    assert rows[0].amount == -1000


def test_rejects_wrong_file():
    with pytest.raises(ImportProblem, match="Couldn't find the transaction columns"):
        parse_csv(b"Date,Amount\n01/01/2026,5\n")


def test_rejects_empty_file():
    with pytest.raises(ImportProblem, match="empty"):
        parse_csv(b"\n\n")


def test_bad_date_reports_line():
    data = (
        "Transaction Date,Transaction Type,Transaction Description,Debit Amount,Credit Amount\n"
        "31/02/2026x,DEB,THING,1,\n"
    )
    with pytest.raises(ImportProblem, match="Line 2"):
        parse_csv(data)


def test_import_is_idempotent(conn, may_csv):
    first = import_path(conn, may_csv)
    assert first.rows_added == 12
    again = import_path(conn, may_csv)
    assert again.rows_added == 0
    assert again.rows_duplicate == 12
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 12


def test_identical_bus_fares_are_both_kept(conn, may_csv):
    import_path(conn, may_csv)
    fares = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE description = 'ARRIVA NEWTOWN'"
    ).fetchone()[0]
    assert fares == 2


def test_overlapping_statements(conn, may_csv, june_overlap_csv):
    import_path(conn, may_csv)
    result = import_path(conn, june_overlap_csv)
    assert result.rows_added == 1
    assert result.rows_duplicate == 1


def test_import_categorises_new_rows(conn, may_csv):
    import_path(conn, may_csv)
    rows = dict(
        conn.execute(
            "SELECT t.description, c.name FROM transactions t "
            "LEFT JOIN categories c ON c.id = t.category_id"
        ).fetchall()
    )
    assert rows["TESCO STORES 1234"] == "Groceries"
    assert rows["ACME LTD SALARY"] == "Wages"
    assert rows["WH Smith Station"] == "Eating & drinking"
    assert rows["SUMUP *COFFEE PLACE"] == "Eating & drinking"
    assert rows["VANGUARD"] == "Fund"
    assert rows["COINBASE UK"] == "Bitcoin"
    assert rows["A N OTHER SAVINGS"] == "Transfers between my accounts"
    assert rows["NEWTOWN CITY COUNCIL"] == "Council tax"
    assert rows["HOMES LETTINGS RENT"] == "Rent or mortgage"
    assert rows["MYSTERY SHOP"] is None


def test_monzo_style_signed_amounts():
    rows = parse_csv((DATA / "monzo_style.csv").read_bytes())
    assert [(r.description, r.amount) for r in rows] == [
        ("TESCO STORES 3000 LONDON GBR", -1240),
        ("ACME LTD SALARY", 150000),
    ]
    assert rows[0].tx_type == "CARD PAYMENT"


def test_starling_style_amount_and_balance_with_currency():
    rows = parse_csv((DATA / "starling_style.csv").read_bytes())
    assert rows[0].description == "Pret A Manger"
    assert rows[0].amount == -425
    assert rows[1].balance == 93575


def test_nationwide_style_preamble_pound_signs_and_cp1252():
    rows = parse_csv((DATA / "nationwide_style.csv").read_bytes())
    assert [(r.date, r.description, r.amount, r.balance) for r in rows] == [
        (date(2026, 6, 5), "GREGGS", -210, 99790),
        (date(2026, 6, 6), "ACME LTD SALARY", 150000, 249790),
    ]


def test_semicolons_brackets_and_cr_dr():
    data = (
        "Date;Details;Amount;Balance\n"
        "2026-06-01;REFUND;12.00 CR;100.00\n"
        "2026-06-02;SHOP;(5.50);94.50\n"
        "2026-06-03;OTHER SHOP;3.00 DR;91.50\n"
    )
    rows = parse_csv(data)
    assert [r.amount for r in rows] == [1200, -550, -300]


def test_only_csv_files(conn):
    from keptwise.importer import import_file

    with pytest.raises(ImportProblem, match="Only CSV"):
        import_file(conn, b"PK...", "statement.xlsx")
