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
        "Pharrell": {"grade": "A", "aliases": ["pharrell", "퍼렐", "humanrace"]},
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
    # Silhouettes whose demand reliably lifts a collab (or a limited general release).
    "hot_silhouettes": {
        "Air Jordan 1": ["jordan 1", "조던 1", "aj1"],
        "SB Dunk": ["sb dunk", "dunk sb", "sb 덩크", "덩크 sb"],
        "Kobe": ["kobe", "코비"],
    },
    # Clothing resells for smaller premiums than shoes from the same partner
    # (size spread, lower trade volume), so apparel sits two notches lower.
    "apparel_keywords": [
        "hoodie", "hooded", "sweatshirt", "crewneck", "tee", "t-shirt", "shirt", "jacket", "pants",
        "shorts", "jersey", "fleece", "beanie", "cap", "후디", "후드", "스웨트셔츠", "맨투맨", "크루넥",
        "티셔츠", "반팔", "긴팔", "셔츠", "재킷", "자켓", "팬츠", "바지", "반바지", "저지", "플리스", "비니", "모자",
    ],
    "rules": {
        "repeat_collab_count": 3,  # this many releases with the same partner in a year -> penalty
        "many_retailers": 5,  # released through this many shops or more -> penalty
    },
    # Grade moves in notches (C- .. S+); 3 notches = one whole letter grade.
    # Strong signals move a full letter, softer ones two notches.
    "adjust": {
        "multi_collab": 2,
        "hot_silhouette": 2,
        "repeat_collab": -2,
        "restock": -3,
        "many_retailers": -2,
        "fcfs": -2,
        "apparel": -2,
    },
    # Expected resale / retail ratio per notch, only used when no real market
    # price is known. The +/- steps sit a third of the way (geometrically)
    # toward the neighbouring letter.
    "price_multiplier": {
        "C-": 0.97, "C": 1.0, "C+": 1.05,
        "B-": 1.10, "B": 1.15, "B+": 1.23,
        "A-": 1.31, "A": 1.4, "A+": 1.52,
        "S-": 1.66, "S": 1.8, "S+": 1.98,
    },
}

NOTCHES = ["C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+", "S-", "S", "S+"]


def notch(grade: str) -> int:
    return NOTCHES.index(grade)


def load_tiers(path: Path) -> dict:
    """Load tiers.json, writing the defaults out on first run so the user can edit them.

    Files written by older versions (plain S/A/B/C, no "adjust") get the
    missing keys filled in from the defaults rather than failing.
    """
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_TIERS, ensure_ascii=False, indent=2), encoding="utf-8")
        return DEFAULT_TIERS
    tiers = json.loads(path.read_text(encoding="utf-8"))
    for key, value in DEFAULT_TIERS.items():
        tiers.setdefault(key, value)
    # New default partners are added; grades the user already edited are kept.
    for key in ("adjust", "price_multiplier", "rules", "collaborators"):
        for k, v in DEFAULT_TIERS[key].items():
            tiers[key].setdefault(k, v)
    return tiers


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


def is_apparel(title: str, tiers: dict) -> bool:
    text = title.lower()
    return any(_alias_pattern(k).search(text) for k in tiers["apparel_keywords"])


def find_hot_silhouette(title: str, tiers: dict) -> str | None:
    text = title.lower()
    for name, aliases in tiers["hot_silhouettes"].items():
        if any(_alias_pattern(a).search(text) for a in aliases):
            return name
    return None
