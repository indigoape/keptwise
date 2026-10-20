"""Money helpers.

All amounts are stored as integer pence to avoid floating point drift. Credits are
positive and debits negative.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MINUS = "−"  # proper minus sign for display


def to_pence(value: object) -> int | None:
    """Parse a bank-style amount ("1,234.5", "10", 3.2, "") into pence.

    Returns ``None`` for blank values.
    """
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN from spreadsheets
        return None
    text = str(value).strip().replace(",", "").replace("£", "")
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Not a money amount: {value!r}") from exc
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def share(pence: int, fraction: float) -> int:
    """Return ``fraction`` of ``pence``, rounded half-up to the nearest penny."""
    result = Decimal(pence) * Decimal(str(fraction))
    return int(result.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fmt(pence: int | None, *, signed: bool = False, blank: str = "") -> str:
    """Format pence as pounds, e.g. ``-123456`` -> ``−£1,234.56``."""
    if pence is None:
        return blank
    sign = ""
    if pence < 0:
        sign = MINUS
    elif signed and pence > 0:
        sign = "+"
    pounds, pennies = divmod(abs(pence), 100)
    return f"{sign}£{pounds:,}.{pennies:02d}"


def fmt_short(pence: int) -> str:
    """Compact format for chart labels: £1.2k, £850, −£40."""
    sign = MINUS if pence < 0 else ""
    pounds = abs(pence) / 100
    if pounds >= 1000:
        return f"{sign}£{pounds / 1000:.1f}k"
    return f"{sign}£{pounds:,.0f}"
