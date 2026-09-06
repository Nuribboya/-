import csv

from leadradar.models import Candidate, ScoredCandidate
from leadradar.pipeline import load_candidates_from_json, write_results_csv


def test_load_candidates_from_json():
    candidates = load_candidates_from_json("leadradar/fixtures/sample_candidates.json")

    assert len(candidates) == 4
    assert candidates[0].name == "지투파워"
    assert candidates[0].revenue_growth_pct == 39.6


def test_write_results_csv(tmp_path):
    results = [
        ScoredCandidate(
            candidate=Candidate(name="A회사", business_description="설명", distance_km=10, revenue_growth_pct=5.0),
            fit_score=90,
            conflicts_with_excluded_client=False,
            reasoning="적합함",
        ),
        ScoredCandidate(
            candidate=Candidate(name="B회사", business_description="설명", distance_km=5, revenue_growth_pct=1.0),
            fit_score=40,
            conflicts_with_excluded_client=True,
            reasoning="충돌 우려",
        ),
    ]
    out_path = tmp_path / "out.csv"

    write_results_csv(results, out_path)

    with open(out_path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    assert rows[0] == ["회사명", "적합도점수", "관계사충돌", "이유", "거리(km)", "매출성장률(%)"]
    assert rows[1][0] == "A회사"
    assert rows[2][2] == "Y"
