import json

import pytest

from leadradar import discovery
from leadradar.sources.dart import CorpCode, parse_revenue_growth_pct


def test_parse_revenue_growth_pct():
    data = {
        "list": [
            {"account_nm": "매출액", "sj_div": "IS", "thstrm_amount": "1,100", "frmtrm_amount": "1,000"},
        ]
    }
    assert parse_revenue_growth_pct(data) == 10.0


def test_parse_revenue_growth_pct_missing_account_returns_none():
    assert parse_revenue_growth_pct({"list": []}) is None


def test_parse_revenue_growth_pct_zero_previous_returns_none():
    data = {
        "list": [
            {"account_nm": "매출액", "sj_div": "IS", "thstrm_amount": "1,000", "frmtrm_amount": "0"},
        ]
    }
    assert parse_revenue_growth_pct(data) is None


def test_load_or_fetch_corp_codes_uses_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps([{"corp_code": "001", "corp_name": "테스트회사", "stock_code": "123456"}]),
        encoding="utf-8",
    )

    def _fail_fetch(api_key):
        raise AssertionError("캐시가 있으면 fetch_corp_codes를 다시 호출하면 안 된다")

    monkeypatch.setattr(discovery, "fetch_corp_codes", _fail_fetch)

    result = discovery.load_or_fetch_corp_codes("dummy-key", cache_path)

    assert result == [CorpCode(corp_code="001", corp_name="테스트회사", stock_code="123456")]


def test_discover_candidates_includes_unlisted_companies(monkeypatch):
    corp_codes = [
        CorpCode(corp_code="001", corp_name="반도체상장사", stock_code="123456"),
        CorpCode(corp_code="002", corp_name="반도체비상장사", stock_code=""),
    ]
    monkeypatch.setattr(
        discovery,
        "load_or_fetch_corp_codes",
        lambda api_key, cache_path=discovery.DEFAULT_CORP_CODE_CACHE: corp_codes,
    )
    monkeypatch.setattr(
        discovery,
        "fetch_company_overview",
        lambda api_key, corp_code: {"corp_name": corp_code, "induty_code": "26429"},
    )

    def _fake_financials(api_key, corp_code, year):
        if corp_code == "002":
            raise RuntimeError("비상장사는 사업보고서 미제출로 조회 실패")
        return {"list": [{"account_nm": "매출액", "sj_div": "IS", "thstrm_amount": "1,200", "frmtrm_amount": "1,000"}]}

    monkeypatch.setattr(discovery, "fetch_financial_highlights", _fake_financials)

    candidates = discovery.discover_candidates("dummy-key", keywords=["반도체"])
    candidates_by_name = {c.name: c for c in candidates}

    assert len(candidates) == 2
    assert candidates_by_name["반도체상장사"].revenue_growth_pct == 20.0
    assert candidates_by_name["반도체비상장사"].revenue_growth_pct is None


def test_discover_candidates_computes_distance_when_kakao_key_given(monkeypatch):
    corp_codes = [CorpCode(corp_code="001", corp_name="반도체상장사", stock_code="123456")]
    monkeypatch.setattr(
        discovery,
        "load_or_fetch_corp_codes",
        lambda api_key, cache_path=discovery.DEFAULT_CORP_CODE_CACHE: corp_codes,
    )
    monkeypatch.setattr(
        discovery,
        "fetch_company_overview",
        lambda api_key, corp_code: {
            "corp_name": "반도체상장사",
            "induty_code": "26429",
            "adres": "경기도 평택시",
        },
    )
    monkeypatch.setattr(
        discovery,
        "fetch_financial_highlights",
        lambda api_key, corp_code, year: {"list": []},
    )

    def _fake_geocode(kakao_api_key, address):
        return {"경기도 안성시": (37.0, 127.0), "경기도 평택시": (37.1, 127.0)}[address]

    monkeypatch.setattr(discovery, "geocode_address", _fake_geocode)

    candidates = discovery.discover_candidates(
        "dummy-key",
        keywords=["반도체"],
        kakao_api_key="dummy-kakao-key",
    )

    assert len(candidates) == 1
    assert candidates[0].distance_km == pytest.approx(11.1, abs=0.5)


def test_save_candidates_json_roundtrip(tmp_path):
    from leadradar.models import Candidate
    from leadradar.pipeline import load_candidates_from_json

    candidates = [Candidate(name="A", business_description="설명", revenue_growth_pct=5.0, source="dart")]
    out_path = tmp_path / "out.json"

    discovery.save_candidates_json(candidates, out_path)
    loaded = load_candidates_from_json(out_path)

    assert loaded == candidates
