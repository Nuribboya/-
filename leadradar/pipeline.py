"""후보 수집(JSON) -> LLM 채점 -> 정렬 -> CSV 출력까지 한번에 돌리는 파이프라인."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .config import LeadRadarConfig
from .models import Candidate, ScoredCandidate
from .scoring import build_client, score_candidate


def load_candidates_from_json(path: str | Path) -> list[Candidate]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Candidate(**item) for item in data]


def run(config: LeadRadarConfig, candidates: list[Candidate], show_progress: bool = False) -> list[ScoredCandidate]:
    client = build_client()
    total = len(candidates)
    results = []
    for i, candidate in enumerate(candidates, start=1):
        if show_progress:
            print(f"[{i}/{total}] 채점 중: {candidate.name}", flush=True)
        results.append(
            score_candidate(
                client,
                config.own_company,
                config.excluded_client,
                candidate,
                model=config.anthropic_model,
            )
        )
    results.sort(key=lambda r: r.fit_score, reverse=True)
    return results


def write_results_csv(results: list[ScoredCandidate], path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["회사명", "적합도점수", "관계사충돌", "이유", "거리(km)", "매출성장률(%)"])
        for r in results:
            writer.writerow(
                [
                    r.candidate.name,
                    r.fit_score,
                    "Y" if r.conflicts_with_excluded_client else "N",
                    r.reasoning,
                    r.candidate.distance_km,
                    r.candidate.revenue_growth_pct,
                ]
            )
