"""Date formatting that works the same on every operating system."""

from __future__ import annotations

from datetime import date


def strftime(value: date, fmt: str) -> str:
    """Like ``value.strftime(fmt)``, but ``%-d`` (day without a leading zero) also works
    on Windows, whose C library doesn't support the ``-`` flag."""
    return value.strftime(fmt.replace("%-d", str(value.day)))
