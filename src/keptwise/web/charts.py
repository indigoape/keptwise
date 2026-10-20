"""Small server-rendered SVG charts.

Rendering on the server keeps the app dependency-free and fully offline. Colours come
from CSS classes, so charts follow the light/dark theme automatically.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape

from markupsafe import Markup

from ..money import fmt, fmt_short


@dataclass
class Bar:
    key: str
    label: str
    value: int  # pence
    href: str | None = None
    current: bool = False
    title: str | None = None


def _nice_ceiling(value: int) -> int:
    """Round up to a tidy axis maximum (in pence)."""
    if value <= 0:
        return 100_00
    pounds = value / 100
    magnitude = 10 ** (len(str(int(pounds))) - 1)
    for step in (1, 2, 2.5, 5, 10):
        if pounds <= step * magnitude:
            return int(step * magnitude * 100)
    return int(pounds * 100)


def month_bars(bars: Sequence[Bar], *, height: int = 168, aria_label: str = "") -> Markup:
    """Vertical bars around a zero line; positive bars in the accent colour."""
    if not bars:
        return Markup("")
    width_per = 56
    pad_left, pad_top, pad_bottom = 8, 22, 26
    plot_h = height - pad_top - pad_bottom
    top = _nice_ceiling(max((b.value for b in bars), default=0))
    bottom = (
        _nice_ceiling(-min((b.value for b in bars), default=0))
        if any(b.value < 0 for b in bars)
        else 0
    )
    span = top + bottom
    zero_y = pad_top + plot_h * top / span
    width = pad_left * 2 + width_per * len(bars)

    def y(value: int) -> float:
        return pad_top + plot_h * (top - value) / span

    parts = [
        f'<svg class="chart month-bars" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(aria_label)}" preserveAspectRatio="xMinYMid meet">'
    ]
    parts.append(
        f'<line class="axis" x1="{pad_left}" x2="{width - pad_left}" '
        f'y1="{zero_y:.1f}" y2="{zero_y:.1f}" />'
    )
    for i, bar in enumerate(bars):
        x = pad_left + i * width_per
        bar_w = width_per - 18
        y0, y1 = sorted((y(bar.value), zero_y))
        h = max(y1 - y0, 1.5)
        tone = "pos" if bar.value >= 0 else "neg"
        cls = f"bar {tone}" + (" current" if bar.current else "")
        title = escape(bar.title or f"{bar.label}: {fmt(bar.value)}")
        label_y = y0 - 6 if bar.value >= 0 else y1 + 13
        inner = (
            f"<title>{title}</title>"
            f'<rect class="hit" x="{x}" y="0" width="{width_per}" height="{height}" />'
            f'<rect class="{cls}" x="{x + 9}" y="{y0:.1f}" width="{bar_w}" '
            f'height="{h:.1f}" rx="3" />'
            f'<text class="value{" current" if bar.current else ""}" x="{x + width_per / 2}" '
            f'y="{label_y:.1f}" text-anchor="middle">{escape(fmt_short(bar.value))}</text>'
            f'<text class="tick{" current" if bar.current else ""}" x="{x + width_per / 2}" '
            f'y="{height - 8}" text-anchor="middle">{escape(bar.label)}</text>'
        )
        if bar.href:
            parts.append(f'<a href="{escape(bar.href)}" class="bar-link">{inner}</a>')
        else:
            parts.append(f"<g>{inner}</g>")
    parts.append("</svg>")
    return Markup("".join(parts))


def paired_bars(
    rows: Sequence[tuple[str, int, int | None]],
    *,
    height: int = 180,
    aria_label: str = "",
    first_label: str = "Planned",
    second_label: str = "Invested",
) -> Markup:
    """Pairs of bars per month (planned vs actually invested). ``None`` = unknown yet."""
    if not rows:
        return Markup("")
    width_per = 64
    pad, pad_top, pad_bottom = 8, 18, 26
    plot_h = height - pad_top - pad_bottom
    top = _nice_ceiling(max(max(a, b or 0) for _, a, b in rows))
    width = pad * 2 + width_per * len(rows)
    base_y = pad_top + plot_h

    def bar_h(value: int) -> float:
        return max(plot_h * max(value, 0) / top, 0)

    parts = [
        f'<svg class="chart paired-bars" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(aria_label)}" preserveAspectRatio="xMinYMid meet">',
        f'<line class="axis" x1="{pad}" x2="{width - pad}" y1="{base_y}" y2="{base_y}" />',
    ]
    for i, (label, planned, actual) in enumerate(rows):
        x = pad + i * width_per + 10
        ph = bar_h(planned)
        title = f"{label}: {first_label.lower()} {fmt(planned)}"
        parts.append("<g>")
        parts.append(
            f'<rect class="bar planned" x="{x}" y="{base_y - ph:.1f}" width="20" '
            f'height="{max(ph, 1.5):.1f}" rx="2" />'
        )
        if actual is not None:
            ah = bar_h(actual)
            title += f", {second_label.lower()} {fmt(actual)}"
            parts.append(
                f'<rect class="bar actual" x="{x + 22}" y="{base_y - ah:.1f}" width="20" '
                f'height="{max(ah, 1.5):.1f}" rx="2" />'
            )
        parts.append(f"<title>{escape(title)}</title>")
        parts.append(
            f'<text class="tick" x="{x + 21}" y="{height - 8}" text-anchor="middle">'
            f"{escape(label)}</text></g>"
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def bar_width(value: int, maximum: int) -> str:
    """CSS width percentage for horizontal HTML bars."""
    if maximum <= 0 or value <= 0:
        return "0%"
    return f"{max(100 * value / maximum, 1.5):.1f}%"


CHART_HELPERS: dict[str, Callable] = {
    "month_bars": month_bars,
    "paired_bars": paired_bars,
    "bar_width": bar_width,
}
