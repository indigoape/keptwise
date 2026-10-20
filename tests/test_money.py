import math

import pytest

from keptwise.money import fmt, fmt_short, share, to_pence


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10", 1000),
        ("10.5", 1050),
        ("1,234.56", 123456),
        ("£3.99", 399),
        (2.675, 268),  # half-up, not banker's rounding or float drift
        ("", None),
        (None, None),
        (math.nan, None),
    ],
)
def test_to_pence(raw, expected):
    assert to_pence(raw) == expected


def test_to_pence_rejects_text():
    with pytest.raises(ValueError):
        to_pence("ten pounds")


def test_fmt():
    assert fmt(123456) == "£1,234.56"
    assert fmt(-5) == "−£0.05"
    assert fmt(500, signed=True) == "+£5.00"
    assert fmt(None, blank="–") == "–"


def test_fmt_short():
    assert fmt_short(160000) == "£1.6k"
    assert fmt_short(-49106) == "−£491"


def test_share_rounds_half_up():
    # The old spreadsheet's numbers: 80% / 20% of £270.80
    assert share(27080, 0.8) == 21664
    assert share(27080, 0.2) == 5416
    assert share(5, 0.5) == 3
