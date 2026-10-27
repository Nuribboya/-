from __future__ import annotations

from resell_radar.models import Release, Venue
from resell_radar.tiers import _alias_pattern, notch

LETTERS = ["C", "B", "A", "S"]
MIN_HISTORY = 8  # releases a venue must have carried before history moves its grade
HIGH_SHARE, LOW_SHARE = 0.30, 0.05


def base_grade(venue: Venue, tiers: dict) -> tuple[str, str]:
    if venue.overseas:
        return "C", "해외 · 배대지 필요"
    if not venue.online:
        return "C", "오프라인 방문 필요"
    text = venue.name.lower()
    for entry in tiers.get("venues", []):
        if any(_alias_pattern(a).search(text) for a in entry["aliases"]):
            return entry["grade"], entry["note"]
    return "B", "기본값 (정보 부족)"


def premium_share(levels: list[str]) -> float:
    return sum(1 for lv in levels if notch(lv) >= notch("A-")) / len(levels) if levels else 0.0


def grade_venue(venue: Venue, tiers: dict, history: dict[str, list[str]]) -> tuple[str, str]:
    grade, note = base_grade(venue, tiers)
    levels = history.get(venue.name, [])
    if grade == "C" or len(levels) < MIN_HISTORY:
        return grade, note
    share = premium_share(levels)
    i = LETTERS.index(grade)
    if share >= HIGH_SHARE and i < 3:
        return LETTERS[i + 1], f"{note} · A급 이상 {share:.0%}로 상향"
    if share <= LOW_SHARE and i > 0:
        return LETTERS[i - 1], f"{note} · A급 이상 {share:.0%}로 하향"
    return grade, note


def apply_grades(releases: list[Release], tiers: dict, history: dict[str, list[str]]) -> None:
    """Grade every venue, order them best first, and point the card's link at the best one."""
    for r in releases:
        if not r.venues:
            continue
        for v in r.venues:
            v.grade, v.grade_note = grade_venue(v, tiers, history)
        r.venues.sort(key=lambda v: (-LETTERS.index(v.grade), v.method != "draw"))
        best = next((v for v in r.venues if v.url and v.grade != "C"), None)
        if best:
            r.url = best.url


def venue_table(results, tiers: dict, history: dict[str, list[str]]) -> list[dict]:
    """Rows for the report: every venue seen this run, with grade and track record."""
    seen: dict[str, Venue] = {}
    for res in results:
        for v in res.release.venues:
            seen.setdefault(v.name, v)
    rows = []
    for name, v in seen.items():
        grade, note = grade_venue(v, tiers, history)
        levels = history.get(name, [])
        rows.append({"name": name, "grade": grade, "note": note, "seen": len(levels),
                     "share": premium_share(levels) if levels else None})
    rows.sort(key=lambda r: (-LETTERS.index(r["grade"]), -(r["share"] or 0), r["name"]))
    return rows
