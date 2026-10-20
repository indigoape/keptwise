import pytest

from keptwise.importer import import_path
from keptwise.investments import (
    Platform,
    SettingsProblem,
    list_platforms,
    parse_patterns,
    platforms_from_form,
    save_platforms,
)


def test_parse_patterns():
    assert parse_patterns("VANGUARD, vanguard,\nCoinbase uk ,") == ["VANGUARD", "Coinbase uk"]


def test_create_platforms_categorises_payments(bare_conn, may_csv):
    import_path(bare_conn, may_csv)
    with bare_conn:
        changed = save_platforms(
            bare_conn,
            [
                Platform(None, "World fund", 0.6, "hl.co.uk", True, ["VANGUARD"]),
                Platform(None, "Crypto", 0.4, None, False, ["COINBASE"]),
            ],
        )
    assert changed == 2
    saved = {p.name: p for p in list_platforms(bare_conn)}
    assert saved["World fund"].url == "https://hl.co.uk"
    assert saved["World fund"].is_isa
    assert saved["Crypto"].patterns == ["COINBASE"]


def test_update_keeps_your_own_rules(conn):
    index = next(p for p in list_platforms(conn) if p.name == "Fund")
    with conn:
        conn.execute(
            "INSERT INTO rules(pattern, match_type, direction, category_id) "
            "VALUES ('VANGUARD ASSET', 'contains', 'in', ?)",
            (index.id,),
        )
        others = [p for p in list_platforms(conn) if p.name != "Fund"]
        save_platforms(conn, [Platform(index.id, "Fund", 0.75, None, True, ["VG"]), *others])
    rules = [
        r[0] for r in conn.execute("SELECT pattern FROM rules WHERE category_id = ?", (index.id,))
    ]
    assert sorted(rules) == ["VANGUARD ASSET", "VG"]  # money-in rule kept, VANGUARD replaced


@pytest.mark.parametrize(
    ("platforms", "message"),
    [
        ([Platform(None, "A", 0.5), Platform(None, "B", 0.4)], "add up to 100%"),
        ([Platform(None, "A", 0.5), Platform(None, "a", 0.5)], "same name"),
        ([Platform(None, " ", 1.0)], "name"),
        ([Platform(None, "A", 1.2)], "between 0 and 100"),
    ],
)
def test_validation(bare_conn, platforms, message):
    with pytest.raises(SettingsProblem, match=message):
        save_platforms(bare_conn, platforms)


def test_name_clash_with_other_category(bare_conn):
    with pytest.raises(SettingsProblem, match="already a category called Groceries"):
        save_platforms(bare_conn, [Platform(None, "Groceries", 1.0)])


def test_no_investments_is_fine(conn):
    with conn:
        save_platforms(conn, [])
    assert list_platforms(conn) == []


def test_platforms_from_form():
    form = {
        "inv-0-id": "7",
        "inv-0-name": "Fund",
        "inv-0-share": "80%",
        "inv-0-isa": "1",
        "inv-0-patterns": "VANGUARD",
        "inv-1-name": "Old",
        "inv-1-share": "20",
        "inv-1-remove": "1",
        "inv-2-name": "",
    }
    [only] = platforms_from_form(form)
    assert (only.id, only.name, only.share, only.is_isa, only.patterns) == (
        7,
        "Fund",
        0.8,
        True,
        ["VANGUARD"],
    )
    with pytest.raises(SettingsProblem, match="isn't a percentage"):
        platforms_from_form({"inv-0-name": "X", "inv-0-share": "lots"})
    with pytest.raises(SettingsProblem, match="name"):
        platforms_from_form({"inv-0-name": "", "inv-0-patterns": "VANGUARD"})
