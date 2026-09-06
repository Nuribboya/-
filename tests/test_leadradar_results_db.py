import pytest

from leadradar.models import Candidate, ScoredCandidate
from leadradar.results_db import (
    init_db,
    list_candidates,
    update_contact_status,
    upsert_scored_candidates,
)


def _scored(name: str, fit_score: int, conflicts: bool = False) -> ScoredCandidate:
    return ScoredCandidate(
        candidate=Candidate(name=name, business_description="설명", distance_km=10.0, revenue_growth_pct=5.0),
        fit_score=fit_score,
        conflicts_with_excluded_client=conflicts,
        reasoning="이유",
    )


def test_upsert_then_list_sorted_by_fit_score(tmp_path):
    db_path = tmp_path / "results.db"
    init_db(db_path)

    upsert_scored_candidates(db_path, [_scored("B회사", 40), _scored("A회사", 90)])

    candidates = list_candidates(db_path)
    assert [c.name for c in candidates] == ["A회사", "B회사"]
    assert candidates[0].contact_status == "미접촉"


def test_upsert_preserves_existing_contact_status(tmp_path):
    db_path = tmp_path / "results.db"
    init_db(db_path)

    upsert_scored_candidates(db_path, [_scored("A회사", 50)])
    update_contact_status(db_path, "A회사", "성사")

    upsert_scored_candidates(db_path, [_scored("A회사", 55)])  # 재채점 결과가 다시 들어옴

    candidates = list_candidates(db_path)
    assert candidates[0].fit_score == 55
    assert candidates[0].contact_status == "성사"


def test_update_contact_status_rejects_unknown_status(tmp_path):
    db_path = tmp_path / "results.db"
    init_db(db_path)
    upsert_scored_candidates(db_path, [_scored("A회사", 50)])

    with pytest.raises(ValueError):
        update_contact_status(db_path, "A회사", "알수없는상태")


def test_list_candidates_excludes_below_min_score(tmp_path):
    db_path = tmp_path / "results.db"
    init_db(db_path)
    upsert_scored_candidates(
        db_path,
        [_scored("부적합회사", 5), _scored("애매한회사", 45), _scored("적합회사", 80)],
    )

    candidates = list_candidates(db_path, min_score=30)

    assert [c.name for c in candidates] == ["적합회사", "애매한회사"]


def test_list_candidates_min_score_zero_shows_everything(tmp_path):
    db_path = tmp_path / "results.db"
    init_db(db_path)
    upsert_scored_candidates(db_path, [_scored("부적합회사", 5)])

    candidates = list_candidates(db_path, min_score=0)

    assert len(candidates) == 1
