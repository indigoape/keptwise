"""Flask web app: create with :func:`create_app`."""

from __future__ import annotations

import ipaddress
import secrets
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, g, redirect, request, session, url_for

from .. import auth
from .. import db as storage
from ..dates import strftime
from ..money import fmt, fmt_short
from ..reports import month_label
from ..seed import seed
from .charts import CHART_HELPERS

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Pages that work without logging in. /shutdown only answers this computer.
OPEN_ENDPOINTS = {"main.login", "main.healthz", "main.shutdown", "static"}


def is_loopback(address: str | None) -> bool:
    """True for addresses that only this computer can use (127.x.x.x, ::1)."""
    if not address:
        return False
    if address == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(address.strip("[]"))
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def create_app(
    db_path: Path | str | None = None, *, testing: bool = False, allow_network: bool = False
) -> Flask:
    """Make the app.

    ``allow_network`` lets other devices connect (``keptwise serve --allow-network``).
    They always have to log in, and are refused while no password is set.
    """
    app = Flask(__name__)
    app.config.update(
        DB_PATH=str(db_path or storage.default_db_path()),
        TESTING=testing,
        ALLOW_NETWORK=allow_network,
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        # Cookies are shared by every app on 127.0.0.1, whatever the port, so use our
        # own name rather than Flask's "session".
        SESSION_COOKIE_NAME="keptwise_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    app.extensions["keptwise_throttle"] = auth.Throttle()

    # Back up (once a day), then create/migrate/seed, and keep a stable secret for
    # flash messages.
    if not testing:
        storage.backup(app.config["DB_PATH"])
    conn = storage.connect(app.config["DB_PATH"])
    seed(conn)
    secret = storage.get_setting(conn, "secret_key")
    if not secret:
        secret = secrets.token_hex(32)
        with conn:
            storage.set_setting(conn, "secret_key", secret)
    app.secret_key = secret
    conn.close()

    @app.before_request
    def guard():
        local = is_loopback(request.remote_addr)
        # Unless other devices are allowed: only this computer, and only requests
        # addressed to it (which stops DNS rebinding). Checking where the request came
        # from too means a faked Host header can't get round it.
        addressed_here = urlsplit("//" + request.host).hostname in LOCAL_HOSTS
        if not app.config["ALLOW_NETWORK"] and not (local and addressed_here):
            abort(403)
        # Only accept changes made from the app's own pages, not from another website
        # open in the same browser.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            source = request.headers.get("Origin") or request.headers.get("Referer")
            if source and urlsplit(source).netloc != request.host:
                abort(403)
        if request.endpoint in OPEN_ENDPOINTS:
            return None
        conn = get_db()
        if not auth.has_password(conn):
            # Without a password, only this computer, and only when other devices
            # aren't allowed: then the Host check above also stops DNS rebinding.
            if local and not app.config["ALLOW_NETWORK"]:
                return None
            # Never let other devices in without a password.
            abort(
                403,
                "Keptwise is open to other devices, so it needs a password. Set one with "
                "`keptwise password` and restart it.",
            )
        if session.get("auth") == auth.epoch(conn):
            return None
        if request.method in ("GET", "HEAD"):
            path = request.full_path if request.query_string else request.path
            return redirect(url_for("main.login", next=path))
        abort(401)

    @app.teardown_appcontext
    def close_db(_exc: BaseException | None) -> None:
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    app.jinja_env.filters.update(
        money=fmt,
        money_signed=lambda p: fmt(p, signed=True),
        money_short=fmt_short,
        month=month_label,
        month_short=lambda m: month_label(m, "%b %Y"),
        day=_day,
        pct=lambda f: f"{round((f or 0) * 100)}%",
        site_name=_site_name,
    )
    app.jinja_env.globals.update(CHART_HELPERS)
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True

    from .views import bp

    app.register_blueprint(bp)
    return app


def get_db() -> sqlite3.Connection:
    from flask import current_app

    if "db" not in g:
        g.db = storage.connect(current_app.config["DB_PATH"])
    return g.db


def _day(value: str | date | None, fmt: str = "%-d %b") -> str:
    if not value:
        return ""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return strftime(value, fmt)


# Friendly names for "Open …" buttons. Matched against the end of the website's host.
KNOWN_SITES = {
    "vanguardinvestor.co.uk": "Vanguard",
    "vanguard.co.uk": "Vanguard",
    "kraken.com": "Kraken",
    "coinbase.com": "Coinbase",
    "trading212.com": "Trading 212",
    "hl.co.uk": "Hargreaves Lansdown",
    "ajbell.co.uk": "AJ Bell",
    "freetrade.io": "Freetrade",
    "ii.co.uk": "interactive investor",
    "fidelity.co.uk": "Fidelity",
    "investengine.com": "InvestEngine",
    "nutmeg.com": "Nutmeg",
    "moneyboxapp.com": "Moneybox",
    "chip.app": "Chip",
    "withplum.com": "Plum",
    "etoro.com": "eToro",
    "lightyear.com": "Lightyear",
    "nsandi.com": "NS&I",
}


def _site_name(url: str | None) -> str:
    """'https://secure.vanguardinvestor.co.uk/' -> 'Vanguard'."""
    from urllib.parse import urlparse

    host = (urlparse(url or "").hostname or "").lower()
    for domain, label in KNOWN_SITES.items():
        if host == domain or host.endswith("." + domain):
            return label
    return host.removeprefix("www.") or "website"
