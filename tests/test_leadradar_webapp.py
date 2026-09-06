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


def test_index_page():
    r = client.get("/")
    assert r.status_code == 200
    assert "원청 레이더" in r.text


def test_run_renders_results(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_SAMPLE_CONFIG_YAML, encoding="utf-8")
    monkeypatch.setenv("LEADRADAR_CONFIG", str(config_path))
    monkeypatch.setenv("DART_API_KEY", "dummy")

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

    r = client.post("/run", data={"keywords": "반도체", "origin": "경기도 안성시"})

    assert r.status_code == 200
    assert "테스트회사" in r.text
    assert "88" in r.text
    assert "적합함" in r.text


def test_run_shows_error_when_dart_key_missing(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_SAMPLE_CONFIG_YAML, encoding="utf-8")
    monkeypatch.setenv("LEADRADAR_CONFIG", str(config_path))
    monkeypatch.delenv("DART_API_KEY", raising=False)

    r = client.post("/run", data={"keywords": "반도체"})

    assert r.status_code == 200
    assert "에러" in r.text
