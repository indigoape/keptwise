"""Investment platforms: what you invest in, how the surplus is split, where to log in.

Each platform is a category in the Investments group. Its statement text is stored as
ordinary "contains" rules, so payments to it are recognised on import.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from .rules import categorise

SHARE_TOLERANCE = 0.005  # shares must add to 100% (±0.5%)


@dataclass
class Platform:
    id: int | None
    name: str
    share: float  # 0..1
    url: str | None = None
    is_isa: bool = False
    patterns: list[str] = field(default_factory=list)


class SettingsProblem(ValueError):
    """Raised when settings can't be saved. The message is shown to the user."""


def list_platforms(conn: sqlite3.Connection) -> list[Platform]:
    rows = conn.execute(
        "SELECT id, name, COALESCE(invest_share, 0) AS share, url, is_isa FROM categories "
        "WHERE bucket = 'investment' ORDER BY share DESC, name"
    ).fetchall()
    platforms = []
    for r in rows:
        patterns = [
            p["pattern"]
            for p in conn.execute(
                "SELECT pattern FROM rules WHERE category_id = ? AND match_type = 'contains' "
                "AND pattern != '' ORDER BY id",
                (r["id"],),
            )
        ]
        platforms.append(
            Platform(r["id"], r["name"], r["share"], r["url"], bool(r["is_isa"]), patterns)
        )
    return platforms


def normalise_url(url: str | None) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    return url


def parse_patterns(text: str) -> list[str]:
    """'VANGUARD, Coinbase uk' -> ['VANGUARD', 'Coinbase uk']"""
    seen, out = set(), []
    for part in re.split(r"[,\n]", text or ""):
        part = part.strip()
        if part and part.casefold() not in seen:
            seen.add(part.casefold())
            out.append(part)
    return out


def validate(platforms: list[Platform]) -> None:
    names = [p.name.strip() for p in platforms]
    if any(not n for n in names):
        raise SettingsProblem("Give every investment a name.")
    if len({n.casefold() for n in names}) != len(names):
        raise SettingsProblem("Two investments have the same name.")
    for p in platforms:
        if not 0 <= p.share <= 1:
            raise SettingsProblem(f"{p.name}: the percentage must be between 0 and 100.")
    if platforms:
        total = sum(p.share for p in platforms)
        if abs(total - 1) > SHARE_TOLERANCE:
            raise SettingsProblem(
                f"The split has to add up to 100%. It adds up to {round(total * 100, 1):g}%."
            )


def save_platforms(conn: sqlite3.Connection, platforms: list[Platform]) -> int:
    """Create, update and remove investment platforms to match ``platforms``.

    Platforms not in the list are removed; their payments go back to the rules. Returns
    how many transactions changed category. Doesn't commit.
    """
    validate(platforms)
    existing = {p.id for p in list_platforms(conn)}
    keep: set[int] = set()
    for p in platforms:
        clash = conn.execute(
            "SELECT id, bucket FROM categories WHERE name = ? AND id IS NOT ?",
            (p.name.strip(), p.id),
        ).fetchone()
        if clash:
            raise SettingsProblem(f"There's already a category called {p.name.strip()}.")
        if p.id in existing:
            conn.execute(
                "UPDATE categories SET name = ?, invest_share = ?, url = ?, is_isa = ? "
                "WHERE id = ?",
                (p.name.strip(), p.share, normalise_url(p.url), int(p.is_isa), p.id),
            )
            cid = p.id
        else:
            cid = conn.execute(
                "INSERT INTO categories(name, bucket, invest_share, url, is_isa) "
                "VALUES (?, 'investment', ?, ?, ?)",
                (p.name.strip(), p.share, normalise_url(p.url), int(p.is_isa)),
            ).lastrowid
        keep.add(cid)
        _set_patterns(conn, cid, p.patterns)
    for cid in existing - keep:
        conn.execute("UPDATE transactions SET manual = 0 WHERE category_id = ?", (cid,))
        conn.execute("DELETE FROM categories WHERE id = ?", (cid,))
    return categorise(conn)


def _set_patterns(conn: sqlite3.Connection, category_id: int, patterns: list[str]) -> None:
    """Make the platform's simple 'contains' rules match ``patterns`` exactly.

    Other kinds of rule (regex, money in only, ...) that you added yourself are kept.
    """
    current = {
        r["pattern"].casefold(): r["id"]
        for r in conn.execute(
            "SELECT id, pattern FROM rules WHERE category_id = ? AND match_type = 'contains' "
            "AND direction = 'any' AND tx_type IS NULL AND pattern != ''",
            (category_id,),
        )
    }
    wanted = {p.casefold(): p for p in patterns}
    for key, rule_id in current.items():
        if key not in wanted:
            conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    for key, pattern in wanted.items():
        if key not in current:
            conn.execute(
                "INSERT INTO rules(pattern, match_type, direction, category_id, priority) "
                "VALUES (?, 'contains', 'any', ?, 30)",
                (pattern, category_id),
            )


def platforms_from_form(form) -> list[Platform]:
    """Read rows named inv-0-name, inv-0-share, ... from a submitted form."""
    indexes = sorted(
        {int(m.group(1)) for key in form for m in [re.match(r"inv-(\d+)-name$", key)] if m}
    )
    platforms = []
    for i in indexes:
        name = (form.get(f"inv-{i}-name") or "").strip()
        if form.get(f"inv-{i}-remove") or not name:
            if not name and any(
                (form.get(f"inv-{i}-{k}") or "").strip() for k in ("url", "patterns")
            ):
                raise SettingsProblem("Give every investment a name.")
            continue
        raw_share = (form.get(f"inv-{i}-share") or "0").strip().rstrip("%") or "0"
        try:
            share = float(raw_share) / 100
        except ValueError as exc:
            raise SettingsProblem(f"{name}: {raw_share!r} isn't a percentage.") from exc
        raw_id = form.get(f"inv-{i}-id") or ""
        platforms.append(
            Platform(
                id=int(raw_id) if raw_id.isdigit() else None,
                name=name,
                share=share,
                url=form.get(f"inv-{i}-url"),
                is_isa=bool(form.get(f"inv-{i}-isa")),
                patterns=parse_patterns(form.get(f"inv-{i}-patterns") or ""),
            )
        )
    return platforms
