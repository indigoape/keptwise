from keptwise import reports
from keptwise.importer import import_path


def test_month_summary(conn, may_csv):
    import_path(conn, may_csv)
    s = reports.summarise(conn, "2026-05")
    assert s.buckets["regular"].money_in == 200000
    assert s.buckets["regular"].money_out == 91000
    assert s.buckets["cash"].net == -10000
    assert s.buckets["adhoc"].net == -7400
    assert s.buckets["investment"].money_out == 25000
    assert s.surplus == 91600
    assert s.bank_net == 91600 - 25000
    assert s.opening_balance == 96600
    assert s.closing_balance == 163200
    assert s.closing_balance - s.opening_balance == s.bank_net
    assert s.uncategorised_count == 1
    assert s.invested == {"Fund": 20000, "Bitcoin": 5000}


def test_allocation_follows_split(conn, may_csv):
    import_path(conn, may_csv)
    s = reports.summarise(conn, "2026-05")
    plan = {a.name: a.amount for a in s.allocations}
    assert plan == {"Fund": 68700, "Bitcoin": 22900}
    assert s.to_invest == s.surplus


def test_negative_surplus_means_nothing_to_invest(conn, june_overlap_csv):
    import_path(conn, june_overlap_csv)
    s = reports.summarise(conn, "2026-06")
    assert s.surplus == -1000
    assert s.to_invest == 0


def test_partial_month_detection(conn, may_csv, june_overlap_csv):
    import_path(conn, may_csv)
    import_path(conn, june_overlap_csv)
    assert not reports.summarise(conn, "2026-05").is_partial  # 1st to 31st
    assert reports.summarise(conn, "2026-06").is_partial  # only up to 2 June


def test_shift_month():
    assert reports.shift_month("2025-12", 1) == "2026-01"
    assert reports.shift_month("2026-01", -1) == "2025-12"


def test_investment_plan_compares_with_following_month(conn, may_csv, june_overlap_csv):
    import_path(conn, may_csv)
    import_path(conn, june_overlap_csv)
    plan = {row.month: row for row in reports.investment_plan(conn)}
    may = plan["2026-05"]
    assert may.planned_total == 91600
    assert may.actual_known
    assert may.actual_total == 0
    assert not plan["2026-06"].actual_known
