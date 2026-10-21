from __future__ import annotations

import json
import re
from pathlib import Path

# Starting-point collaborator grades. This is a judgement call built from
# 2024-2026 resale data (StockX/Complex reports) plus long-run reputation —
# it is meant to be edited in tiers.json and, later, recalibrated from
# recorded prices rather than trusted as fact.
#   S: premium almost every time   A: usually profitable
#   B: depends on the details      C: usually trades near/below retail
DEFAULT_TIERS: dict = {
    "collaborators": {
        "Fragment": {"grade": "S", "aliases": ["fragment", "프라그먼트", "후지와라"]},
        "Union LA": {"grade": "S", "aliases": ["union", "유니온"]},
        "Tom Sachs": {"grade": "S", "aliases": ["tom sachs", "톰 삭스", "톰삭스", "mars yard"]},
        "Cactus Plant Flea Market": {
            "grade": "S",
            "aliases": ["cactus plant flea market", "cpfm", "캑터스 플랜트", "칵투스 플랜트"],
        },
        "A Ma Maniére": {"grade": "S", "aliases": ["a ma maniere", "a ma maniére", "아마마니에르", "아 마 마니에르"]},
        "Supreme": {"grade": "A", "aliases": ["supreme", "슈프림"]},
        "Awake NY": {"grade": "A", "aliases": ["awake ny", "awake", "어웨이크"]},
        "Bad Bunny": {"grade": "A", "aliases": ["bad bunny", "배드 버니", "배드버니"]},
        "Satoshi Nakamoto": {"grade": "A", "aliases": ["satoshi nakamoto", "사토시 나카모토"]},
        "JJJJound": {"grade": "A", "aliases": ["jjjjound", "자운드"]},
        "PEACEMINUSONE": {"grade": "A", "aliases": ["peaceminusone", "g-dragon", "피스마이너스원", "지드래곤", "권도"]},
        "Concepts": {"grade": "A", "aliases": ["concepts", "컨셉츠"]},
        "Travis Scott": {"grade": "B", "aliases": ["travis scott", "cactus jack", "트래비스 스캇", "트래비스 스콧", "트래비스"]},
        "Kith": {"grade": "B", "aliases": ["kith", "키스"]},
        "Aimé Leon Dore": {"grade": "B", "aliases": ["aime leon dore", "aimé leon dore", "ald", "에메 레온 도르"]},
        "Stüssy": {"grade": "B", "aliases": ["stussy", "stüssy", "스투시"]},
        "sacai": {"grade": "B", "aliases": ["sacai", "사카이"]},
        "Yeezy": {"grade": "C", "aliases": ["yeezy", "이지 부스트", "이지부스트"]},
        "Gucci": {"grade": "C", "aliases": ["gucci", "구찌"]},
        "Prada": {"grade": "C", "aliases": ["prada", "프라다"]},
        "Uniqlo": {"grade": "C", "aliases": ["uniqlo", "유니클로"]},
    },
    # Silhouettes whose demand reliably carries a collab (or a limited
    # general release) one grade higher.
    "hot_silhouettes": {
        "Air Jordan 1": ["jordan 1", "조던 1", "aj1"],
        "SB Dunk": ["sb dunk", "dunk sb", "sb 덩크", "덩크 sb"],
        "Kobe": ["kobe", "코비"],
    },
    "rules": {
        "repeat_collab_count": 3,  # this many releases with the same partner in a year -> -1 grade
        "many_retailers": 5,  # released through this many shops or more -> -1 grade
    },
    # Expected resale / retail ratio per grade, only used when no real market
    # price is known for the item.
    "price_multiplier": {"S": 1.8, "A": 1.4, "B": 1.15, "C": 1.0},
}

GRADES = ["C", "B", "A", "S"]


def load_tiers(path: Path) -> dict:
    """Load tiers.json, writing the defaults out on first run so the user can edit them."""
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_TIERS, ensure_ascii=False, indent=2), encoding="utf-8")
        return DEFAULT_TIERS
    return json.loads(path.read_text(encoding="utf-8"))


def _alias_pattern(alias: str) -> re.Pattern:
    alias = alias.lower()
    # ASCII aliases need word boundaries ("ald" must not match "baldwin");
    # Korean ones are matched as substrings, but "조던 1" must still not match "조던 11".
    if alias.isascii():
        return re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])")
    return re.compile(re.escape(alias) + r"(?![0-9])")


def find_collaborators(title: str, tiers: dict) -> list[tuple[str, str]]:
    """Return [(collaborator, grade)] mentioned in a product title."""
    text = title.lower()
    found = []
    for name, info in tiers["collaborators"].items():
        if any(_alias_pattern(a).search(text) for a in info["aliases"]):
            found.append((name, info["grade"]))
    return found


def find_hot_silhouette(title: str, tiers: dict) -> str | None:
    text = title.lower()
    for name, aliases in tiers["hot_silhouettes"].items():
        if any(_alias_pattern(a).search(text) for a in aliases):
            return name
    return None
