from fastapi.testclient import TestClient

from dashboard.main import app
from leadradar.models import Candidate, ScoredCandidate

client = TestClient(app)

_SAMPLE_CONFIG_YAML = """\
own_company:
  name: "우리회사"
  business_description: "제어반 제작"
excluded_client:
  name: "케이씨그룹"
  business_description: "가스정화기"
"""


def test_home_page_links_to_both_apps():
    r = client.get("/")
    assert r.status_code == 200
    assert "/leads/" in r.text
    assert "/inventory/" in r.text


def test_leadradar_mounted_and_reachable():
    r = client.get("/leads/", follow_redirects=True)
    assert r.status_code == 200
    assert "원청 레이더" in r.text


def test_inventory_mounted_and_reachable():
    r = client.get("/inventory/", follow_redirects=True)
    assert r.status_code == 200
    assert "재고 관리" in r.text


def test_leadradar_run_redirects_within_mount(tmp_path, monkeypatch):
    import leadradar.webapp.main as leadradar_main

    config_path = tmp_path / "config.yaml"
    config_path.write_text(_SAMPLE_CONFIG_YAML, encoding="utf-8")
    monkeypatch.setenv("LEADRADAR_CONFIG", str(config_path))
    monkeypatch.setenv("LEADRADAR_RESULTS_DB", str(tmp_path / "results.db"))
    monkeypatch.setenv("DART_API_KEY", "dummy")

    fake_candidate = Candidate(name="테스트회사", business_description="설명")
    monkeypatch.setattr(leadradar_main, "discover_candidates", lambda *a, **k: [fake_candidate])
    monkeypatch.setattr(
        leadradar_main,
        "run_scoring",
        lambda config, candidates: [
            ScoredCandidate(
                candidate=fake_candidate,
                fit_score=77,
                conflicts_with_excluded_client=False,
                reasoning="적합함",
            )
        ],
    )

    r = client.post("/leads/run", data={"keywords": "반도체"}, follow_redirects=True)

    assert r.status_code == 200
    assert r.request.url.path == "/leads/"
    assert "테스트회사" in r.text


def test_inventory_movement_redirects_within_mount(tmp_path, monkeypatch):
    monkeypatch.setenv("INVENTORY_DB", str(tmp_path / "inventory.db"))

    r = client.post(
        "/inventory/movements",
        data={"item_name": "볼트 M8", "direction": "in", "quantity": "10", "unit": "개"},
        follow_redirects=True,
    )

    assert r.status_code == 200
    assert r.request.url.path == "/inventory/"
    assert "볼트 M8" in r.text
