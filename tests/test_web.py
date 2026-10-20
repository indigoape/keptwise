import io

import pytest

from keptwise.web import create_app

SETUP_FORM = {
    "bank_name": "Testbank",
    "inv-0-name": "Fund",
    "inv-0-share": "75",
    "inv-0-patterns": "VANGUARD",
    "inv-0-url": "vanguardinvestor.co.uk",
    "inv-0-isa": "1",
    "inv-1-name": "Bitcoin",
    "inv-1-share": "25",
    "inv-1-patterns": "COINBASE",
    "inv-1-url": "coinbase.com",
    "inv-2-name": "",  # the blank row the form always shows
}


@pytest.fixture
def fresh(tmp_path):
    """A brand-new install, before setup."""
    return create_app(tmp_path / "test.db", testing=True).test_client()


@pytest.fixture
def client(fresh):
    """Set up like a typical user: two investments, 80/20."""
    res = fresh.post("/setup", data=SETUP_FORM)
    assert res.status_code == 302, res.get_data(as_text=True)
    return fresh


def upload(client, path):
    return client.post(
        "/import",
        data={"files": (io.BytesIO(path.read_bytes()), path.name)},
        content_type="multipart/form-data",
    )


def test_first_run_goes_to_setup(fresh):
    res = fresh.get("/")
    assert res.status_code == 302 and res.headers["Location"].endswith("/setup")
    page = fresh.get("/setup").get_data(as_text=True)
    assert "Which bank" in page and "What do you invest in?" in page


def test_setup_then_welcome(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"Start with last month" in res.data
    assert b"Testbank" in res.data


def test_setup_without_investments(fresh, may_csv):
    res = fresh.post("/setup", data={"bank_name": "", "inv-0-name": ""})
    assert res.status_code == 302
    upload(fresh, may_csv)
    page = fresh.get("/months/2026-05").get_data(as_text=True)
    assert "You kept" in page and "put" not in page.split("You kept")[1][:200]
    assert b"Add investments in Settings" in fresh.get("/investments").data


def test_setup_rejects_bad_split(fresh):
    form = dict(SETUP_FORM, **{"inv-1-share": "10"})
    res = fresh.post("/setup", data=form)
    assert res.status_code == 200
    page = res.get_data(as_text=True)
    assert "has to add up to 100%" in page
    assert 'value="VANGUARD"' in page  # what was typed is kept


def test_import_then_overview(client, may_csv):
    res = upload(client, may_csv)
    assert res.status_code == 200
    assert b"Imported 12 new transactions" in res.data
    res = client.get("/")
    assert res.status_code == 302
    page = client.get(res.headers["Location"]).get_data(as_text=True)
    assert "May 2026" in page
    assert "£916.00" in page  # kept
    assert "£687.00" in page and "£229.00" in page  # 75/25 split


def test_reimport_reports_nothing_new(client, may_csv):
    upload(client, may_csv)
    res = upload(client, may_csv)
    assert b"Nothing new to import" in res.data


def test_bad_upload_shows_reason(client):
    res = client.post(
        "/import",
        data={"files": (io.BytesIO(b"a,b\n1,2\n"), "nope.csv")},
        content_type="multipart/form-data",
    )
    assert b"find the transaction columns" in res.data


@pytest.mark.parametrize(
    "url",
    [
        "/transactions?month=2026-05",
        "/transactions?month=all&category=none",
        "/transactions.csv?month=2026-05",
        "/rules",
        "/investments",
        "/import",
    ],
)
def test_pages_render(client, may_csv, url):
    upload(client, may_csv)
    assert client.get(url).status_code == 200


def test_transactions_defaults_to_latest_month(client, may_csv):
    upload(client, may_csv)
    res = client.get("/transactions")
    assert res.status_code == 302
    assert "month=2026-05" in res.headers["Location"]


def test_unknown_month_404(client, may_csv):
    upload(client, may_csv)
    assert client.get("/months/2030-01").status_code == 404
    assert client.get("/months/nonsense").status_code == 404


def _tx_id(client, description):
    from keptwise.web import get_db

    with client.application.app_context():
        return (
            get_db()
            .execute("SELECT id FROM transactions WHERE description = ?", (description,))
            .fetchone()[0]
        )


def _category_id(client, name):
    from keptwise.web import get_db

    with client.application.app_context():
        row = get_db().execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None


def test_set_category_offers_rule(client, may_csv):
    upload(client, may_csv)
    tx = _tx_id(client, "MYSTERY SHOP")
    shopping = _category_id(client, "Shopping")
    res = client.post(f"/transactions/{tx}", json={"category": str(shopping)})
    data = res.get_json()
    assert data["category"] == "Shopping"
    assert data["manual"] is True
    assert data["uncategorised"] == 1  # the transfer to savings, which has no rule here
    assert data["suggestion"]["pattern"] == "MYSTERY SHOP"

    # hand it back to the rules: no rule matches, so it's uncategorised again
    data = client.post(f"/transactions/{tx}", json={"category": "auto"}).get_json()
    assert data["manual"] is False
    assert data["category_id"] is None


def test_create_rule_recategorises(client, may_csv):
    upload(client, may_csv)
    shopping = _category_id(client, "Shopping")
    res = client.post(
        "/rules",
        data={
            "pattern": "MYSTERY",
            "match_type": "contains",
            "direction": "out",
            "category_id": shopping,
            "priority": "20",
        },
        follow_redirects=True,
    )
    assert b"1 transaction changed category" in res.data


def test_invalid_regex_is_rejected(client, may_csv):
    upload(client, may_csv)
    shopping = _category_id(client, "Shopping")
    res = client.post(
        "/rules",
        data={"pattern": "(oops", "match_type": "regex", "category_id": shopping},
        follow_redirects=True,
    )
    assert b"isn&#39;t valid" in res.data


def test_rule_preview(client, may_csv):
    upload(client, may_csv)
    data = client.get("/rules/preview?pattern=arriva&match_type=contains&direction=any").get_json()
    assert data["count"] == 2


def test_settings_change_split_and_platforms(client, may_csv):
    upload(client, may_csv)
    page = client.get("/settings").get_data(as_text=True)
    assert 'value="Testbank"' in page and 'value="VANGUARD"' in page
    index, btc = _category_id(client, "Fund"), _category_id(client, "Bitcoin")
    form = dict(
        SETUP_FORM,
        **{"inv-0-id": str(index), "inv-1-id": str(btc), "inv-0-share": "70", "inv-1-share": "30"},
    )
    res = client.post("/settings", data=form, follow_redirects=True)
    assert b"Settings saved" in res.data
    page = client.get("/months/2026-05").get_data(as_text=True)
    assert "£641.20" in page  # 70% of £916.00

    # Remove Bitcoin: its payment is no longer counted as an investment
    form = dict(form, **{"inv-0-share": "100", "inv-1-remove": "1"})
    client.post("/settings", data=form)
    assert _category_id(client, "Bitcoin") is None
    tx = client.get("/transactions?month=2026-05&bucket=investment").get_data(as_text=True)
    assert "COINBASE UK" not in tx and "VANGUARD" in tx


def test_delete_category_returns_transactions_to_rules(client, may_csv):
    upload(client, may_csv)
    groceries = _category_id(client, "Groceries")
    res = client.post(f"/categories/{groceries}/delete", follow_redirects=True)
    assert b"Deleted Groceries" in res.data


def test_statement_due():
    from datetime import date

    from keptwise import db as storage
    from keptwise.importer import ParsedTransaction, import_transactions
    from keptwise.web.views import statement_due

    conn = storage.connect(":memory:")
    assert statement_due(conn, date(2026, 11, 2)) is None  # nothing imported
    tx = ParsedTransaction(date(2026, 9, 29), "DEB", "TESCO", -100, None)
    import_transactions(conn, [tx], filename="a", source="csv", sha256="x")
    assert statement_due(conn, date(2026, 10, 19)) is None  # September is in
    assert statement_due(conn, date(2026, 11, 1)) == (date(2026, 9, 29), date(2026, 10, 31))


def test_reminder_calendar_file(client):
    res = client.get("/reminder.ics")
    assert res.mimetype == "text/calendar"
    body = res.get_data(as_text=True)
    assert "RRULE:FREQ=MONTHLY;BYMONTHDAY=1" in body
    assert "from Testbank as a CSV" in body
    assert "\r\n" in body and "BEGIN:VALARM" in body


def test_tax_year_pages(client, may_csv):
    upload(client, may_csv)
    res = client.get("/tax-years")
    assert res.status_code == 302 and "/tax-years/2026" in res.headers["Location"]
    page = client.get("/tax-years/2026").get_data(as_text=True)
    assert "ISA allowance" in page
    assert "£200.00" in page  # Vanguard payment in May
    assert client.get("/tax-years/1999").status_code == 404


def test_investment_csv(client, may_csv):
    upload(client, may_csv)
    index = _category_id(client, "Fund")
    body = client.get(f"/investments/{index}.csv").get_data(as_text=True)
    assert "2026-05-05,2026/27,VANGUARD,200.00" in body


def test_investment_links_and_isa_editable(client, may_csv):
    upload(client, may_csv)
    btc = _category_id(client, "Bitcoin")
    client.post(
        f"/categories/{btc}",
        data={"name": "Bitcoin", "bucket": "investment", "url": "coinbase.com", "is_isa": "1"},
    )
    page = client.get("/investments").get_data(as_text=True)
    assert 'href="https://coinbase.com"' in page
    assert "Open Coinbase" in page


def test_health_and_shutdown_guard(client):
    assert client.get("/healthz").data == b"ok"
    assert client.post("/shutdown").status_code == 404  # not started by `serve`


def test_merge_category(client, may_csv):
    upload(client, may_csv)
    groceries = _category_id(client, "Groceries")
    shopping = _category_id(client, "Shopping")
    res = client.post(
        f"/categories/{groceries}/merge", data={"target_id": shopping}, follow_redirects=True
    )
    assert b"Merged Groceries into Shopping. 1 transaction moved." in res.data
    res = client.post(
        f"/categories/{shopping}/merge", data={"target_id": shopping}, follow_redirects=True
    )
    assert b"Choose a different category" in res.data


def test_refuses_other_hosts(fresh):
    # DNS rebinding: a page on another domain that resolves to 127.0.0.1
    assert fresh.get("/", headers={"Host": "evil.example"}).status_code == 403
    assert fresh.get("/", headers={"Host": "127.0.0.1:8765"}).status_code != 403


def test_refuses_changes_from_other_websites(fresh):
    res = fresh.post("/setup", data=SETUP_FORM, headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
    res = fresh.post("/setup", data=SETUP_FORM, headers={"Origin": "http://localhost"})
    assert res.status_code == 302
