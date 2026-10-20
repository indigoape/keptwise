import pytest

from keptwise.importer import import_path
from keptwise.rules import Rule, categorise, suggest_pattern, validate_pattern


def rule(pattern, match_type="contains", direction="any", tx_type=None, priority=0):
    return Rule(1, pattern, match_type, direction, tx_type, 1, priority)


def test_contains_is_case_insensitive():
    assert rule("tesco").matches("TESCO STORES 2231", -100)


def test_direction():
    wages = rule("ACME LTD", direction="in")
    assert wages.matches("ACME LTD SALARY", 200000)
    assert not wages.matches("ACME LTD SALARY", -300)


def test_tx_type_and_empty_pattern():
    every_atm = rule("", tx_type="CPT", direction="out")
    assert every_atm.matches("LNK NOTEMACHINE", -2000, "CPT")
    assert not every_atm.matches("LNK NOTEMACHINE", -2000, "DEB")


def test_starts_with_exact_and_regex():
    assert rule("ARRIVA", "starts_with").matches("ARRIVA NEWTOWN", -200)
    assert not rule("NEWTOWN", "starts_with").matches("ARRIVA NEWTOWN", -200)
    assert rule("o2", "exact").matches("O2", -1300)
    assert not rule("o2", "exact").matches("MY O2 BILL", -1300)
    assert rule(r"\bO2\b", "regex").matches("MY O2 BILL PAYMENT", -1300)
    assert not rule(r"\bO2\b", "regex").matches("PO2X", -1300)


def test_validate_pattern():
    assert validate_pattern("TESCO", "contains") is None
    assert validate_pattern("  ", "contains")
    assert "isn't valid" in validate_pattern("(unclosed", "regex")


def test_manual_choices_survive_recategorising(conn, may_csv):
    import_path(conn, may_csv)
    snacks = conn.execute("SELECT id FROM categories WHERE name = 'Shopping'").fetchone()[0]
    with conn:
        conn.execute(
            "UPDATE transactions SET category_id = ?, manual = 1 "
            "WHERE description = 'TESCO STORES 1234'",
            (snacks,),
        )
        categorise(conn)
    name = conn.execute(
        "SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id "
        "WHERE t.description = 'TESCO STORES 1234'"
    ).fetchone()[0]
    assert name == "Shopping"


def test_higher_priority_wins(conn, may_csv):
    import_path(conn, may_csv)
    shopping = conn.execute("SELECT id FROM categories WHERE name = 'Shopping'").fetchone()[0]
    with conn:
        conn.execute(
            "INSERT INTO rules(pattern, category_id, priority) VALUES ('TESCO', ?, 50)",
            (shopping,),
        )
        changed = categorise(conn)
    assert changed == 1
    category = conn.execute(
        "SELECT category_id FROM transactions WHERE description = 'TESCO STORES 1234'"
    ).fetchone()[0]
    assert category == shopping


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("TESCO STORES 2231", "TESCO STORES"),
        ("SUMUP *CORNER CAFE", "SUMUP *CORNER CAFE"),
        ("ZETTLE_*MARKET STALL", "ZETTLE_*MARKET STALL"),
        ("eBay O*12-34567-89", "eBay O"),
        ("AMAZON* AB1CD2EF3", "AMAZON"),
        ("YES", "YES"),
    ],
)
def test_suggest_pattern(description, expected):
    assert suggest_pattern(description) == expected
