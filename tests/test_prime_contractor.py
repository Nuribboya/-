"""원청 후보 탐색 파이프라인 테스트."""
from __future__ import annotations

import json

import pytest

from prime_contractor.config import ScreenConfig, load_config
from prime_contractor.geo import distance_from_home, extract_region, haversine_km
from prime_contractor.industry import judge_overlap, match_sector, normalize_name
from prime_contractor.models import Award, Candidate
from prime_contractor.pipeline import build_candidates, enrich, run_screen
from prime_contractor.report import render_table, write_csv
from prime_contractor.scoring import score_candidate, split_by_overlap
from prime_contractor.sources import g2b


# --- 지역/거리 ----------------------------------------------------------------

def test_extract_region_handles_province_prefix():
    assert extract_region("경기도 평택시 청북읍 1-2") == "평택"
    assert extract_region("충청남도 천안시 서북구 직산읍") == "천안"
    assert extract_region("주소 없음") == ""


def test_gwangju_disambiguated_by_province():
    """'광주'는 경기 광주시와 광주광역시가 겹친다. 광역시 표기를 우선한다."""
    _, gyeonggi = distance_from_home("경기도 광주시 오포읍")
    _, metro = distance_from_home("광주광역시 북구")
    assert gyeonggi < 60 < metro


def test_distance_from_anseong_is_zero():
    region, dist = distance_from_home("경기도 안성시 공도읍")
    assert region == "안성" and dist == 0.0


def test_haversine_known_distance():
    seoul, busan = (37.5665, 126.9780), (35.1796, 129.0756)
    assert 300 < haversine_km(seoul, busan) < 340


# --- 상호 정규화 --------------------------------------------------------------

@pytest.mark.parametrize("raw", ["㈜케이씨텍", "(주)케이씨텍", "주식회사 케이씨텍", "케이씨 텍"])
def test_normalize_name_strips_corporate_noise(raw):
    assert normalize_name(raw) == "케이씨텍"


# --- 업종 겹침 판정 ------------------------------------------------------------

def _cand(name, **kw):
    return Candidate(name=name, **kw)


def test_kc_affiliate_detected_by_name_prefix():
    cfg = ScreenConfig()
    verdict = judge_overlap(_cand("(주)케이씨이앤씨"), cfg.incumbent)
    assert verdict.level == "affiliate"


def test_same_industry_detected_by_ksic_code():
    cfg = ScreenConfig()
    verdict = judge_overlap(_cand("무명전자", ksic_code="26110"), cfg.incumbent)
    assert verdict.level == "same_industry"
    assert "26110" in verdict.reasons[0]


def test_same_industry_detected_by_award_title_keyword():
    """업종코드가 없어도 수주 공고명에 반도체가 있으면 겹친다고 본다."""
    cand = _cand("한빛설비", awards=[Award(title="반도체 FAB 클린룸 제어반 공사")])
    verdict = judge_overlap(cand, ScreenConfig().incumbent)
    assert verdict.level == "same_industry"


def test_water_treatment_company_is_clear():
    cand = _cand("한빛수처리", awards=[Award(title="정수장 자동제어설비 설치공사")])
    assert judge_overlap(cand, ScreenConfig().incumbent).level == "clear"


def test_adjacent_keyword_is_not_same_industry():
    cand = _cand("태양광에너지(주)", awards=[Award(title="태양광 발전 연계 제어반")])
    assert judge_overlap(cand, ScreenConfig().incumbent).level == "adjacent"


def test_kc_prefix_does_not_match_unrelated_name():
    """'KC' 가 상호 중간에 있는 업체까지 계열사로 오인하면 안 된다."""
    assert judge_overlap(_cand("대한KC엔지니어링"), ScreenConfig().incumbent).level != "affiliate"


# --- 타깃 업종 매칭 ------------------------------------------------------------

def test_match_sector_prefers_stronger_evidence():
    cfg = ScreenConfig()
    cand = _cand("대성환경플랜트", ksic_code="38220",
                 awards=[Award(title="폐수처리 감시제어 시스템")])
    name, weight, why = match_sector(cand, cfg.sectors)
    assert name in ("환경·폐기물·소각", "상하수도·수처리")
    assert weight > 0 and why


def test_match_sector_returns_blank_when_nothing_matches():
    name, weight, _ = match_sector(_cand("무연고상사"), ScreenConfig().sectors)
    assert name == "" and weight == 0.0


# --- 후보 집계 ----------------------------------------------------------------

def test_candidates_grouped_by_bizno_not_name_spelling():
    awards = [
        Award(notice_no="1", winner_name="(주)한빛", winner_bizno="111", amount=100, demand_org="A시청"),
        Award(notice_no="2", winner_name="주식회사 한빛", winner_bizno="111", amount=200, demand_org="A시청"),
    ]
    cands = build_candidates(awards, include_demand_orgs=False)
    assert len(cands) == 1
    assert cands[0].award_count == 2 and cands[0].award_amount == 300


def test_demand_orgs_become_candidates_but_schools_are_skipped():
    awards = [
        Award(notice_no="1", winner_name="갑전기", demand_org="평택시 상하수도사업소"),
        Award(notice_no="2", winner_name="을전기", demand_org="경기도교육청"),
    ]
    names = {c.name for c in build_candidates(awards)}
    assert "평택시 상하수도사업소" in names
    assert "경기도교육청" not in names


# --- 점수/필터 ----------------------------------------------------------------

def test_closer_company_outranks_distant_twin():
    cfg = ScreenConfig()
    near = _cand("가까운수처리", address="경기도 평택시", awards=[Award(title="정수장 자동제어", amount=10**9)])
    far = _cand("먼수처리", address="부산광역시", awards=[Award(title="정수장 자동제어", amount=10**9)])
    for c in (near, far):
        enrich([c])
        score_candidate(c, cfg)
    assert near.score > far.score


def test_overlapping_candidates_are_excluded_not_just_penalized():
    cfg = ScreenConfig()
    kc = _cand("케이씨텍", awards=[Award(title="제어반", amount=10**10)])
    ok = _cand("한빛수처리", awards=[Award(title="정수장 자동제어", amount=10**8)])
    for c in (kc, ok):
        score_candidate(c, cfg)
    passed, excluded = split_by_overlap([kc, ok], cfg)
    assert [c.name for c in passed] == ["한빛수처리"]
    assert [c.name for c in excluded] == ["케이씨텍"]


def test_strict_mode_also_drops_adjacent_industries():
    cand = _cand("태양광에너지", awards=[Award(title="태양광 제어반")])
    score_candidate(cand, ScreenConfig())
    _, excluded = split_by_overlap([cand], ScreenConfig(max_overlap_rank=0))
    assert excluded and excluded[0].overlap.level == "adjacent"


def test_min_awards_filters_one_off_contractors():
    cfg = ScreenConfig(min_awards=2)
    cand = _cand("한번만든업체", awards=[Award(title="정수장 자동제어")])
    score_candidate(cand, cfg)
    passed, excluded = split_by_overlap([cand], cfg)
    assert not passed and "미달" in excluded[0].overlap.reasons[-1]


# --- 설정 ---------------------------------------------------------------------

def test_config_json_overrides_only_given_keys(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({
        "incumbent": {"name": "다른그룹", "affiliate_prefixes": ["대한"], "core_keywords": ["조선"]},
        "max_distance_km": 80,
    }, ensure_ascii=False), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.incumbent.name == "다른그룹"
    assert cfg.max_distance_km == 80
    assert cfg.keywords  # 건드리지 않은 값은 기본값 유지
    assert judge_overlap(_cand("대한중공업"), cfg.incumbent).level == "affiliate"
    assert judge_overlap(_cand("케이씨텍"), cfg.incumbent).level == "clear"


# --- 나라장터 응답 파싱 --------------------------------------------------------

def test_parse_json_envelope():
    page = g2b._parse(json.dumps({"response": {
        "header": {"resultCode": "00", "resultMsg": "OK"},
        "body": {"totalCount": 1, "items": [{"bidwinnrNm": "갑사", "sucsfbidAmt": "1,000"}]},
    }}))
    assert page.ok and page.total_count == 1 and page.items[0]["bidwinnrNm"] == "갑사"


def test_parse_xml_envelope_and_error_code():
    page = g2b._parse(
        "<response><header><resultCode>30</resultCode>"
        "<resultMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</resultMsg></header>"
        "<body><totalCount>0</totalCount><items/></body></response>"
    )
    assert not page.ok and page.result_code == "30"


def test_parse_single_item_dict_form():
    """1건만 오면 items 가 리스트가 아니라 dict 로 내려오는 경우가 있다."""
    page = g2b._parse(json.dumps({"response": {
        "header": {"resultCode": "00"},
        "body": {"totalCount": 1, "items": {"item": {"bidwinnrNm": "을사"}}},
    }}))
    assert page.items == [{"bidwinnrNm": "을사"}]


def test_award_amount_strips_commas():
    award = g2b._to_award({"bidwinnrNm": "병사", "sucsfbidAmt": "1,234,000"}, "cnstwk", "자동제어")
    assert award.amount == 1_234_000 and award.category == "공사"


def test_encoded_service_key_is_unquoted_once():
    assert g2b._decode_key("abc%2Bdef%3D%3D") == "abc+def=="
    assert g2b._decode_key("abc+def==") == "abc+def=="


# --- 파이프라인 전체 -----------------------------------------------------------

def test_offline_screen_ranks_and_excludes(tmp_path):
    result = run_screen(ScreenConfig(), offline=True)
    assert result.passed and result.awards
    excluded_names = {c.name for c in result.excluded}
    assert any("케이씨" in n for n in excluded_names)
    assert all("케이씨" not in c.name for c in result.passed)
    # 점수 내림차순 정렬
    assert result.passed == sorted(result.passed, key=lambda c: c.score, reverse=True)

    table = render_table(result)
    assert "원청 후보 스크리닝 결과" in table

    csv_path = write_csv(result, tmp_path / "out.csv", include_excluded=True)
    body = csv_path.read_text(encoding="utf-8-sig")
    assert "업체명" in body and "제외" in body


def test_enrich_fills_region_from_org_name_without_address():
    cands = [Candidate(name="평택시 상하수도사업소", kind="demand_org")]
    enrich(cands)
    assert cands[0].region == "평택" and cands[0].distance_km is not None


# --- API 경로 탐색 / 키워드 미지원 대응 ------------------------------------------

class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


class FakeSession:
    """요청 파라미터를 보고 정해진 응답을 돌려주는 가짜 세션."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):
        params = params or {}
        self.calls.append((url, params))
        return FakeResponse(self.handler(url, params))


def _envelope(items: list[dict], total: int | None = None) -> str:
    return json.dumps({"response": {
        "header": {"resultCode": "00", "resultMsg": "OK"},
        "body": {"totalCount": len(items) if total is None else total, "items": items},
    }})


def _client(handler) -> g2b.G2BClient:
    client = g2b.G2BClient("dummy-key", sleep_sec=0, session=FakeSession(handler))
    return client


def test_resolve_prefers_variant_that_actually_returns_rows():
    """응답코드만 정상이고 0건인 경로를 덥석 잡으면 수집이 통째로 빈다."""
    def handler(url, params):
        if "inqryBgnDate" in params:                       # 두 번째 날짜 방식만 건수가 있다
            return _envelope([{"bidwinnrNm": "갑사"}])
        return _envelope([], total=0)

    client = _client(handler)
    from datetime import datetime, timedelta
    end = datetime(2026, 9, 1)
    url, style = client._resolve("scsbid:cnstwk", g2b.SCSBID_BASES,
                                 "getOpengResultListInfoCnstwkPPSSrch", end - timedelta(days=7), end)
    assert style[0] == "inqryBgnDate"


def test_resolve_falls_back_to_zero_row_variant_rather_than_failing():
    """전 구간이 진짜 0건일 수도 있다. 그때는 에러 대신 경로를 채택하고 진행한다."""
    client = _client(lambda url, params: _envelope([], total=0))
    from datetime import datetime, timedelta
    end = datetime(2026, 9, 1)
    url, style = client._resolve("scsbid:servc", g2b.SCSBID_BASES,
                                 "getOpengResultListInfoServcPPSSrch", end - timedelta(days=7), end)
    assert url.startswith("http://apis.data.go.kr/1230000/")


def test_supports_keyword_detects_silent_zero_result():
    """명세에 없는 파라미터는 에러가 아니라 0건으로 조용히 돌아온다."""
    def handler(url, params):
        if "bidNtceNm" in params:
            return _envelope([], total=0)
        return _envelope([{"bidwinnrNm": "갑사"}], total=500)

    client = _client(handler)
    from datetime import datetime, timedelta
    end = datetime(2026, 9, 1)
    assert client.supports_keyword(
        "http://x/op", g2b.DATE_STYLES[0], end - timedelta(days=7), end, "자동제어") is False


def test_fetch_awards_filters_titles_locally_when_keyword_unsupported():
    rows = [
        {"bidNtceNo": "1", "bidNtceNm": "정수장 자동제어설비 공사", "bidwinnrNm": "갑전기", "sucsfbidAmt": "100"},
        {"bidNtceNo": "2", "bidNtceNm": "청사 화단 조경공사", "bidwinnrNm": "을조경", "sucsfbidAmt": "200"},
        {"bidNtceNo": "3", "bidNtceNm": "배전반 교체", "bidwinnrNm": "병전기", "sucsfbidAmt": "300"},
    ]

    def handler(url, params):
        if "bidNtceNm" in params:
            return _envelope([], total=0)        # 키워드 검색 미지원
        if params.get("numOfRows") == "1":
            return _envelope(rows[:1], total=len(rows))
        return _envelope(rows, total=len(rows))

    client = _client(handler)
    from datetime import datetime
    awards = client.fetch_awards(keywords=("자동제어", "배전반"), categories=("cnstwk",),
                                 lookback_days=10, end=datetime(2026, 9, 1))
    names = {a.winner_name for a in awards}
    assert names == {"갑전기", "병전기"}          # 조경공사는 걸러진다
    assert "cnstwk" in client.keyword_fallback


def test_fetch_awards_uses_server_side_search_when_supported():
    def handler(url, params):
        if params.get("bidNtceNm") == "자동제어":
            return _envelope([{"bidNtceNo": "1", "bidNtceNm": "자동제어 공사",
                               "bidwinnrNm": "갑전기", "sucsfbidAmt": "100"}], total=1)
        return _envelope([{"bidNtceNo": "9", "bidNtceNm": "아무거나",
                           "bidwinnrNm": "무관사", "sucsfbidAmt": "1"}], total=1)

    client = _client(handler)
    from datetime import datetime
    awards = client.fetch_awards(keywords=("자동제어",), categories=("cnstwk",),
                                 lookback_days=10, end=datetime(2026, 9, 1))
    assert not client.keyword_fallback
    assert {a.winner_name for a in awards} == {"갑전기"}


def test_empty_result_table_explains_what_to_check():
    from prime_contractor.pipeline import ScreenResult
    table = render_table(ScreenResult())
    assert "probe" in table and "승인" in table
