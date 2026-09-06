from fastapi.testclient import TestClient

import leadradar.webapp.main as webapp_main
from leadradar.models import Candidate, ScoredCandidate

client = TestClient(webapp_main.app)

_SAMPLE_CONFIG_YAML = """\
own_company:
  name: "우리회사"
  business_description: "제어반 제작"
excluded_client:
  name: "케이씨그룹"
  business_description: "가스정화기"
"""


def _setup_env(tmp_path, monkeypatch, dart_key: str = "dummy"):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_SAMPLE_CONFIG_YAML, encoding="utf-8")
    monkeypatch.setenv("LEADRADAR_CONFIG", str(config_path))
    monkeypatch.setenv("LEADRADAR_RESULTS_DB", str(tmp_path / "results.db"))
    if dart_key is not None:
        monkeypatch.setenv("DART_API_KEY", dart_key)
    else:
        monkeypatch.delenv("DART_API_KEY", raising=False)


def test_index_page_loads(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    r = client.get("/")
    assert r.status_code == 200
    assert "원청 레이더" in r.text


def test_run_persists_results_and_shows_them_on_index(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    fake_candidate = Candidate(name="테스트회사", business_description="설명")
    monkeypatch.setattr(webapp_main, "discover_candidates", lambda *a, **k: [fake_candidate])
    monkeypatch.setattr(
        webapp_main,
        "run_scoring",
        lambda config, candidates: [
            ScoredCandidate(
                candidate=fake_candidate,
                fit_score=88,
                conflicts_with_excluded_client=False,
                reasoning="적합함",
            )
        ],
    )

    r = client.post("/run", data={"keywords": "반도체", "origin": "경기도 안성시"}, follow_redirects=True)

    assert r.status_code == 200
    assert "테스트회사" in r.text
    assert "88" in r.text
    assert "적합함" in r.text
    assert "미접촉" in r.text


def test_run_redirects_with_error_when_dart_key_missing(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch, dart_key=None)

    r = client.post("/run", data={"keywords": "반도체"}, follow_redirects=True)

    assert r.status_code == 200
    assert "에러" in r.text


def test_update_status_persists(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)

    fake_candidate = Candidate(name="테스트회사", business_description="설명")
    monkeypatch.setattr(webapp_main, "discover_candidates", lambda *a, **k: [fake_candidate])
    monkeypatch.setattr(
        webapp_main,
        "run_scoring",
        lambda config, candidates: [
            ScoredCandidate(
                candidate=fake_candidate,
                fit_score=88,
                conflicts_with_excluded_client=False,
                reasoning="적합함",
            )
        ],
    )
    client.post("/run", data={"keywords": "반도체"})

    r = client.post(
        "/candidates/status",
        data={"name": "테스트회사", "status": "성사"},
        follow_redirects=True,
    )

    assert r.status_code == 200
    assert "성사" in r.text
