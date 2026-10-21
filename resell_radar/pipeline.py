from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from resell_radar.config import Settings
from resell_radar.models import Release, Score, fmt_kst
from resell_radar.notify import format_message
from resell_radar.scoring import MAYBE, PASS, RECOMMEND, ProfitSettings, score_release
from resell_radar.sources import manual, nike
from resell_radar.storage import Store

VERDICT_ORDER = {RECOMMEND: 0, MAYBE: 1, PASS: 2}


@dataclass
class Result:
    release: Release
    score: Score


def collect(settings: Settings, home: Path) -> tuple[list[Release], list[str]]:
    """Gather releases from every enabled source. A failing source is reported, not fatal."""
    releases: list[Release] = []
    errors: list[str] = []
    if settings.sources.get("nike", True):
        try:
            releases.extend(nike.fetch())
        except Exception as exc:  # network / format change on Nike's side
            errors.append(f"나이키 SNKRS 수집 실패: {exc}")
    if settings.sources.get("manual", True):
        try:
            releases.extend(manual.load(home / "releases.csv"))
        except Exception as exc:
            errors.append(f"releases.csv 읽기 실패: {exc}")
    return releases, errors


def is_upcoming(release: Release, now: datetime) -> bool:
    last = release.entry_end or release.release_at
    return last is None or last >= now - timedelta(hours=1)


def evaluate(
    releases: list[Release], tiers: dict, settings: Settings, store: Store, estimator=None
) -> list[Result]:
    profit = ProfitSettings(
        min_profit=settings.min_profit,
        min_profit_ratio=settings.min_profit_ratio,
        sell_fee_rate=settings.sell_fee_rate,
        shipping_cost=settings.shipping_cost,
    )
    now = datetime.now(timezone.utc)
    results = []
    seen = set()
    for release in releases:
        if release.key in seen or not is_upcoming(release, now):
            continue
        seen.add(release.key)
        first = score_release(release, tiers, profit, estimator=estimator)
        counts = store.recent_collab_counts(release, first.collaborators)
        if any(counts.values()):
            score = score_release(release, tiers, profit, counts, estimator=estimator)
        else:
            score = first
        store.save(release, score)
        results.append(Result(release, score))
    results.sort(key=lambda r: (VERDICT_ORDER[r.score.verdict], -(r.score.est_profit or 0)))
    return results


def notify(results: list[Result], settings: Settings, store: Store, send) -> int:
    """Send each alert once: when an item first qualifies, and again near its entry deadline."""
    wanted = {RECOMMEND, MAYBE} if settings.notify_maybe else {RECOMMEND}
    now = datetime.now(timezone.utc)
    sent = 0
    for r in results:
        if r.score.verdict not in wanted:
            continue
        kinds = ["new"]
        end = r.release.entry_end
        if end and now <= end <= now + timedelta(hours=settings.remind_hours_before_end):
            kinds.append("deadline")
        for kind in kinds:
            if store.was_notified(r.release.key, kind):
                continue
            send(format_message(r.release, r.score, kind))
            store.mark_notified(r.release.key, kind)
            sent += 1
    return sent


def write_report(results: list[Result], path: Path) -> None:
    # utf-8-sig so Excel on Windows shows the Korean columns correctly.
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["판정", "등급", "상품명", "정가", "예상시세", "예상순수익", "응모시작", "응모마감", "근거", "링크"])
        for r in results:
            s, rel = r.score, r.release
            writer.writerow([
                s.verdict,
                s.level,
                rel.name,
                rel.retail_price or "",
                f"{s.est_price} ({s.price_basis})" if s.est_price else "",
                s.est_profit if s.est_profit is not None else "",
                fmt_kst(rel.entry_start),
                fmt_kst(rel.entry_end),
                " / ".join(s.reasons),
                rel.url,
            ])
