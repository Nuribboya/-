from __future__ import annotations

import re
import statistics

from resell_radar.models import Release

# Starting USD -> KRW retail conversion before any real pairs are known.
# Brands price Korea on their own tables, not at the FX rate, so this is
# replaced by the median of observed KR/US price pairs once there are enough.
DEFAULT_FACTOR = 1150
MIN_PAIRS = 3
MATCH_THRESHOLD = 0.6

# Where a brand's limited pairs are usually entered for in Korea.
CHANNELS = [
    (("jordan", "nike", "조던", "나이키"), "나이키 SNKRS 한국 · 국내 편집숍"),
    (("adidas", "yeezy", "아디다스"), "adidas CONFIRMED 앱 · 국내 편집숍"),
    (("new balance", "뉴발란스"), "뉴발란스 공식몰 · 국내 편집숍"),
    (("asics", "아식스"), "아식스 공식몰 · 국내 편집숍"),
    (("vans", "반스"), "반스 공식몰 · 무신사"),
    (("salomon", "살로몬"), "살로몬 공식몰 · 국내 편집숍"),
    (("puma", "푸마"), "푸마 공식몰 · 무신사"),
    (("converse", "컨버스"), "컨버스 공식몰 · 무신사"),
]
# Partners that often sell their own collabs first on their own site.
OWN_SHOP = ["jjjjound", "supreme", "kith", "union", "awake", "a ma maniere", "concepts", "stussy", "stüssy"]

STOP = {"x", "the", "and", "a", "of", "in", "with", "pack", "collection", "s"}


def tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9가-힣]+", name.lower()) if t not in STOP}


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def guess_channels(name: str) -> str:
    text = name.lower()
    parts = [label for words, label in CHANNELS if any(w in text for w in words)]
    own = [w for w in OWN_SHOP if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", text)]
    if own:
        parts.append(f"{own[0].title()} 공식몰 선발매 가능 (해외 배송 여부 확인)")
    return " · ".join(parts) or "브랜드 공식몰 · 국내 편집숍"


def learned_factor(pairs: list[tuple[str, int, int]]) -> tuple[float, int]:
    """Median KRW-per-USD over known (name, usd, krw) pairs; the default until there are enough."""
    ratios = [krw / usd for _, usd, krw in pairs if usd]
    if len(ratios) < MIN_PAIRS:
        return DEFAULT_FACTOR, len(ratios)
    return statistics.median(ratios), len(ratios)


def _best_match(item: Release, candidates: list[Release]) -> Release | None:
    best, best_sim = None, MATCH_THRESHOLD
    for c in candidates:
        sim = similarity(item.name, c.name)
        if sim >= best_sim:
            best, best_sim = c, sim
    return best


def reconcile(releases: list[Release], store, keep=lambda r: True) -> tuple[list[Release], float, int]:
    """Merge US-calendar items into the Korean schedule.

    - A US item that matches a Korean one (Nike KR feed / releases.csv) is
      dropped; its USD price teaches the conversion factor when the Korean
      price is real, or fills the Korean price when that is missing.
    - Unmatched US items stay (if keep(item) — e.g. only collabs), with
      Korean price/channels estimated. Both feeds often cover the same shoe,
      so near-duplicate US items are collapsed first.
    Returns (releases, factor, pairs_used).
    """
    kr = [r for r in releases if r.source != "us_calendar"]
    us: list[Release] = []
    for item in (r for r in releases if r.source == "us_calendar"):
        dup = _best_match(item, us)
        if dup is None:
            us.append(item)
        elif dup.us_price is None:
            dup.us_price = item.us_price

    matches = []
    for item in us:
        target = _best_match(item, kr)
        if target is None:
            continue
        matches.append((item, target))
        real_kr_price = target.retail_price and "추정" not in target.note
        if item.us_price and real_kr_price:
            store.save_price_pair(target.name, item.us_price, target.retail_price)

    factor, n_pairs = learned_factor(store.price_pairs())
    basis = f"미국 정가 x{factor:,.0f}" + (f" (실제 {n_pairs}건 학습)" if n_pairs >= MIN_PAIRS else " (기본값)")

    matched = {id(item) for item, _ in matches}
    for item, target in matches:
        if not target.retail_price and item.us_price:
            target.retail_price = round(item.us_price * factor, -2)
            target.note = f"정가 추정: ${item.us_price} → {basis}"

    kept = []
    for item in us:
        if id(item) in matched or not keep(item):
            continue
        item.channels = guess_channels(item.name)
        if item.us_price:
            item.retail_price = round(item.us_price * factor, -2)
        item.note = "국내 일정·정가 추정 (미국 발매일 기준, 국내는 당일~하루 차이)" + (
            f" · ${item.us_price} → {basis}" if item.us_price else ""
        )
        kept.append(item)
    return kr + kept, factor, n_pairs


def merge_korean(releases: list[Release], starter_names: set[str] = frozenset()) -> list[Release]:
    """Collapse the same Korean release seen in several sources into the luck-d one.

    luck-d lists every Korean venue, so its record wins. A Nike KR feed item
    with the same product code is dropped (its entry window fills gaps); a
    releases.csv row with a near-identical name is dropped too, after handing
    over a typed-in market price and note. Built-in starter rows simply give way.
    """
    luckd = [r for r in releases if r.source == "luckd"]
    if not luckd:
        return releases
    by_code = {r.style_code.upper(): r for r in luckd if r.style_code}
    out = []
    for r in releases:
        if r.source == "nike" and r.style_code.upper() in by_code:
            target = by_code[r.style_code.upper()]
            target.entry_start = target.entry_start or r.entry_start
            target.entry_end = target.entry_end or r.entry_end
            continue
        if r.source == "manual":
            target = _best_match(r, luckd)
            if target is not None:
                if r.name not in starter_names:
                    target.market_price = target.market_price or r.market_price
                    target.note = target.note or r.note
                continue
        out.append(r)
    return out
