"""The optional password.

Only a salted hash is stored, in the ``settings`` table. Changing or removing the
password also changes ``auth_epoch``, which logs out every browser that was logged in.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
import time

from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_setting, set_setting

MIN_LENGTH = 8
MAX_LENGTH = 200

# After this many wrong tries in a row, each further try has to wait, starting at
# 30 seconds and doubling up to 15 minutes.
FREE_TRIES = 5
FIRST_WAIT = 30
LONGEST_WAIT = 15 * 60


class PasswordProblem(ValueError):
    """A password that can't be used, with a message to show."""


def has_password(conn: sqlite3.Connection) -> bool:
    return bool(get_setting(conn, "password_hash"))


def check_password(conn: sqlite3.Connection, password: str) -> bool:
    stored = get_setting(conn, "password_hash")
    return bool(stored) and check_password_hash(stored, password or "")


def validate(password: str, confirm: str | None = None) -> None:
    if len(password) < MIN_LENGTH:
        raise PasswordProblem(f"Use at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise PasswordProblem(f"Use at most {MAX_LENGTH} characters.")
    if confirm is not None and password != confirm:
        raise PasswordProblem("The two passwords don't match.")


def set_password(conn: sqlite3.Connection, password: str, confirm: str | None = None) -> str:
    """Save a new password and log out other browsers. Returns the new epoch."""
    validate(password, confirm)
    with conn:
        set_setting(conn, "password_hash", generate_password_hash(password))
        return _new_epoch(conn)


def remove_password(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("DELETE FROM settings WHERE key = 'password_hash'")
        _new_epoch(conn)


def epoch(conn: sqlite3.Connection) -> str:
    """Changes whenever the password does. Stored in the session cookie when you log in."""
    return get_setting(conn, "auth_epoch") or _new_epoch(conn)


def _new_epoch(conn: sqlite3.Connection) -> str:
    value = secrets.token_hex(16)
    set_setting(conn, "auth_epoch", value)
    conn.commit()
    return value


class Throttle:
    """Slows down guessing, separately for this computer and each other device.

    Kept in memory: one per running app. Each try is counted before the password is
    checked, so sending many guesses at once doesn't get round it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, tuple[int, float]] = {}  # who -> (failures, last failure)

    def _wait(self, who: str, now: float) -> int:
        failures, last = self._state.get(who, (0, 0.0))
        if failures < FREE_TRIES:
            return 0
        delay = min(FIRST_WAIT * 2 ** (failures - FREE_TRIES), LONGEST_WAIT)
        return max(0, int(last + delay - now + 0.999))

    def wait(self, who: str = "", now: float | None = None) -> int:
        """Seconds until ``who`` may try again (0 = now)."""
        with self._lock:
            return self._wait(who, time.monotonic() if now is None else now)

    def attempt(self, who: str = "", now: float | None = None) -> int:
        """Start a try: returns the seconds to wait, or 0 and counts it as a failure
        until :meth:`succeeded` says otherwise."""
        now = time.monotonic() if now is None else now
        with self._lock:
            wait = self._wait(who, now)
            if not wait:
                failures, _ = self._state.get(who, (0, 0.0))
                self._state[who] = (failures + 1, now)
            return wait

    def succeeded(self, who: str = "") -> None:
        with self._lock:
            self._state.pop(who, None)
