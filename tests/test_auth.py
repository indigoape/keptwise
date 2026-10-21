import os
import sys

import pytest
from click.testing import CliRunner

from keptwise import auth
from keptwise import db as storage
from keptwise.cli import app as cli
from keptwise.web import create_app, is_loopback
from test_web import SETUP_FORM

PASSWORD = "correct horse"


@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def app(db_file):
    return create_app(db_file, testing=True)


@pytest.fixture
def client(app):
    client = app.test_client()
    assert client.post("/setup", data=SETUP_FORM).status_code == 302
    return client


@pytest.fixture
def locked(client, db_file):
    """Set up, with a password, and not logged in."""
    conn = storage.connect(db_file)
    auth.set_password(conn, PASSWORD)
    conn.close()
    client.delete_cookie("keptwise_session")
    return client


def log_in(client, password=PASSWORD, **extra):
    return client.post("/login", data={"password": password, **extra})


# ------------------------------------------------------------------ no password


def test_no_password_by_default(client):
    assert client.get("/").status_code == 200
    assert client.get("/login").status_code == 302
    page = client.get("/settings").get_data(as_text=True)
    assert "Set password" in page and "Log out" not in page


# ------------------------------------------------------------------ logging in


def test_asks_for_password(locked):
    res = locked.get("/transactions?month=all")
    assert res.status_code == 302
    assert "/login?next=/transactions?month%3Dall" in res.headers["Location"]
    # Nothing is shown before logging in, not even the sidebar.
    page = locked.get("/login").get_data(as_text=True)
    assert 'type="password"' in page and "Testbank" not in page and "Settings" not in page


def test_changes_refused_before_logging_in(locked):
    assert locked.post("/settings", data=SETUP_FORM).status_code == 401
    assert locked.get("/transactions.csv").status_code == 302


def test_open_pages(locked):
    assert locked.get("/healthz").data == b"ok"
    assert locked.get("/static/app.css").status_code == 200


def test_wrong_password(locked):
    res = log_in(locked, "nope nope nope")
    assert res.status_code == 401
    assert b"isn&#39;t right" in res.data
    assert locked.get("/").status_code == 302


def test_log_in_and_out(locked):
    res = log_in(locked, next="/transactions")
    assert res.status_code == 302 and res.headers["Location"] == "/transactions"
    assert locked.get("/transactions").status_code == 200
    assert b"Log out" in locked.get("/settings").data
    res = locked.post("/logout", follow_redirects=True)
    assert b"Logged out." in res.data
    assert locked.get("/transactions").status_code == 302


def test_next_stays_on_this_site(locked):
    targets = (
        "https://evil.example/",
        "//evil.example/",
        "/\\evil.example",
        "/\t/evil.example",
        "/\r/evil.example",
        "/\n/x",
        "/ /evil.example",
    )
    for target in targets:
        res = log_in(locked, next=target)
        assert res.headers["Location"] == "/", target
        locked.post("/logout")


def test_remember_me(locked):
    res = log_in(locked)
    assert "Expires" not in res.headers["Set-Cookie"]  # until the browser closes
    locked.post("/logout")
    res = log_in(locked, remember="1")
    assert "Expires" in res.headers["Set-Cookie"]


def test_cookie_settings(locked):
    cookie = log_in(locked).headers["Set-Cookie"]
    assert cookie.startswith("keptwise_session=")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_slows_down_guessing(locked):
    for _ in range(auth.FREE_TRIES):
        assert log_in(locked, "wrong password").status_code == 401
    res = log_in(locked)  # even the right one has to wait now
    assert res.status_code == 429
    assert b"Too many wrong tries. Try again in 30 seconds." in res.data


def test_throttle_timing():
    throttle = auth.Throttle()
    for _ in range(auth.FREE_TRIES):
        assert throttle.attempt("me", now=0) == 0
    assert throttle.attempt("me", now=0) == 30
    assert throttle.attempt("someone else", now=0) == 0  # counted separately
    assert throttle.attempt("me", now=30) == 0
    assert throttle.wait("me", now=30) == 60
    now = 30
    for _ in range(10):  # keep guessing as soon as allowed
        now += throttle.wait("me", now=now)
        assert throttle.attempt("me", now=now) == 0
    assert throttle.wait("me", now=now) == auth.LONGEST_WAIT
    assert throttle.wait("me", now=now + auth.LONGEST_WAIT) == 0
    throttle.succeeded("me")
    assert throttle.wait("me", now=now) == 0


def test_other_devices_cant_lock_you_out(db_file):
    app = create_app(db_file, testing=True, allow_network=True)
    conn = storage.connect(db_file)
    auth.set_password(conn, PASSWORD)
    conn.close()
    phone = app.test_client()
    lan = {"REMOTE_ADDR": "192.168.1.20"}
    for _ in range(auth.FREE_TRIES + 1):
        res = phone.post("/login", data={"password": "wrong password"}, environ_base=lan)
    assert res.status_code == 429
    assert log_in(app.test_client()).status_code == 302


# ------------------------------------------------------------------ setting it


def test_set_password_at_setup(app):
    client = app.test_client()
    form = {**SETUP_FORM, "password": PASSWORD, "password_confirm": "different"}
    res = client.post("/setup", data=form)
    assert res.status_code == 200 and b"don&#39;t match" in res.data
    assert client.get("/").headers["Location"].endswith("/setup")  # nothing saved

    form["password_confirm"] = PASSWORD
    res = client.post("/setup", data=form, follow_redirects=True)
    assert b"Settings saved." in res.data
    assert client.get("/").status_code == 200  # this browser is logged in
    assert app.test_client().get("/").status_code == 302  # a new one isn't


def test_setup_without_password_is_fine(app):
    client = app.test_client()
    client.post("/setup", data={**SETUP_FORM, "password": "", "password_confirm": ""})
    assert client.get("/").status_code == 200


def test_set_change_and_remove_in_settings(app, client):
    res = client.post(
        "/settings/password", data={"new": "short", "confirm": "short"}, follow_redirects=True
    )
    assert b"at least 8 characters" in res.data
    res = client.post(
        "/settings/password", data={"new": PASSWORD, "confirm": PASSWORD}, follow_redirects=True
    )
    assert b"Password set." in res.data
    assert client.get("/").status_code == 200  # still logged in here

    other = app.test_client()
    log_in(other)
    assert other.get("/").status_code == 200

    data = {"current": "wrong", "new": "brand new one", "confirm": "brand new one"}
    res = client.post("/settings/password", data=data, follow_redirects=True)
    assert b"current password isn" in res.data
    data["current"] = PASSWORD
    res = client.post("/settings/password", data=data, follow_redirects=True)
    assert b"Other browsers will need to log in again." in res.data
    assert client.get("/").status_code == 200
    assert other.get("/").status_code == 302  # logged out by the change

    res = client.post(
        "/settings/password",
        data={"current": "brand new one", "action": "remove"},
        follow_redirects=True,
    )
    assert b"Password removed." in res.data
    assert other.get("/").status_code == 200


# ------------------------------------------------------------------ other devices


def test_refuses_other_devices(client):
    lan = {"REMOTE_ADDR": "192.168.1.20"}
    assert client.get("/", environ_base=lan).status_code == 403
    # Faking the Host header doesn't help.
    res = client.get("/", environ_base=lan, headers={"Host": "127.0.0.1:8765"})
    assert res.status_code == 403
    assert client.get("/healthz", environ_base=lan).status_code == 403


def test_allow_network_needs_password(db_file):
    app = create_app(db_file, testing=True, allow_network=True)
    client = app.test_client()
    phone = app.test_client()
    lan = {"REMOTE_ADDR": "192.168.1.20"}
    assert phone.get("/", environ_base=lan, headers={"Host": "192.168.1.5:8765"}).status_code == 403

    # Even this computer is refused until a password is set from the command line.
    assert client.get("/").status_code == 403
    conn = storage.connect(db_file)
    auth.set_password(conn, PASSWORD)
    conn.close()
    log_in(client)
    assert client.post("/setup", data=SETUP_FORM).status_code == 302
    res = phone.get("/", environ_base=lan, headers={"Host": "192.168.1.5:8765"})
    assert res.status_code == 302 and "/login" in res.headers["Location"]
    phone.post(
        "/login",
        data={"password": PASSWORD},
        environ_base=lan,
        headers={"Host": "192.168.1.5:8765"},
    )
    assert phone.get("/", environ_base=lan, headers={"Host": "192.168.1.5:8765"}).status_code == 200

    # With other devices allowed, no password means nobody gets in, not even this
    # computer (where a faked Host header could otherwise get through).
    conn = storage.connect(db_file)
    auth.remove_password(conn)
    conn.close()
    assert client.get("/settings", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/settings").status_code == 403
    conn = storage.connect(db_file)
    auth.set_password(conn, PASSWORD)
    conn.close()
    log_in(client)

    # Can't take the password away while other devices are allowed.
    res = client.post(
        "/settings/password",
        data={"current": PASSWORD, "action": "remove"},
        follow_redirects=True,
    )
    assert b"needs a password" in res.data
    conn = storage.connect(db_file)
    assert auth.has_password(conn)
    conn.close()


@pytest.mark.parametrize(
    "address, expected",
    [
        ("127.0.0.1", True),
        ("127.1.2.3", True),
        ("::1", True),
        ("[::1]", True),
        ("::ffff:127.0.0.1", True),
        ("localhost", True),
        ("0.0.0.0", False),
        ("192.168.1.20", False),
        ("::", False),
        ("my-mac.local", False),
        ("", False),
        (None, False),
    ],
)
def test_is_loopback(address, expected):
    assert is_loopback(address) is expected


# ------------------------------------------------------------------ command line


def test_serve_refuses_network_without_flag(db_file):
    res = CliRunner().invoke(cli, ["--db", str(db_file), "serve", "--host", "0.0.0.0"])
    assert res.exit_code == 2
    assert "--allow-network" in res.output


def test_serve_refuses_debug_on_network(db_file):
    args = ["--db", str(db_file), "serve", "--host", "0.0.0.0", "--allow-network", "--debug"]
    res = CliRunner().invoke(cli, args)
    assert res.exit_code == 2 and "--debug" in res.output


def test_serve_refuses_network_without_password(db_file):
    args = ["--db", str(db_file), "serve", "--host", "0.0.0.0", "--allow-network"]
    res = CliRunner().invoke(cli, args)
    assert res.exit_code == 1
    assert "Set a password first" in res.output


def test_password_command(db_file):
    runner = CliRunner()
    res = runner.invoke(cli, ["--db", str(db_file), "password"], input="short\nshort\n")
    assert res.exit_code == 1 and "at least 8" in res.output
    res = runner.invoke(cli, ["--db", str(db_file), "password"], input=f"{PASSWORD}\n{PASSWORD}\n")
    assert res.exit_code == 0, res.output
    conn = storage.connect(db_file)
    assert auth.check_password(conn, PASSWORD)
    assert PASSWORD not in (storage.get_setting(conn, "password_hash") or "")
    conn.close()

    res = runner.invoke(cli, ["--db", str(db_file), "password", "--remove"])
    assert "Password removed." in res.output
    conn = storage.connect(db_file)
    assert not auth.has_password(conn)
    conn.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_database_is_private(db_file):
    storage.connect(db_file).close()
    assert os.stat(db_file).st_mode & 0o777 == 0o600
    storage.backup(db_file, force=True)
    folder = db_file.parent / "backups"
    assert os.stat(folder).st_mode & 0o777 == 0o700
    assert all(os.stat(p).st_mode & 0o777 == 0o600 for p in folder.iterdir())
