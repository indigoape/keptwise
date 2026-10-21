"""Routes for the Keptwise web app."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .. import auth, insights, reports
from .. import categories as category_ops
from .. import investments as platforms
from ..db import BUCKETS, get_setting, set_setting
from ..importer import ImportProblem, import_file
from ..money import fmt
from ..rules import (
    DIRECTIONS,
    MATCH_TYPES,
    Rule,
    categorise,
    count_matches,
    suggest_pattern,
    validate_pattern,
)
from . import get_db, is_loopback
from .charts import Bar

bp = Blueprint("main", __name__)

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@bp.app_context_processor
def inject_nav() -> dict:
    conn = get_db()
    uncategorised = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE category_id IS NULL"
    ).fetchone()[0]
    return {
        "nav_uncategorised": uncategorised,
        "BUCKETS": BUCKETS,
        "statement_due": statement_due(conn),
        "bank_name": get_setting(conn, "bank_name") or "",
        "password_set": auth.has_password(conn),
    }


def statement_due(conn, today: date | None = None) -> tuple[date, date] | None:
    """Dates to download if a whole month has ended since the last transaction.

    Starts from the last imported day, so nothing slips through the gap (the overlap
    is skipped on import).
    """
    last = conn.execute("SELECT MAX(date) FROM transactions").fetchone()[0]
    if not last:
        return None
    today = today or date.today()
    last_month_end = today.replace(day=1) - timedelta(days=1)
    last_date = date.fromisoformat(last)
    if last_date >= last_month_end - timedelta(days=3):
        return None
    return last_date, last_month_end


# The UID still says "bookkeeping" (the old name) on purpose: calendars that already have
# the reminder update it in place instead of adding a second one.
REMINDER_ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Keptwise//Monthly statement reminder//EN\r
CALSCALE:GREGORIAN\r
BEGIN:VEVENT\r
UID:bookkeeping-monthly-statement@localhost\r
DTSTAMP:{stamp}\r
DTSTART:{start}\r
DURATION:PT15M\r
RRULE:FREQ=MONTHLY;BYMONTHDAY=1\r
SUMMARY:Download last month's bank statement\r
DESCRIPTION:Download last month's transactions from {bank} as a CSV and import \r
 them into Keptwise: {url}\r
URL:{url}\r
BEGIN:VALARM\r
ACTION:DISPLAY\r
DESCRIPTION:Download last month's bank statement\r
TRIGGER:PT0M\r
END:VALARM\r
END:VEVENT\r
END:VCALENDAR\r
"""


def _ics_text(text: str) -> str:
    """Escape text for an iCalendar property."""
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", " ")


@bp.get("/reminder.ics")
def reminder_ics():
    """A repeating event on the 1st of each month at 6pm, for Apple Calendar."""
    today = date.today()
    first = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    body = REMINDER_ICS.format(
        stamp=f"{today:%Y%m%d}T000000Z",
        start=f"{first:%Y%m%d}T180000",
        url=url_for("main.import_page", _external=True),
        bank=_ics_text(get_setting(get_db(), "bank_name") or "your bank"),
    )
    return Response(
        body,
        mimetype="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="keptwise-reminder.ics"'},
    )


@bp.get("/healthz")
def healthz():
    return "ok"


@bp.post("/shutdown")
def shutdown():
    """Used by `keptwise stop`. Local requests only."""
    from flask import current_app

    stop = current_app.config.get("SHUTDOWN")
    if not is_loopback(request.remote_addr) or stop is None:
        abort(404)
    stop()
    return "stopping"


# -------------------------------------------------------------------- password & login


def _log_in(conn, *, remember: bool) -> None:
    flashes = session.get("_flashes")
    session.clear()  # start a fresh session
    if flashes:
        session["_flashes"] = flashes
    session["auth"] = auth.epoch(conn)
    session.permanent = remember  # 30 days, otherwise until the browser closes


def _safe_next(target: str | None) -> str:
    """Only go back to a page of this app after logging in."""
    from urllib.parse import urlsplit

    target = target or ""
    # Browsers ignore tabs and newlines in URLs and treat \ like /, so refuse anything
    # with them rather than risk "/\t/evil.example" becoming "//evil.example".
    ok = (
        target.startswith("/")
        and not target.startswith("//")
        and target.isprintable()
        and not any(c in target for c in " \\")
    )
    if ok:
        parts = urlsplit(target)
        if not parts.scheme and not parts.netloc:
            return target
    return url_for("main.index")


def _wait_text(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = -(-seconds // 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


@bp.route("/login", methods=["GET", "POST"])
def login():
    conn = get_db()
    target = _safe_next(request.values.get("next"))
    if not auth.has_password(conn):
        return redirect(target)
    if request.method == "GET":
        return render_template("login.html", next=target, error=None)
    throttle = current_app.extensions["keptwise_throttle"]
    # Someone on another device can't lock you out on this computer.
    who = "this computer" if is_loopback(request.remote_addr) else request.remote_addr or ""
    wait = throttle.attempt(who)
    if wait:
        error, status = f"Too many wrong tries. Try again in {_wait_text(wait)}.", 429
    elif auth.check_password(conn, request.form.get("password", "")):
        throttle.succeeded(who)
        _log_in(conn, remember=bool(request.form.get("remember")))
        return redirect(target)
    else:
        error, status = "That password isn't right.", 401
    return render_template("login.html", next=target, error=error), status


@bp.post("/logout")
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("main.login"))


@bp.post("/settings/password")
def password_settings():
    conn = get_db()
    form = request.form
    back = redirect(url_for("main.settings", _anchor="password"))
    had_password = auth.has_password(conn)
    if had_password and not auth.check_password(conn, form.get("current", "")):
        flash("Your current password isn't right.", "error")
        return back
    if form.get("action") == "remove":
        if current_app.config["ALLOW_NETWORK"]:
            flash(
                "Keptwise is open to other devices, so it needs a password. Restart it "
                "without --allow-network to remove the password.",
                "error",
            )
            return back
        auth.remove_password(conn)
        session.pop("auth", None)
        flash("Password removed. Anyone using this computer can open Keptwise.", "success")
        return back
    try:
        auth.set_password(conn, form.get("new", ""), form.get("confirm", ""))
    except auth.PasswordProblem as exc:
        flash(str(exc), "error")
        return back
    _log_in(conn, remember=session.permanent)
    if had_password:
        flash("Password changed. Other browsers will need to log in again.", "success")
    else:
        flash("Password set. You'll need it next time you open Keptwise.", "success")
    return back


def _categories(conn):
    rows = conn.execute(
        "SELECT * FROM categories ORDER BY CASE bucket WHEN 'regular' THEN 0 WHEN 'cash' THEN 1 "
        "WHEN 'investment' THEN 2 ELSE 3 END, name"
    ).fetchall()
    grouped: dict[str, list] = {key: [] for key in BUCKETS}
    for row in rows:
        grouped[row["bucket"]].append(row)
    return rows, grouped


# --------------------------------------------------------------------------- overview


@bp.get("/")
def index():
    conn = get_db()
    months = reports.months(conn)
    if not months:
        if not get_setting(conn, "setup_done"):
            return redirect(url_for("main.setup"))
        return render_template("welcome.html")
    return redirect(url_for("main.month", month=months[-1]))


# ------------------------------------------------------------------- setup & settings


def _save_settings(conn, form) -> str | None:
    """Save bank name and investment platforms. Returns an error message or None."""
    try:
        new_platforms = platforms.platforms_from_form(form)
        with conn:
            set_setting(conn, "bank_name", (form.get("bank_name") or "").strip()[:60])
            changed = platforms.save_platforms(conn, new_platforms)
            set_setting(conn, "setup_done", "1")
    except platforms.SettingsProblem as exc:
        return str(exc)
    if changed:
        flash(f"Settings saved. {_changed_text(changed)}", "success")
    else:
        flash("Settings saved.", "success")
    return None


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First run: your bank and what you invest in."""
    conn = get_db()
    if request.method == "POST":
        password = request.form.get("password", "")
        error = None
        if password and not auth.has_password(conn):
            try:
                auth.validate(password, request.form.get("password_confirm", ""))
            except auth.PasswordProblem as exc:
                error = f"Password: {exc}"
        error = error or _save_settings(conn, request.form)
        if error is None:
            if password and not auth.has_password(conn):
                auth.set_password(conn, password)
                _log_in(conn, remember=False)
            return redirect(url_for("main.import_page"))
        flash(error, "error")
        return render_template("setup.html", form=request.form, rows=_form_rows(request.form))
    rows = _platform_rows(platforms.list_platforms(conn))
    return render_template("setup.html", form={}, rows=rows)


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    conn = get_db()
    if request.method == "POST":
        error = _save_settings(conn, request.form)
        if error is None:
            return redirect(url_for("main.settings"))
        flash(error, "error")
        return render_template(
            "settings.html", form=request.form, rows=_form_rows(request.form), db_path=_db_path()
        )
    rows = _platform_rows(platforms.list_platforms(conn))
    return render_template(
        "settings.html",
        form={"bank_name": get_setting(conn, "bank_name") or ""},
        rows=rows,
        db_path=_db_path(),
    )


def _db_path() -> str:
    from flask import current_app

    return current_app.config["DB_PATH"]


def _platform_rows(items) -> list[dict]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "share": f"{round(p.share * 100, 1):g}",
            "url": p.url or "",
            "isa": p.is_isa,
            "patterns": ", ".join(p.patterns),
        }
        for p in items
    ]


def _form_rows(form) -> list[dict]:
    """Re-show what was typed when saving failed."""
    indexes = sorted(
        {int(m.group(1)) for key in form for m in [re.match(r"inv-(\d+)-name$", key)] if m}
    )
    rows = [
        {
            "id": form.get(f"inv-{i}-id", ""),
            "name": form.get(f"inv-{i}-name", ""),
            "share": form.get(f"inv-{i}-share", ""),
            "url": form.get(f"inv-{i}-url", ""),
            "isa": bool(form.get(f"inv-{i}-isa")),
            "patterns": form.get(f"inv-{i}-patterns", ""),
        }
        for i in indexes
        if not form.get(f"inv-{i}-remove")
    ]
    return rows


@bp.get("/months/<month>")
def month(month: str):
    if not MONTH_RE.match(month):
        abort(404)
    conn = get_db()
    months = reports.months(conn)
    if month not in months:
        if not months:
            return redirect(url_for("main.index"))
        abort(404)
    summary = reports.summarise(conn, month)
    idx = months.index(month)
    end = min(len(months), max(idx + 3, 12))
    window = months[max(0, end - 12) : end]
    summaries = {m: reports.summarise(conn, m) for m in window}
    strip = [
        Bar(
            key=m,
            label=reports.month_label(m, "%b"),
            value=summaries[m].surplus,
            href=url_for("main.month", month=m),
            current=m == month,
            title=f"{reports.month_label(m)}: {'kept' if summaries[m].surplus >= 0 else 'over by'} "
            f"{fmt(abs(summaries[m].surplus))}",
        )
        for m in window
    ]

    next_month = reports.shift_month(month, 1)
    following = reports.summarise(conn, next_month) if next_month in months else None
    ranges = insights.normals(conn, month)
    verdicts = {
        cid: (insights.compare(c.net_spend, normal), normal.describe())
        for c in summary.category_spend
        if (cid := c.category_id) is not None and (normal := ranges.get(cid))
    }
    adhoc = [c for c in summary.category_spend if c.bucket == "adhoc" and c.net_spend > 0]
    adhoc_max = max((c.net_spend for c in adhoc), default=0)
    regulars = [c for c in summary.category_spend if c.bucket == "regular"]
    regulars.sort(key=lambda c: (c.money_in == 0, -abs(c.money_in - c.money_out)))
    cash = [c for c in summary.category_spend if c.bucket == "cash"]

    return render_template(
        "month.html",
        s=summary,
        months=months,
        prev_month=months[idx - 1] if idx > 0 else None,
        next_month=months[idx + 1] if idx + 1 < len(months) else None,
        strip=strip,
        following=following,
        adhoc=adhoc,
        adhoc_max=adhoc_max,
        adhoc_total=sum(c.net_spend for c in adhoc),
        verdicts=verdicts,
        flags=insights.month_flags(conn, month),
        regulars=regulars,
        cash=cash,
        biggest=reports.top_transactions(conn, month),
    )


# ----------------------------------------------------------------------- transactions


def _transaction_query(args) -> tuple[str, list]:
    sql = """
        SELECT t.*, c.name AS category, COALESCE(c.bucket, 'adhoc') AS bucket,
               r.pattern AS rule_pattern
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        LEFT JOIN rules r ON r.id = t.rule_id
        WHERE 1 = 1
    """
    params: list = []
    if (m := args.get("month")) and MONTH_RE.match(m):
        sql += " AND substr(t.date, 1, 7) = ?"
        params.append(m)
    if (b := args.get("bucket")) in BUCKETS:
        sql += " AND COALESCE(c.bucket, 'adhoc') = ?"
        params.append(b)
    cat = args.get("category")
    if cat == "none":
        sql += " AND t.category_id IS NULL"
    elif cat and cat.isdigit():
        sql += " AND t.category_id = ?"
        params.append(int(cat))
    if q := (args.get("q") or "").strip():
        sql += " AND (t.description LIKE ? OR t.note LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    direction = args.get("direction")
    if direction == "in":
        sql += " AND t.amount > 0"
    elif direction == "out":
        sql += " AND t.amount < 0"
    sql += " ORDER BY t.date DESC, t.id DESC"
    return sql, params


@bp.get("/transactions")
def transactions():
    conn = get_db()
    all_months = reports.months(conn)
    if "month" not in request.args and all_months:
        # Open on the latest month; "All months" is an explicit choice (month=all).
        args = request.args.to_dict()
        args["month"] = all_months[-1]
        return redirect(url_for("main.transactions", **args))
    sql, params = _transaction_query(request.args)
    rows = conn.execute(sql, params).fetchall()
    categories, grouped = _categories(conn)
    return render_template(
        "transactions.html",
        rows=rows,
        total_in=sum(r["amount"] for r in rows if r["amount"] > 0),
        total_out=-sum(r["amount"] for r in rows if r["amount"] < 0),
        months=list(reversed(all_months)),
        categories=categories,
        grouped=grouped,
        args=request.args,
    )


@bp.get("/transactions.csv")
def transactions_csv():
    conn = get_db()
    sql, params = _transaction_query(request.args)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Date", "Type", "Description", "Amount", "Bucket", "Category", "Note"])
    for row in conn.execute(sql, params):
        writer.writerow(
            [
                row["date"],
                row["tx_type"],
                row["description"],
                f"{row['amount'] / 100:.2f}",
                BUCKETS[row["bucket"]],
                row["category"] or "",
                row["note"],
            ]
        )
    name = f"transactions-{request.args.get('month') or 'all'}.csv"
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@bp.post("/transactions/<int:tx_id>")
def update_transaction(tx_id: int):
    """Set a transaction's category (or hand it back to the rules) and/or its note."""
    conn = get_db()
    tx = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if tx is None:
        abort(404)
    data = request.get_json(silent=True) or request.form
    with conn:
        if "note" in data:
            conn.execute(
                "UPDATE transactions SET note = ? WHERE id = ?",
                (str(data["note"]).strip()[:500], tx_id),
            )
        if "category" in data:
            value = str(data["category"])
            if value == "auto":
                conn.execute("UPDATE transactions SET manual = 0 WHERE id = ?", (tx_id,))
                categorise(conn, ids=[tx_id])
            else:
                category_id = int(value) if value.isdigit() else None
                if (
                    category_id is not None
                    and not conn.execute(
                        "SELECT 1 FROM categories WHERE id = ?", (category_id,)
                    ).fetchone()
                ):
                    abort(400)
                conn.execute(
                    "UPDATE transactions SET category_id = ?, rule_id = NULL, manual = 1 "
                    "WHERE id = ?",
                    (category_id, tx_id),
                )
    row = conn.execute(
        "SELECT t.*, c.name AS category, COALESCE(c.bucket,'adhoc') AS bucket "
        "FROM transactions t LEFT JOIN categories c ON c.id = t.category_id WHERE t.id = ?",
        (tx_id,),
    ).fetchone()

    suggestion = None
    if row["category_id"] and row["manual"]:
        pattern = suggest_pattern(row["description"])
        probe = Rule(0, pattern, "contains", "any", None, row["category_id"], 0)
        others = [
            r
            for r in conn.execute(
                "SELECT id, description, amount, category_id FROM transactions "
                "WHERE id != ? AND manual = 0",
                (tx_id,),
            )
            if probe.matches(r["description"], r["amount"])
            and r["category_id"] != row["category_id"]
        ]
        suggestion = {
            "pattern": pattern,
            "category_id": row["category_id"],
            "category": row["category"],
            "direction": "in" if row["amount"] > 0 else "out",
            "others": len(others),
        }
    if request.is_json:
        return jsonify(
            id=row["id"],
            category_id=row["category_id"],
            category=row["category"],
            bucket=row["bucket"],
            bucket_label=BUCKETS[row["bucket"]],
            manual=bool(row["manual"]),
            note=row["note"],
            suggestion=suggestion,
            uncategorised=conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE category_id IS NULL"
            ).fetchone()[0],
        )
    flash("Transaction updated.", "success")
    return redirect(request.referrer or url_for("main.transactions"))


# ------------------------------------------------------------------------------ import


@bp.route("/import", methods=["GET", "POST"])
def import_page():
    conn = get_db()
    results, problems = [], []
    if request.method == "POST":
        files = [f for f in request.files.getlist("files") if f and f.filename]
        if not files:
            problems.append(("", "Choose at least one CSV file to import."))
        for upload in files:
            name = upload.filename or "upload.csv"
            if not name.lower().endswith((".csv", ".txt")):
                problems.append(
                    (name, "Only CSV files can be imported. Download your statement as CSV.")
                )
                continue
            try:
                results.append(import_file(conn, upload.read(), name))
            except ImportProblem as exc:
                problems.append((name, str(exc)))
        added_ids = [i for r in results for i in r.added_ids]
        new_rows = []
        if added_ids:
            marks = ",".join("?" * len(added_ids))
            new_rows = conn.execute(
                f"SELECT t.*, c.name AS category, COALESCE(c.bucket,'adhoc') AS bucket "
                f"FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
                f"WHERE t.id IN ({marks}) ORDER BY t.date DESC, t.id DESC",
                added_ids,
            ).fetchall()
        touched = sorted({r["date"][:7] for r in new_rows})
        history = _import_history(conn)
        return render_template(
            "import.html",
            results=results,
            problems=problems,
            new_rows=new_rows,
            touched=touched,
            uncategorised_new=sum(1 for r in new_rows if r["category_id"] is None),
            history=history,
        )
    return render_template("import.html", history=_import_history(conn))


def _import_history(conn):
    return conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 20").fetchall()


# ------------------------------------------------------------------------------- rules


@bp.get("/rules")
def rules_page():
    conn = get_db()
    categories, grouped = _categories(conn)
    rules = conn.execute(
        "SELECT r.*, c.name AS category, c.bucket FROM rules r "
        "JOIN categories c ON c.id = r.category_id "
        "ORDER BY r.priority DESC, length(r.pattern) DESC, r.id"
    ).fetchall()
    by_category: dict[int, list] = {}
    for rule in rules:
        by_category.setdefault(rule["category_id"], []).append(rule)
    tx_counts = {
        row["category_id"]: row["n"]
        for row in conn.execute(
            "SELECT category_id, COUNT(*) AS n FROM transactions GROUP BY category_id"
        )
    }
    edit_id = request.args.get("edit", type=int)
    editing = next((r for r in rules if r["id"] == edit_id), None)
    prefill = {
        "pattern": request.args.get("pattern", ""),
        "category_id": request.args.get("category_id", type=int),
        "direction": request.args.get("direction", "any"),
    }
    return render_template(
        "rules.html",
        categories=categories,
        grouped=grouped,
        by_category=by_category,
        matches=count_matches(conn),
        tx_counts=tx_counts,
        rule_total=len(rules),
        editing=editing,
        prefill=prefill,
        MATCH_TYPES=MATCH_TYPES,
        DIRECTIONS=DIRECTIONS,
    )


def _rule_from_form(form) -> tuple[dict, str | None]:
    values = {
        "pattern": (form.get("pattern") or "").strip(),
        "match_type": form.get("match_type") or "contains",
        "direction": form.get("direction") or "any",
        "tx_type": (form.get("tx_type") or "").strip().upper() or None,
        "category_id": form.get("category_id", type=int),
        "priority": 10,
    }
    try:
        values["priority"] = int(form.get("priority") or 10)
    except ValueError:
        return values, "Priority must be a whole number."
    if values["direction"] not in DIRECTIONS:
        return values, "Unknown direction."
    if not values["category_id"]:
        return values, "Choose a category."
    if not values["pattern"] and values["tx_type"]:
        return values, None  # e.g. "every FPO payment"
    return values, validate_pattern(values["pattern"], values["match_type"])


@bp.post("/rules")
def create_rule():
    conn = get_db()
    values, error = _rule_from_form(request.form)
    if error:
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify(error=error), 400
        flash(error, "error")
        return redirect(url_for("main.rules_page"))
    with conn:
        conn.execute(
            "INSERT INTO rules(pattern, match_type, direction, tx_type, category_id, priority) "
            "VALUES (:pattern, :match_type, :direction, :tx_type, :category_id, :priority)",
            values,
        )
        changed = categorise(conn)
    message = f"Rule added. {_changed_text(changed)}"
    if request.headers.get("Accept") == "application/json":
        return jsonify(message=message, changed=changed)
    flash(message, "success")
    return redirect(request.form.get("next") or url_for("main.rules_page"))


@bp.post("/rules/<int:rule_id>")
def update_rule(rule_id: int):
    conn = get_db()
    values, error = _rule_from_form(request.form)
    if error:
        flash(error, "error")
        return redirect(url_for("main.rules_page", edit=rule_id))
    with conn:
        conn.execute(
            "UPDATE rules SET pattern=:pattern, match_type=:match_type, direction=:direction, "
            "tx_type=:tx_type, category_id=:category_id, priority=:priority WHERE id=:id",
            {**values, "id": rule_id},
        )
        changed = categorise(conn)
    flash(f"Rule saved. {_changed_text(changed)}", "success")
    return redirect(url_for("main.rules_page") + f"#category-{values['category_id']}")


@bp.post("/rules/<int:rule_id>/delete")
def delete_rule(rule_id: int):
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        changed = categorise(conn)
    flash(f"Rule deleted. {_changed_text(changed)}", "success")
    return redirect(url_for("main.rules_page"))


@bp.get("/rules/preview")
def preview_rule():
    """Live preview for the rule form: which transactions would this match?"""
    conn = get_db()
    values, error = _rule_from_form(request.args)
    if error and error != "Choose a category.":
        return jsonify(error=error, count=0, samples=[])
    if not values["pattern"] and not values["tx_type"]:
        return jsonify(count=0, samples=[])
    probe = Rule(
        0,
        values["pattern"],
        values["match_type"],
        values["direction"],
        values["tx_type"],
        values["category_id"] or 0,
        values["priority"],
    )
    hits = [
        r
        for r in conn.execute(
            "SELECT t.description, t.amount, t.tx_type, t.date, c.name AS category "
            "FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
            "ORDER BY t.date DESC"
        )
        if probe.matches(r["description"], r["amount"], r["tx_type"])
    ]
    seen, samples = set(), []
    for r in hits:
        if r["description"] not in seen:
            seen.add(r["description"])
            samples.append({"description": r["description"], "category": r["category"]})
        if len(samples) == 6:
            break
    return jsonify(count=len(hits), samples=samples)


def _changed_text(changed: int) -> str:
    if changed == 0:
        return "No transactions changed category."
    if changed == 1:
        return "1 transaction changed category."
    return f"{changed} transactions changed category."


# -------------------------------------------------------------------------- categories


@bp.post("/categories")
def create_category():
    conn = get_db()
    name = (request.form.get("name") or "").strip()
    bucket = request.form.get("bucket") or "adhoc"
    if not name or bucket not in BUCKETS:
        flash("Give the category a name and a group.", "error")
        return redirect(url_for("main.rules_page"))
    if conn.execute("SELECT 1 FROM categories WHERE name = ?", (name,)).fetchone():
        flash(f"There's already a category called {name}.", "error")
        return redirect(url_for("main.rules_page"))
    with conn:
        cur = conn.execute(
            "INSERT INTO categories(name, bucket, invest_share) VALUES (?,?,?)",
            (name, bucket, 0 if bucket == "investment" else None),
        )
    flash(f"Added {name}.", "success")
    return redirect(url_for("main.rules_page") + f"#category-{cur.lastrowid}")


@bp.post("/categories/<int:category_id>")
def update_category(category_id: int):
    conn = get_db()
    name = (request.form.get("name") or "").strip()
    bucket = request.form.get("bucket")
    if not name or bucket not in BUCKETS:
        flash("Give the category a name and a group.", "error")
        return redirect(url_for("main.rules_page"))
    clash = conn.execute(
        "SELECT 1 FROM categories WHERE name = ? AND id != ?", (name, category_id)
    ).fetchone()
    if clash:
        flash(f"There's already a category called {name}.", "error")
        return redirect(url_for("main.rules_page"))
    url = (request.form.get("url") or "").strip() or None
    if url and not re.match(r"^https?://", url):
        url = "https://" + url
    with conn:
        conn.execute(
            "UPDATE categories SET name = ?, bucket = ?, "
            "invest_share = CASE WHEN ? = 'investment' THEN COALESCE(invest_share, 0) END, "
            "url = ?, is_isa = ? WHERE id = ?",
            (
                name,
                bucket,
                bucket,
                url if bucket == "investment" else None,
                int(bucket == "investment" and bool(request.form.get("is_isa"))),
                category_id,
            ),
        )
    flash(f"Saved {name}.", "success")
    return redirect(url_for("main.rules_page") + f"#category-{category_id}")


@bp.post("/categories/<int:category_id>/merge")
def merge_category(category_id: int):
    conn = get_db()
    target_id = request.form.get("target_id", type=int)
    names = {
        r["id"]: r["name"]
        for r in conn.execute(
            "SELECT id, name FROM categories WHERE id IN (?, ?)", (category_id, target_id or 0)
        )
    }
    if category_id not in names or target_id not in names or target_id == category_id:
        flash("Choose a different category to merge into.", "error")
        return redirect(url_for("main.rules_page"))
    with conn:
        moved = category_ops.merge(conn, category_id, target_id)
        categorise(conn)
    flash(
        f"Merged {names[category_id]} into {names[target_id]}. "
        f"{moved} transaction{'s' if moved != 1 else ''} moved.",
        "success",
    )
    return redirect(url_for("main.rules_page") + f"#category-{target_id}")


@bp.post("/categories/<int:category_id>/delete")
def delete_category(category_id: int):
    conn = get_db()
    row = conn.execute("SELECT name FROM categories WHERE id = ?", (category_id,)).fetchone()
    if row is None:
        abort(404)
    with conn:
        conn.execute("UPDATE transactions SET manual = 0 WHERE category_id = ?", (category_id,))
        conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        categorise(conn)
    flash(f"Deleted {row['name']}. Its transactions went back to the rules.", "success")
    return redirect(url_for("main.rules_page"))


# ------------------------------------------------------------------------- investments


@bp.get("/investments")
def investments():
    conn = get_db()
    plan = reports.investment_plan(conn)
    targets = reports.investment_categories(conn)
    names = [t["name"] for t in targets]
    put_in = {n: 0 for n in names}
    taken_out = {n: 0 for n in names}
    for summary in reports.history(conn):
        for n in names:
            put_in[n] += summary.invested.get(n, 0)
            taken_out[n] += summary.withdrawn.get(n, 0)
    # Compare plan with reality only where both months are imported.
    tracked = [row for row in plan if row.actual_known]
    planned_total = sum(row.planned_total for row in tracked)
    actual_total = sum(row.actual_total for row in tracked)
    chart_rows = [
        (
            row.invest_month_label.split()[0],
            row.planned_total,
            row.actual_total if row.actual_known else None,
        )
        for row in plan[-12:]
    ]
    return render_template(
        "investments.html",
        plan=list(reversed(plan)),
        targets=targets,
        names=names,
        put_in=put_in,
        taken_out=taken_out,
        invested_total=sum(put_in.values()),
        withdrawn_total=sum(taken_out.values()),
        planned_total=planned_total,
        actual_total=actual_total,
        chart_rows=chart_rows,
        share_total=round(sum(t["share"] for t in targets) * 100),
    )


# --------------------------------------------------------------------------- tax years


@bp.get("/tax-years")
def tax_years_index():
    conn = get_db()
    years = reports.tax_years(conn)
    if not years:
        return redirect(url_for("main.index"))
    current = reports.tax_year_of(date.today())
    return redirect(url_for("main.tax_year", year=current if current in years else years[-1]))


@bp.get("/tax-years/<int:year>")
def tax_year(year: int):
    conn = get_db()
    years = reports.tax_years(conn)
    if year not in years:
        abort(404)
    ty = reports.tax_year_summary(conn, year)
    investments = []
    for cat in conn.execute(
        "SELECT * FROM categories WHERE bucket = 'investment' "
        "ORDER BY COALESCE(invest_share, 0) DESC, name"
    ):
        payments = reports.investment_payments(conn, cat["id"], ty.start, ty.end)
        investments.append((cat, payments))
    idx = years.index(year)
    return render_template(
        "tax_year.html",
        ty=ty,
        investments=investments,
        prev_year=years[idx - 1] if idx > 0 else None,
        next_year=years[idx + 1] if idx + 1 < len(years) else None,
        allowance=reports.ISA_ALLOWANCE,
        days_left=ty.days_left(),
    )


@bp.get("/investments/<int:category_id>.csv")
def investment_csv(category_id: int):
    """Every payment to and from one investment, for your records."""
    conn = get_db()
    cat = conn.execute(
        "SELECT * FROM categories WHERE id = ? AND bucket = 'investment'", (category_id,)
    ).fetchone()
    if cat is None:
        abort(404)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Date", "Tax year", "Description", "Paid in", "Taken out", "Note"])
    for row in reports.investment_payments(conn, category_id):
        day = date.fromisoformat(row["date"])
        writer.writerow(
            [
                row["date"],
                reports.tax_year_label(reports.tax_year_of(day)),
                row["description"],
                f"{-row['amount'] / 100:.2f}" if row["amount"] < 0 else "",
                f"{row['amount'] / 100:.2f}" if row["amount"] > 0 else "",
                row["note"],
            ]
        )
    name = re.sub(r"[^a-z0-9]+", "-", cat["name"].lower()).strip("-") or "investment"
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}-payments.csv"'},
    )


@bp.app_template_global()
def today() -> date:
    return date.today()
