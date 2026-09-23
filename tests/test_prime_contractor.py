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
    assert "찾기 결과" in table
    assert "A 먼저 연락" in table          # 등급 뜻을 표 아래에 적어 준다

    csv_path = write_csv(result, tmp_path / "out.csv", include_excluded=True)
    body = csv_path.read_text(encoding="utf-8-sig")
    assert "회사 이름" in body and "제외" in body


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
                                 g2b.ops_for("cnstwk"), end - timedelta(days=7), end)
    assert style[0] == "inqryBgnDate"


def test_resolve_falls_back_to_zero_row_variant_rather_than_failing():
    """전 구간이 진짜 0건일 수도 있다. 그때는 에러 대신 경로를 채택하고 진행한다."""
    client = _client(lambda url, params: _envelope([], total=0))
    from datetime import datetime, timedelta
    end = datetime(2026, 9, 1)
    url, style = client._resolve("scsbid:servc", g2b.SCSBID_BASES,
                                 g2b.ops_for("servc"), end - timedelta(days=7), end)
    assert url.startswith("http://apis.data.go.kr/1230000/")


def test_supports_keyword_detects_ignored_parameter():
    """명세에 없는 파라미터는 에러가 아니라 '무시'된다 — 건수가 그대로다."""
    client = _client(lambda url, params: _envelope([{"bidwinnrNm": "갑사"}], total=500))
    from datetime import datetime, timedelta
    end = datetime(2026, 9, 1)
    assert client.supports_keyword(
        "http://x/op", g2b.DATE_STYLES[0], end - timedelta(days=7), end) is False


def test_supports_keyword_true_when_unknown_term_returns_nothing():
    def handler(url, params):
        if params.get("bidNtceNm"):
            return _envelope([], total=0)          # 없는 공고명 → 제대로 0건
        return _envelope([{"bidwinnrNm": "갑사"}], total=500)

    client = _client(handler)
    from datetime import datetime, timedelta
    end = datetime(2026, 9, 1)
    assert client.supports_keyword(
        "http://x/op", g2b.DATE_STYLES[0], end - timedelta(days=7), end) is True


def test_resolve_prefers_operation_that_exposes_winner_name():
    """개찰결과 계열은 낙찰업체명이 안 들어온다. 업체명이 나오는 쪽을 골라야 한다."""
    def handler(url, params):
        if "getScsbidListSttus" in url:
            return _envelope([{"bidNtceNo": "1", "bidwinnrNm": "갑전기"}], total=10)
        return _envelope([{"bidNtceNo": "1", "progrsDivCdNm": "개찰완료"}], total=99)

    client = _client(handler)
    from datetime import datetime, timedelta
    end = datetime(2026, 9, 1)
    url, _ = client._resolve("scsbid:cnstwk", g2b.SCSBID_BASES, g2b.ops_for("cnstwk"),
                             end - timedelta(days=7), end)
    assert "getScsbidListSttus" in url


def test_resolve_settles_for_opening_result_when_nothing_better():
    """업체명이 안 나와도 유일하게 응답하는 경로라면 일단 쓴다 (경고만)."""
    def handler(url, params):
        if "getScsbidListSttus" in url:
            return _envelope([], total=0)
        return _envelope([{"bidNtceNo": "1", "progrsDivCdNm": "개찰완료"}], total=99)

    client = _client(handler)
    from datetime import datetime, timedelta
    end = datetime(2026, 9, 1)
    url, _ = client._resolve("scsbid:servc", g2b.SCSBID_BASES, g2b.ops_for("servc"),
                             end - timedelta(days=7), end)
    assert "getOpengResultListInfo" in url


@pytest.mark.parametrize("raw,expected", [
    ({"bidwinnrNm": "(주)한빛전기", "bidwinnrBizno": "1234567890"}, ("(주)한빛전기", "1234567890")),
    ([{"corpNm": "대성설비"}], ("대성설비", "")),
    ("가나전기^2211133344", ("가나전기", "2211133344")),
    ("(주)한빛전기|1234567890|홍길동", ("(주)한빛전기", "1234567890")),
    ("", ("", "")),
])
def test_openg_corp_info_parsed_from_various_shapes(raw, expected):
    """개찰결과는 낙찰업체를 opengCorpInfo 한 칸에 몰아 넣는다. 형식이 제각각이다."""
    assert g2b._corp_info({"opengCorpInfo": raw} if raw != "" else {}) == expected


def test_award_falls_back_to_corp_info_when_winner_field_absent():
    award = g2b._to_award(
        {"bidNtceNo": "7", "bidNtceNm": "정수장 자동제어", "opengCorpInfo": "가나전기^2211133344"},
        "servc", "자동제어")
    assert award.winner_name == "가나전기" and award.winner_bizno == "2211133344"


def test_fetch_awards_filters_titles_locally_when_keyword_unsupported():
    rows = [
        {"bidNtceNo": "1", "bidNtceNm": "정수장 자동제어설비 공사", "bidwinnrNm": "갑전기"},
        {"bidNtceNo": "2", "bidNtceNm": "청사 화단 조경공사", "bidwinnrNm": "을조경"},
        {"bidNtceNo": "3", "bidNtceNm": "배전반 교체", "bidwinnrNm": "병전기"},
    ]

    def handler(url, params):                      # bidNtceNm 을 통째로 무시한다
        if params.get("numOfRows") == "1":
            return _envelope(rows[:1], total=len(rows))
        return _envelope(rows, total=len(rows))

    client = _client(handler)
    from datetime import datetime
    awards = client.fetch_awards(keywords=("자동제어", "배전반"), categories=("cnstwk",),
                                 lookback_days=10, end=datetime(2026, 9, 1))
    assert {a.winner_name for a in awards} == {"갑전기", "병전기"}   # 조경공사는 걸러진다
    assert "cnstwk" in client.keyword_fallback


def test_fetch_awards_uses_server_side_search_when_supported():
    def handler(url, params):
        kw = params.get("bidNtceNm")
        if kw == "자동제어":
            return _envelope([{"bidNtceNo": "1", "bidNtceNm": "자동제어 공사",
                               "bidwinnrNm": "갑전기"}], total=1)
        if kw:
            return _envelope([], total=0)          # 그 밖의 검색어는 0건
        return _envelope([{"bidNtceNo": "9", "bidNtceNm": "아무거나",
                           "bidwinnrNm": "무관사"}], total=50)

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


# --- 용어를 다듬어도 앱이 깨지지 않아야 한다 -------------------------------------

def test_saved_settings_from_an_older_wording_fall_back_to_defaults():
    """문구를 바꾸면 예전에 저장해 둔 선택지가 목록에 없다. 그때 빈 칸이 되면 안 된다."""
    from prime_contractor.app_settings import (
        DISTANCE_CHOICES, MODES, OVERLAP_CHOICES, build_config)
    cfg = build_config({"distance": "70km 이내",          # 옛 문구
                        "overlap": "KC 계열사만 제외 (반도체 포함)",
                        "mode": "공공 낙찰 (나라장터)"})
    base = ScreenConfig()
    assert cfg.within_km == base.within_km
    assert cfg.max_overlap_rank == base.max_overlap_rank
    # 새 문구는 모두 고를 수 있는 값이어야 한다
    assert all(v in DISTANCE_CHOICES.values() for v in [50.0, 70.0, None])
    assert set(OVERLAP_CHOICES.values()) == {0, 1, 2}
    assert len(MODES) == 3


def test_help_text_covers_what_a_beginner_asks_first():
    from prime_contractor.help_text import HELP_TEXT
    for topic in ("인증키", "data.go.kr", "원청", "발주처", "등급", "매출.csv", "어림짐작"):
        assert topic in HELP_TEXT, topic


def test_grade_advice_is_an_instruction_not_a_label():
    """'우선 접촉' 같은 명사보다 '먼저 연락해 보세요' 가 바로 이해된다."""
    from prime_contractor.fitness import GRADE_ADVICE
    assert all(v.endswith(("요", "다", "때", "니다")) for v in GRADE_ADVICE.values())
    assert GRADE_ADVICE["A"] != GRADE_ADVICE["D"]


def test_variants_rejects_a_bare_string_operation():
    """문자열을 넘기면 한 글자씩 순회해 엉뚱한 URL이 조용히 만들어진다."""
    client = _client(lambda url, params: _envelope([]))
    with pytest.raises(TypeError):
        client._variants(g2b.SCSBID_BASES, "getScsbidListSttusServcPPSSrch")


# --- 업종 분류: 납품처 우선 -----------------------------------------------------

def test_general_contractor_classified_by_what_it_won_not_its_name():
    """'○○종합건설'이 정수장 일을 했으면 업종은 건설이 아니라 수처리다."""
    cfg = ScreenConfig()
    cand = _cand("비씨종합건설 주식회사", ksic_code="41221",
                 awards=[Award(title="고덕정수장 배수지 자동제어설비 설치공사",
                               demand_org="평택시 상하수도사업소")])
    name, _, why = match_sector(cand, cfg.sectors)
    assert name == "상하수도·수처리", why


def test_company_ksic_does_not_outweigh_demand_evidence():
    """업종코드는 '무엇을 만드는지'지 '누구에게 파는지'가 아니다."""
    cfg = ScreenConfig()
    cand = _cand("삼정전기공사(주)", ksic_code="42201",      # 전기공사업 → EPC 코드
                 awards=[Award(title="열병합발전소 수배전반 증설공사",
                               demand_org="한국지역난방공사")])
    name, _, _ = match_sector(cand, cfg.sectors)
    assert name == "발전·에너지"


def test_epc_still_matched_when_nothing_more_specific_fits():
    cfg = ScreenConfig()
    cand = _cand("대제종합건설", ksic_code="41221",
                 awards=[Award(title="사옥 신축공사 전기공사")])
    name, _, _ = match_sector(cand, cfg.sectors)
    assert name == "건설·플랜트 EPC"


# --- 반도체 포함 / 계열사만 제외 -------------------------------------------------

def test_semiconductor_demand_passes_by_default_but_kc_affiliate_does_not():
    """'KC 계열사만 빼고 반도체는 본다'가 기본 동작이다."""
    cfg = ScreenConfig()
    fab = _cand("한양이엔지풍", awards=[Award(title="반도체 FAB 클린룸 전력제어반")])
    kc = _cand("케이씨텍", awards=[Award(title="반도체 세정장비 제어반")])
    for c in (fab, kc):
        score_candidate(c, cfg)
    passed, excluded = split_by_overlap([fab, kc], cfg)
    assert [c.name for c in passed] == ["한양이엔지풍"]
    assert excluded[0].overlap.level == "affiliate"
    assert fab.sector == "반도체·디스플레이"


def test_exclude_same_industry_restores_the_old_behaviour():
    cfg = ScreenConfig(max_overlap_rank=1)
    fab = _cand("한양이엔지풍", awards=[Award(title="반도체 FAB 클린룸 제어반")])
    score_candidate(fab, cfg)
    passed, excluded = split_by_overlap([fab], cfg)
    assert not passed and excluded[0].overlap.level == "same_industry"


# --- 거리 하드 필터 -------------------------------------------------------------

def test_candidates_beyond_within_km_are_dropped():
    cfg = ScreenConfig(within_km=70)
    near = _cand("가까운설비", address="경기도 평택시", awards=[Award(title="정수장 자동제어")])
    far = _cand("먼설비", address="강원도 춘천시", awards=[Award(title="정수장 자동제어")])
    for c in (near, far):
        enrich([c])
        score_candidate(c, cfg)
    passed, excluded = split_by_overlap([near, far], cfg)
    assert [c.name for c in passed] == ["가까운설비"]
    assert "km" in excluded[0].overlap.reasons[-1]


def test_unknown_address_is_kept_rather_than_silently_dropped():
    """거리를 모른다는 게 멀다는 뜻은 아니다."""
    cfg = ScreenConfig(within_km=70)
    cand = _cand("주소미상설비", awards=[Award(title="정수장 자동제어")])
    enrich([cand])
    score_candidate(cand, cfg)
    passed, _ = split_by_overlap([cand], cfg)
    assert passed and passed[0].distance_km is None


def test_switchgear_keywords_are_searched():
    """배전반 확장 - 수변전·특고압 쪽 공고도 검색 대상이어야 한다."""
    cfg = ScreenConfig()
    for kw in ("수배전반", "특고압", "큐비클", "변전실"):
        assert kw in cfg.keywords


# --- 데스크톱 앱 설정 -----------------------------------------------------------

def _label_for(choices: dict, value):
    """표시 문구가 아니라 값으로 선택지를 찾는다 (문구는 언제든 다듬어진다)."""
    return next(k for k, v in choices.items() if v == value)


def test_gui_options_map_to_config():
    from prime_contractor.app_settings import DISTANCE_CHOICES, OVERLAP_CHOICES, build_config
    cfg = build_config({"days": "120",
                        "distance": _label_for(DISTANCE_CHOICES, 50.0),
                        "overlap": _label_for(OVERLAP_CHOICES, 1),
                        "g2b_key": "  abc  ", "include_demand_orgs": False})
    assert cfg.lookback_days == 120
    assert cfg.within_km == 50.0
    assert cfg.max_overlap_rank == 1
    assert cfg.g2b_service_key == "abc"        # 앞뒤 공백은 떼어낸다
    assert cfg.include_demand_orgs is False


def test_gui_blank_or_bad_input_falls_back_to_defaults():
    """화면에서 잘못 고른 것 때문에 탐색이 멈추면 안 된다."""
    from prime_contractor.app_settings import build_config
    base = ScreenConfig()
    cfg = build_config({"days": "", "distance": "말도 안 되는 값", "overlap": None})
    assert cfg.lookback_days == base.lookback_days
    assert cfg.within_km == base.within_km
    assert cfg.max_overlap_rank == base.max_overlap_rank


def test_nationwide_choice_clears_the_distance_limit():
    from prime_contractor.app_settings import DISTANCE_CHOICES, build_config
    label = _label_for(DISTANCE_CHOICES, None)
    assert build_config({"distance": label}).within_km is None


def test_settings_round_trip(tmp_path, monkeypatch):
    from prime_contractor import app_settings
    monkeypatch.setenv("APPDATA", str(tmp_path))
    saved = {"mode": app_settings.MODE_PUBLIC, "days": 90, "g2b_key": "zzz"}
    path = app_settings.save_settings(saved)
    assert path.exists()
    assert app_settings.load_settings() == saved


def test_corrupt_settings_file_does_not_crash(tmp_path, monkeypatch):
    from prime_contractor import app_settings
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = app_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{깨진 파일", encoding="utf-8")
    assert app_settings.load_settings() == {}


def test_sector_dropdown_starts_with_all_option():
    from prime_contractor.app_settings import SECTOR_ALL, sector_names
    names = sector_names(ScreenConfig())
    assert names[0] == SECTOR_ALL
    assert "반도체·디스플레이" in names


def test_filter_sector_moves_others_to_excluded_with_a_reason():
    from prime_contractor.pipeline import ScreenResult, filter_sector
    keep = _cand("반도체설비", awards=[Award(title="반도체 클린룸 제어반")])
    drop = _cand("물설비", awards=[Award(title="정수장 자동제어")])
    cfg = ScreenConfig()
    for c in (keep, drop):
        score_candidate(c, cfg)
    result = ScreenResult(passed=[keep, drop])
    filter_sector(result, "반도체")
    assert [c.name for c in result.passed] == ["반도체설비"]
    assert "다름" in result.excluded[0].overlap.reasons[-1]


# --- 적합도 평가 ----------------------------------------------------------------

def _scored(name, cfg=None, **kw):
    cfg = cfg or ScreenConfig()
    c = _cand(name, **kw)
    score_candidate(c, cfg)
    return c


def test_direct_product_mention_beats_generic_electrical_work():
    """'배전반 교체'는 판넬이 확실히 들어가고, '전기공사'는 들어갈지 모른다."""
    direct = _scored("갑전기", awards=[Award(title="수배전반 교체공사", amount=10**9, category="공사")])
    generic = _scored("을전기", awards=[Award(title="청사 전기공사", amount=10**9, category="공사")])
    assert direct.fitness.axis("product_fit").score > generic.fitness.axis("product_fit").score


def test_panel_amount_estimated_by_category_and_directness():
    from prime_contractor.fitness import estimate_panel_amount
    goods = _cand("갑", awards=[Award(title="배전반 구매", amount=1_000_000_000, category="물품")])
    works = _cand("을", awards=[Award(title="배전반 설치공사", amount=1_000_000_000, category="공사")])
    assert estimate_panel_amount(goods) == 700_000_000      # 물품 direct 0.70
    assert estimate_panel_amount(works) == 250_000_000      # 공사 direct 0.25


def test_unrelated_notice_contributes_no_panel_volume():
    from prime_contractor.fitness import estimate_panel_amount
    cand = _cand("갑", awards=[Award(title="청사 화단 조경공사", amount=10**10, category="공사")])
    assert estimate_panel_amount(cand) == 0


def test_repeat_axis_rewards_multiple_buyers_over_one_big_order():
    spread = _scored("갑전기", awards=[
        Award(title="배전반 교체", amount=3 * 10**8, category="공사", demand_org="A시"),
        Award(title="배전반 증설", amount=3 * 10**8, category="공사", demand_org="B시"),
        Award(title="분전반 설치", amount=3 * 10**8, category="공사", demand_org="C시"),
    ])
    single = _scored("을전기", awards=[
        Award(title="배전반 교체", amount=9 * 10**8, category="공사", demand_org="A시")])
    assert spread.fitness.axis("repeat").score > single.fitness.axis("repeat").score


def test_grades_follow_the_total_score():
    from prime_contractor.fitness import GRADE_CUTS
    assert [g for _, g in GRADE_CUTS] == ["A", "B", "C", "D"]
    strong = _scored("가까운배전반", address="경기도 평택시", awards=[
        Award(title="수배전반 교체공사", amount=2 * 10**9, category="공사", demand_org="A시"),
        Award(title="분전반 증설공사", amount=10**9, category="공사", demand_org="B시"),
    ])
    enrich([strong]); score_candidate(strong, ScreenConfig())
    weak = _scored("먼조경", address="부산광역시",
                   awards=[Award(title="조경 부대 전기공사", amount=2 * 10**7, category="공사")])
    enrich([weak]); score_candidate(weak, ScreenConfig())
    assert strong.grade in ("A", "B")
    assert weak.grade in ("C", "D")
    assert strong.score > weak.score


def test_grade_at_least_ordering():
    from prime_contractor.scoring import grade_at_least
    assert grade_at_least("A", "B") and grade_at_least("B", "B")
    assert not grade_at_least("C", "B")
    assert grade_at_least("?", "B")            # 모르는 등급은 거르지 않는다


def test_min_grade_filters_low_candidates_with_a_reason():
    cfg = ScreenConfig(min_grade="A")
    weak = _cand("약한곳", awards=[Award(title="부대 전기공사", amount=10**7, category="공사")])
    score_candidate(weak, cfg)
    passed, excluded = split_by_overlap([weak], cfg)
    assert not passed
    assert "등급" in excluded[0].overlap.reasons[-1]


def test_weights_are_normalised_to_100():
    """배점을 바꿔도 총점은 100점 만점으로 읽혀야 한다."""
    awards = [Award(title="배전반 교체공사", amount=10**9, category="공사", demand_org="A시")]
    base = _scored("갑", ScreenConfig(), address="경기도 평택시", awards=awards)
    tilted = _scored("갑", ScreenConfig(weights={"product_fit": 60.0, "volume": 10.0,
                                                 "access": 10.0, "repeat": 10.0, "safety": 10.0}),
                     address="경기도 평택시", awards=awards)
    assert 0 <= base.score <= 100 and 0 <= tilted.score <= 100
    assert base.score != tilted.score          # 배점이 실제로 반영된다


def test_candidate_without_awards_scores_on_industry_instead():
    """업종 훑기 모드에는 낙찰 이력이 없다. 그래도 평가는 되어야 한다."""
    cand = _scored("반도체설비(주)", ksic_code="26110")
    axis = cand.fitness.axis("product_fit")
    assert axis.score > 0
    assert "수주 기록이 없" in axis.detail and "업종" in axis.detail


def test_cautions_flag_what_the_user_must_verify():
    cand = _scored("주소없는곳", awards=[Award(title="배전반 교체", amount=10**8, category="공사")])
    joined = " ".join(cand.fitness.cautions)
    assert "위치" in joined            # 주소를 못 찾았다
    assert "업종" in joined            # 공식 업종 정보가 없다
    assert "1건" in joined             # 따낸 공사가 하나뿐이다
    assert len(cand.fitness.cautions) == 3


def test_explain_lists_every_axis():
    cand = _scored("갑전기", address="경기도 평택시",
                   awards=[Award(title="배전반 교체", amount=10**9, category="공사")])
    text = cand.fitness.explain()
    for axis in cand.fitness.axes:
        assert axis.label in text          # 다섯 축이 모두 설명에 나온다
    assert len(cand.fitness.axes) == 5
    assert "왜 이 점수인가" in text


def test_csv_carries_grade_and_axis_detail(tmp_path):
    result = run_screen(ScreenConfig(), offline=True)
    body = write_csv(result, tmp_path / "out.csv").read_text(encoding="utf-8-sig")
    header = body.splitlines()[0]
    for column in ("등급", "점수", "예상 판넬 일감(원)", "판넬일감 근거", "확인하실 점"):
        assert column in header


# --- 매출 기록 / 부족분 채우기 ---------------------------------------------------

def _book(rows, targets=None):
    from prime_contractor.sales import _from_snapshot
    return _from_snapshot({
        "months": [{"ym": ym, "revenue": rev} for ym, rev in rows],
        "targets": targets or {},
    })


def test_reads_the_sales_app_snapshot_schema():
    """매출 앱이 localStorage 에 담는 모양 그대로 읽어야 한다."""
    book = _book([("2026-08", 24900000), ("2026-09", 19320000)],
                 {"2026-08": 27000000, "2026-09": 27000000})
    aug = book.month("2026-08")
    assert aug.revenue == 24900000 and aug.target == 27000000
    assert aug.gap == 2100000
    assert round(aug.rate * 100) == 92


def test_current_month_is_not_counted_as_a_miss():
    """진행 중인 달은 당연히 목표에 못 미친다. 그걸 미달로 보면 매번 경보가 뜬다."""
    from datetime import date
    book = _book([("2026-08", 24900000), ("2026-09", 5000000)],
                 {"2026-08": 27000000, "2026-09": 27000000})
    assert book.latest_closed(date(2026, 9, 22)).ym == "2026-08"


def test_recent_gap_sums_several_months():
    from datetime import date
    book = _book([("2026-07", 25000000), ("2026-08", 24000000)],
                 {"2026-07": 27000000, "2026-08": 27000000})
    assert book.recent_gap(2, date(2026, 9, 1)) == 5000000
    assert book.recent_gap(1, date(2026, 9, 1)) == 3000000


def test_exceeding_target_leaves_no_gap():
    book = _book([("2026-08", 30000000)], {"2026-08": 27000000})
    assert book.month("2026-08").gap == 0
    assert book.month("2026-08").achieved is True


def test_csv_accepts_korean_headers_and_loose_dates(tmp_path):
    from prime_contractor.sales import load_sales
    path = tmp_path / "sales.csv"
    path.write_text("연월,매출,목표\n2026/7,\"25,000,000\",27000000\n2026.08,24900000,27000000\n",
                    encoding="utf-8")
    book = load_sales(path)
    assert [m.ym for m in book.sorted_months()] == ["2026-07", "2026-08"]
    assert book.month("2026-07").revenue == 25000000     # 쉼표가 섞여 있어도 읽는다


def test_monthly_expected_scales_by_period_and_grade():
    """추정 물량은 조회 기간 전체 값이라 월로 나누고, 수주 확률을 곱해야 한다."""
    from prime_contractor.sales import monthly_expected
    cand = _scored("갑전기", address="경기도 평택시",
                   awards=[Award(title="배전반 교체공사", amount=12 * 10**8, category="공사",
                                 demand_org="A시")])
    enrich([cand]); score_candidate(cand, ScreenConfig())
    half_year = monthly_expected(cand, lookback_days=180)
    quarter = monthly_expected(cand, lookback_days=90)
    assert quarter > half_year          # 같은 물량이 짧은 기간에 나왔으면 월 기대치가 크다
    assert half_year > 0


def test_plan_stops_once_the_gap_is_covered():
    from prime_contractor.sales import plan_to_close_gap
    cands = []
    for i in range(6):
        c = _scored(f"업체{i}", address="경기도 평택시",
                    awards=[Award(title="배전반 교체공사", amount=10**9, category="공사",
                                  demand_org=f"{i}시")])
        enrich([c]); score_candidate(c, ScreenConfig())
        cands.append(c)
    plan = plan_to_close_gap(1_000_000, cands, lookback_days=180)
    assert plan.is_covered
    assert len(plan.rows) < len(cands)          # 부족분을 덮으면 거기서 멈춘다
    assert plan.rows[-1].cumulative >= plan.gap


def test_plan_says_so_when_candidates_cannot_cover_the_gap():
    from prime_contractor.sales import plan_to_close_gap
    small = _scored("작은곳", address="경기도 평택시",
                    awards=[Award(title="분전반 교체", amount=10**7, category="공사")])
    enrich([small]); score_candidate(small, ScreenConfig())
    plan = plan_to_close_gap(50 * 10**8, [small], lookback_days=180)
    assert not plan.is_covered
    assert plan.shortfall_left > 0
    assert "모자랍니다" in plan.note


def test_plan_skips_candidates_with_no_expected_volume():
    """점수가 높아도 판넬 물량이 안 나오는 곳은 부족분 메우기에 도움이 안 된다."""
    from prime_contractor.sales import plan_to_close_gap
    no_volume = _scored("조경업체", address="경기도 안성시",
                        awards=[Award(title="청사 화단 조경공사", amount=10**10, category="공사")])
    enrich([no_volume]); score_candidate(no_volume, ScreenConfig())
    plan = plan_to_close_gap(10**7, [no_volume], lookback_days=180)
    assert plan.rows == []


def test_no_gap_means_no_plan():
    from prime_contractor.sales import plan_to_close_gap
    plan = plan_to_close_gap(0, [], lookback_days=180)
    assert not plan.rows and "채우셨습니다" in plan.note


def test_gap_report_shows_shortfall_and_plan():
    from prime_contractor.report import render_gap
    from prime_contractor.sales import plan_to_close_gap
    book = _book([("2026-08", 24900000)], {"2026-08": 27000000})
    record = book.month("2026-08")
    cand = _scored("갑전기", address="경기도 평택시",
                   awards=[Award(title="배전반 교체공사", amount=10**9, category="공사")])
    enrich([cand]); score_candidate(cand, ScreenConfig())
    text = render_gap(book, record, plan_to_close_gap(record.gap, [cand], 180))
    assert "모자랍니다" in text and "한 달 예상" in text and "갑전기" in text


# --- 업데이트 확인 --------------------------------------------------------------

@pytest.mark.parametrize("latest,current,expected", [
    ("v1.1.0", "1.0.0", True),
    ("v1.0.1", "1.0.0", True),
    ("v2.0.0-beta", "1.9.9", True),
    ("v1.0.0", "1.0.0", False),
    ("v1.0", "1.0.0", False),          # 1.0 과 1.0.0 은 같다
    ("v0.9.9", "1.0.0", False),
    ("", "1.0.0", False),              # 버전을 못 읽으면 업데이트라고 우기지 않는다
    ("최신판", "1.0.0", False),
])
def test_version_comparison(latest, current, expected):
    from prime_contractor.updater import is_newer
    assert is_newer(latest, current) is expected


def test_update_check_returns_none_when_offline(monkeypatch):
    """업데이트 확인이 실패해도 앱은 떠야 한다."""
    from prime_contractor import updater

    def boom(*a, **kw):
        raise OSError("네트워크 없음")

    monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
    assert updater.check_for_update("1.0.0") is None


def _fake_releases(monkeypatch, releases):
    from prime_contractor import updater

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(releases).encode()

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **kw: FakeResponse())
    return updater


def test_update_check_reads_release_payload(monkeypatch):
    updater = _fake_releases(monkeypatch, [
        {"tag_name": "finder-v1.4.0", "html_url": "https://example.test/r/finder-v1.4.0",
         "body": "매출 탭 추가"},
    ])
    info = updater.check_for_update("1.0.0")
    assert info is not None
    assert info.latest == "1.4.0"
    assert "finder-v1.4.0" in info.url
    assert "1.4.0" in info.message and "1.0.0" in info.message


def test_no_update_when_already_current(monkeypatch):
    updater = _fake_releases(monkeypatch, [{"tag_name": "finder-v1.0.0"}])
    assert updater.check_for_update("1.0.0") is None


def test_other_apps_releases_are_ignored(monkeypatch):
    """한 저장소에 앱이 여럿이다. 남의 배포를 내 업데이트로 착각하면 안 된다."""
    updater = _fake_releases(monkeypatch, [
        {"tag_name": "desktop-latest", "html_url": "https://example.test/r/desktop"},
        {"tag_name": "v9.9.9", "html_url": "https://example.test/r/other-app"},
        {"tag_name": "finder-v1.0.0", "html_url": "https://example.test/r/finder"},
    ])
    assert updater.check_for_update("1.0.0") is None       # 내 최신은 1.0.0 그대로


def test_picks_highest_version_not_most_recent_entry():
    """목록 순서가 아니라 버전으로 고른다."""
    from prime_contractor.updater import pick_latest
    newest = pick_latest([
        {"tag_name": "finder-v1.2.0"},
        {"tag_name": "finder-v1.10.0"},
        {"tag_name": "finder-v1.9.0"},
    ])
    assert newest["tag_name"] == "finder-v1.10.0"


def test_drafts_and_prereleases_are_skipped():
    from prime_contractor.updater import pick_latest
    picked = pick_latest([
        {"tag_name": "finder-v2.0.0", "draft": True},
        {"tag_name": "finder-v1.9.0", "prerelease": True},
        {"tag_name": "finder-v1.5.0"},
    ])
    assert picked["tag_name"] == "finder-v1.5.0"


def test_no_release_for_this_app_yet(monkeypatch):
    updater = _fake_releases(monkeypatch, [{"tag_name": "desktop-latest"}])
    assert updater.check_for_update("1.0.0") is None


def test_package_version_is_parseable():
    """태그와 맞춰야 하는 값이라 형식이 깨지면 안 된다."""
    from prime_contractor import __version__
    from prime_contractor.updater import parse_version
    assert len(parse_version(__version__)) == 3


# --- 폐업 회사 걸러내기 ----------------------------------------------------------

class _FakeNts:
    """국세청 응답을 흉내낸다. 요청받은 번호와 상태를 기록해 둔다."""

    def __init__(self, table: dict[str, dict]) -> None:
        self.table = table
        self.asked: list[str] = []

    def statuses(self, biznos):
        from prime_contractor.sources.nts import clean_bizno
        self.asked = [clean_bizno(b) for b in biznos if clean_bizno(b)]
        return {k: v for k, v in self.table.items() if k in self.asked}


@pytest.mark.parametrize("raw,expected", [
    ("123-45-67890", "1234567890"),
    ("1234567890", "1234567890"),
    (" 123 45 67890 ", "1234567890"),
    ("12345", ""),            # 10자리가 아니면 버린다
    ("", ""),
    (None, ""),
])
def test_bizno_cleaned_before_lookup(raw, expected):
    from prime_contractor.sources.nts import clean_bizno
    assert clean_bizno(raw) == expected


def test_closed_business_is_dropped_with_a_reason():
    from prime_contractor.sources.nts import apply_statuses
    alive = _cand("살아있는전기", bizno="1111111111",
                  awards=[Award(title="배전반 교체", amount=10**9, category="공사")])
    closed = _cand("문닫은전기", bizno="2222222222",
                   awards=[Award(title="배전반 교체", amount=10**9, category="공사")])
    checked, dead = apply_statuses([alive, closed], {
        "1111111111": {"code": "01", "label": "계속사업자", "closed_at": ""},
        "2222222222": {"code": "03", "label": "폐업자", "closed_at": "20250731"},
    })
    assert (checked, dead) == (2, 1)
    assert closed.business_closed and not alive.business_closed

    cfg = ScreenConfig()
    for c in (alive, closed):
        score_candidate(c, cfg)
    passed, excluded = split_by_overlap([alive, closed], cfg)
    assert [c.name for c in passed] == ["살아있는전기"]
    assert "폐업" in excluded[0].overlap.reasons[-1]
    assert "20250731" in excluded[0].overlap.reasons[-1]   # 언제 닫았는지도 남긴다


def test_suspended_business_is_kept_but_flagged():
    """휴업은 다시 열 수도 있다. 빼지 말고 '확인하실 점' 으로만 알린다."""
    from prime_contractor.sources.nts import apply_statuses
    cand = _cand("쉬는중전기", bizno="3333333333",
                 awards=[Award(title="배전반 교체", amount=10**9, category="공사")])
    apply_statuses([cand], {"3333333333": {"code": "02", "label": "휴업자", "closed_at": ""}})
    score_candidate(cand, ScreenConfig())
    passed, _ = split_by_overlap([cand], ScreenConfig())
    assert passed == [cand]
    assert any("휴업" in c for c in cand.fitness.cautions)


def test_keep_closed_option_leaves_them_in():
    from prime_contractor.sources.nts import apply_statuses
    cfg = ScreenConfig(drop_closed_businesses=False)
    cand = _cand("문닫은전기", bizno="2222222222",
                 awards=[Award(title="배전반 교체", amount=10**9, category="공사")])
    apply_statuses([cand], {"2222222222": {"code": "03", "label": "폐업자", "closed_at": ""}})
    score_candidate(cand, cfg)
    passed, _ = split_by_overlap([cand], cfg)
    assert passed == [cand]


def test_status_check_runs_over_collected_biznos():
    """낙찰 자료에 사업자번호가 같이 오므로 따로 입력받을 필요가 없다."""
    cfg = ScreenConfig()
    nts = _FakeNts({"1234567890": {"code": "03", "label": "폐업자", "closed_at": ""}})
    result = run_screen(cfg, offline=False, g2b_client=_StubG2B(), nts_client=nts)
    assert "1234567890" in nts.asked
    assert any("폐업" in n for n in result.notes)
    assert all(not c.business_closed for c in result.passed)


class _StubG2B:
    """낙찰 두 건만 돌려주는 가짜 나라장터."""

    keyword_fallback: set = set()

    def fetch_awards(self, **kw):
        return [
            Award(notice_no="1", title="정수장 배전반 교체공사", demand_org="A시",
                  winner_name="문닫은전기", winner_bizno="123-45-67890",
                  amount=10**9, category="공사"),
            Award(notice_no="2", title="하수처리장 제어반 설치", demand_org="B시",
                  winner_name="살아있는전기", winner_bizno="9999999999",
                  amount=5 * 10**8, category="공사"),
        ]


def test_lookup_failure_does_not_stop_the_search(monkeypatch):
    """상태 확인은 부가 정보다. 국세청이 응답 안 해도 탐색은 끝나야 한다."""
    from prime_contractor.sources import nts as nts_module

    class Boom:
        def post(self, *a, **kw):
            raise __import__("requests").RequestException("국세청 응답 없음")

    client = nts_module.NtsClient("key", sleep_sec=0, session=Boom())
    assert client.statuses(["1234567890"]) == {}


# --- 엑셀 장부 읽기 --------------------------------------------------------------

def _make_xlsx(tmp_path, sheets: dict[str, dict[str, object]], name="장부.xlsx"):
    """{시트이름: {'B4': '1월', 'C4': 88158000}} 로 최소한의 xlsx 를 만든다."""
    import re as _re
    import zipfile

    strings: list[str] = []

    def sid(text: str) -> int:
        if text not in strings:
            strings.append(text)
        return strings.index(text)

    sheet_xml = {}
    for i, (title, cells) in enumerate(sheets.items(), 1):
        rows: dict[int, list[str]] = {}
        for ref, value in cells.items():
            row = int(_re.match(r"[A-Z]+(\d+)", ref).group(1))
            if isinstance(value, str):
                cell = f'<c r="{ref}" t="s"><v>{sid(value)}</v></c>'
            else:
                cell = f'<c r="{ref}"><v>{value}</v></c>'
            rows.setdefault(row, []).append(cell)
        body = "".join(f'<row r="{r}">{"".join(cs)}</row>' for r, cs in sorted(rows.items()))
        sheet_xml[f"xl/worksheets/sheet{i}.xml"] = (
            '<?xml version="1.0"?><worksheet xmlns='
            '"http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{body}</sheetData></worksheet>")

    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        sheet_tags = "".join(
            f'<sheet name="{t}" sheetId="{i}" r:id="rId{i}"/>'
            for i, t in enumerate(sheets, 1))
        zf.writestr("xl/workbook.xml",
                    '<?xml version="1.0"?><workbook xmlns='
                    '"http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
                    f'relationships"><sheets>{sheet_tags}</sheets></workbook>')
        rels = "".join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>'
                       for i in range(1, len(sheets) + 1))
        zf.writestr("xl/_rels/workbook.xml.rels",
                    '<?xml version="1.0"?><Relationships xmlns='
                    f'"http://schemas.openxmlformats.org/package/2006/relationships">{rels}'
                    "</Relationships>")
        zf.writestr("xl/sharedStrings.xml",
                    '<?xml version="1.0"?><sst xmlns='
                    '"http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    + "".join(f"<si><t>{t}</t></si>" for t in strings) + "</sst>")
        for member, xml in sheet_xml.items():
            zf.writestr(member, xml)
    return path


def test_reads_a_handwritten_excel_ledger(tmp_path):
    from prime_contractor.sales import load_sales
    path = _make_xlsx(tmp_path, {"월매출장": {
        "C2": "2026년 월매출원장리스트", "C3": "매출금액",
        "B4": "1월", "C4": 88158000,
        "B5": "2월", "C5": 88392000,
        "B6": "3월", "C6": 120105000,
    }})
    book = load_sales(path)
    assert [m.ym for m in book.sorted_months()] == ["2026-01", "2026-02", "2026-03"]
    assert book.month("2026-01").revenue == 88158000


def test_total_row_is_not_read_as_a_month():
    """실제 장부에서 1~9월 합계가 '10월' 칸에 들어앉아 있었다.

    그냥 읽으면 '10월에 8억 벌었다'가 되어 목표 대비 계산이 통째로 어긋난다.
    """
    from prime_contractor.sales import _block_to_months
    block = {"col": "C", "rows": {
        4: (1, 100.0), 5: (2, 200.0), 6: (3, 300.0),
        7: (4, 600.0),                      # ← 앞선 세 달의 합계
    }}
    months = _block_to_months(block, 2026)
    assert [m.ym for m in months] == ["2026-01", "2026-02", "2026-03"]
    assert sum(m.revenue for m in months) == 600


def test_side_by_side_blocks_are_kept_apart(tmp_path):
    """한 행에 우리 매출과 거래처 마감이 나란히 있는 장부가 실제로 있다."""
    from prime_contractor.sales import load_sales
    path = _make_xlsx(tmp_path, {"월매출장": {
        "C2": "2026년 월매출", "C3": "매출금액", "G3": "결제금액",
        "B4": "1월", "C4": 88158000, "F4": "1월", "G4": 13003045,
        "B5": "2월", "C5": 88392000, "F5": "2월", "G5": 15005813,
    }})
    book = load_sales(path)
    # 왼쪽(우리 매출) 블록을 골라야 한다. 오른쪽 거래처 금액이 섞이면 안 된다.
    assert book.month("2026-01").revenue == 88158000
    assert sum(m.revenue for m in book.months) == 88158000 + 88392000


def test_year_comes_from_the_title_when_cells_only_say_month(tmp_path):
    from prime_contractor.sales import load_sales
    path = _make_xlsx(tmp_path, {"월매출장": {
        "C2": "2024년 월매출원장리스트", "C3": "매출금액",
        "B4": "1월", "C4": 5000000,
    }}, name="ledger.xlsx")
    assert load_sales(path).months[0].ym == "2024-01"


def test_customer_sheet_loses_to_the_revenue_sheet(tmp_path):
    from prime_contractor.sales import load_sales
    path = _make_xlsx(tmp_path, {
        "거래처매출장": {"C2": "2026년", "C3": "결제금액", "B4": "1월", "C4": 238000},
        "월매출장": {"C2": "2026년", "C3": "매출금액", "B4": "1월", "C4": 88158000},
    })
    assert load_sales(path).month("2026-01").revenue == 88158000


def test_excel_without_any_month_rows_says_so(tmp_path):
    from prime_contractor.sales import load_sales
    path = _make_xlsx(tmp_path, {"연차표": {"B2": "홍길동", "C2": 15}})
    with pytest.raises(ValueError, match="월별 매출"):
        load_sales(path)


def test_target_can_be_supplied_when_the_ledger_has_none():
    book = _book([("2026-07", 90000000), ("2026-08", 82142000)])
    assert not book.has_targets
    assert book.apply_target(100000000) == 2
    assert book.has_targets
    assert book.month("2026-08").gap == 17858000


def test_apply_target_keeps_targets_already_written():
    book = _book([("2026-07", 90000000), ("2026-08", 82142000)], {"2026-07": 95000000})
    book.apply_target(100000000)
    assert book.month("2026-07").target == 95000000      # 적혀 있던 값은 그대로
    assert book.month("2026-08").target == 100000000


def test_average_revenue_ignores_the_running_month():
    from datetime import date
    book = _book([("2026-06", 60000000), ("2026-07", 80000000), ("2026-09", 1000000)])
    assert book.average_revenue(6, date(2026, 9, 15)) == 70000000


# --- 손익분기 ------------------------------------------------------------------

def test_breakeven_arithmetic():
    from prime_contractor.breakeven import CostModel
    m = CostModel(monthly_fixed=30_000_000, variable_ratio=0.6, monthly_profit=5_000_000)
    assert m.margin_ratio == pytest.approx(0.4)
    assert m.breakeven == 75_000_000            # 3천만 ÷ 0.4
    assert m.target == 87_500_000               # (3천만+5백만) ÷ 0.4
    assert m.profit_at(75_000_000) == 0         # 손익분기에서 딱 0
    assert m.profit_at(73_287_092) < 0          # 그 밑이면 적자


def test_without_profit_target_the_breakeven_is_the_target():
    from prime_contractor.breakeven import CostModel
    m = CostModel(monthly_fixed=20_000_000, variable_ratio=0.5)
    assert m.target == m.breakeven == 40_000_000


@pytest.mark.parametrize("ratio", [1.0, 1.2, -0.1])
def test_impossible_cost_ratio_is_rejected(ratio):
    """재료비가 매출과 같거나 크면 팔수록 손해라 손익분기가 없다."""
    from prime_contractor.breakeven import CostModel
    with pytest.raises(ValueError):
        CostModel(monthly_fixed=10_000_000, variable_ratio=ratio)


@pytest.mark.parametrize("text,expected", [("60", 0.6), ("60%", 0.6), ("0.6", 0.6), ("55.5", 0.555)])
def test_ratio_accepts_percent_or_fraction(text, expected):
    from prime_contractor.breakeven import parse_ratio
    assert parse_ratio(text) == pytest.approx(expected)


def test_cost_model_from_income_statement():
    from prime_contractor.breakeven import from_financials
    m = from_financials(annual_revenue=1_200_000_000, cost_of_sales=720_000_000,
                        sga=240_000_000)
    assert m.variable_ratio == pytest.approx(0.6)
    assert m.monthly_fixed == 20_000_000        # 판관비 2.4억 ÷ 12


def test_treating_all_cost_of_sales_as_variable_understates_breakeven():
    """흑자 회사에서 매출원가를 전부 변동비로 보면 손익분기가 실제보다 낮게 나온다.

    '안전하다'고 잘못 알려주는 쪽으로 틀리므로, 안내문에서 이 방향을 경고해야 한다.
    """
    from prime_contractor.breakeven import from_financials
    naive = from_financials(1_200_000_000, 720_000_000, 240_000_000)
    adjusted = from_financials(1_200_000_000, 720_000_000, 240_000_000,
                               fixed_share_of_cost_of_sales=0.25)
    assert adjusted.variable_ratio < naive.variable_ratio
    assert adjusted.monthly_fixed > naive.monthly_fixed
    assert naive.breakeven < adjusted.breakeven        # 50,000,000 < 약 63,636,364
    assert naive.breakeven == 50_000_000


def test_breakeven_report_counts_loss_months_and_skips_running_month():
    from datetime import date
    from prime_contractor.breakeven import CostModel
    from prime_contractor.report import render_breakeven
    book = _book([("2026-06", 73_287_092), ("2026-07", 118_740_500), ("2026-08", 82_142_000),
                  ("2026-09", 52_236_400)])
    text = render_breakeven(book, CostModel(30_000_000, 0.6), today=date(2026, 9, 23))
    assert "끝난 3개월 중 적자 1개월" in text      # 6월만 적자, 9월은 진행 중이라 안 센다
    assert "진행중" in text
    assert "7,500만원" in text


def test_gap_uses_breakeven_target_when_costs_given(tmp_path, capsys):
    from prime_contractor import cli
    path = tmp_path / "매출.csv"
    path.write_text("연월,매출\n2026-06,73287092\n2026-07,118740500\n", encoding="utf-8")
    code = cli.main(["gap", "--sales", str(path), "--month", "2026-06",
                     "--fixed-cost", "30000000", "--variable-ratio", "60", "--offline"])
    out = capsys.readouterr().out
    assert code == 0
    assert "손익분기 매출  월 7,500만원" in out
    assert "171만원 모자랍니다" in out           # 7,500만 − 7,329만


def test_gap_needs_both_cost_numbers(tmp_path, capsys):
    from prime_contractor import cli
    path = tmp_path / "매출.csv"
    path.write_text("연월,매출\n2026-06,73287092\n", encoding="utf-8")
    code = cli.main(["gap", "--sales", str(path), "--fixed-cost", "30000000", "--offline"])
    assert code == 2
    assert "둘 다" in capsys.readouterr().err


# --- 최우선 목표 (의존도 낮추기) ---------------------------------------------------

def test_goal_back_calculates_contacts_per_week():
    from prime_contractor.goal import GoalInputs, build_plan
    plan = build_plan(GoalInputs(monthly_revenue=100_000_000, target_dependency=0.7,
                                 months=12, revenue_per_new_client=10_000_000))
    # 1억 ÷ 0.7 = 1억 4,286만 → 새로 4,286만 → 1천만씩 5곳
    assert plan.new_revenue_needed == 42_857_143
    assert plan.clients_needed == 5
    # 전환율 30% × 40% × 50% = 6% → 5 ÷ 0.06 = 84곳 (올림)
    assert plan.contacts_needed == 84
    assert plan.registrations_needed == 10          # 5 ÷ 0.5
    assert plan.weeks == 52
    assert plan.contacts_per_week == pytest.approx(84 / 52)


def test_goal_counts_revenue_already_coming_from_others():
    """이미 다른 곳에서 조금 들어오고 있으면 그만큼 덜 벌어도 된다."""
    from prime_contractor.goal import GoalInputs, build_plan
    alone = build_plan(GoalInputs(monthly_revenue=100_000_000, current_dependency=1.0))
    some = build_plan(GoalInputs(monthly_revenue=100_000_000, current_dependency=0.9))
    assert some.new_revenue_needed < alone.new_revenue_needed


def test_goal_already_met_needs_nothing():
    from prime_contractor.goal import GoalInputs, build_plan
    plan = build_plan(GoalInputs(monthly_revenue=100_000_000, current_dependency=0.6,
                                 target_dependency=0.7))
    assert plan.already_there
    assert plan.clients_needed == plan.contacts_needed == 0
    assert "이미" in plan.summary()[0]


def test_goal_risk_if_the_only_client_stops():
    """원청이 멈추면 남는 매출로 고정비를 못 덮는 만큼이 매달 적자다."""
    from prime_contractor.goal import GoalInputs, build_plan
    plan = build_plan(GoalInputs(monthly_revenue=100_000_000, current_dependency=1.0,
                                 monthly_fixed=30_000_000, margin_ratio=0.4,
                                 cash_on_hand=90_000_000))
    assert plan.loss_if_anchor_stops == 30_000_000   # 남는 매출 0 → 고정비 전부
    assert plan.months_of_runway == pytest.approx(3.0)
    assert any("적자" in l for l in plan.summary())


def test_goal_rejects_nonsense():
    from prime_contractor.goal import GoalInputs
    for bad in ({"target_dependency": 1.2}, {"target_dependency": 0},
                {"months": 0}, {"revenue_per_new_client": 0},
                {"funnel": {"연락 → 미팅": 0}}):
        with pytest.raises(ValueError):
            GoalInputs(monthly_revenue=100_000_000, **bad)


# --- 영업 진행 기록 ---------------------------------------------------------------

def test_lead_moves_through_stages_and_remembers_history(tmp_path):
    from datetime import date
    from prime_contractor.leads import Lead, LeadBook
    book = LeadBook(path=tmp_path / "leads.json")
    book.add(Lead(name="갑전기"), date(2026, 9, 1))
    book.move("갑전기", "연락함", date(2026, 9, 2))
    lead = book.move("갑전기", "미팅", date(2026, 9, 9), next_action="서류 준비")
    assert [s for s, _ in lead.history] == ["후보", "연락함", "미팅"]
    assert lead.next_date == "2026-09-16"          # 날짜 안 주면 1주 뒤
    assert lead.reached("연락함") and not lead.reached("등록완료")


def test_paused_lead_still_counts_what_it_passed(tmp_path):
    """보류로 돌려도 이미 미팅까지 간 건 전환율 계산에 남아야 한다."""
    from datetime import date
    from prime_contractor.leads import Lead, LeadBook
    book = LeadBook(path=tmp_path / "leads.json")
    book.add(Lead(name="을전기"), date(2026, 9, 1))
    book.move("을전기", "미팅", date(2026, 9, 3))
    book.move("을전기", "보류", date(2026, 9, 10))
    assert book.funnel()["미팅"] == 1


def test_leads_survive_save_and_load(tmp_path):
    from datetime import date
    from prime_contractor.leads import Lead, LeadBook
    path = tmp_path / "leads.json"
    book = LeadBook(path=path)
    book.add(Lead(name="갑전기", bizno="1234567890", grade="A"), date(2026, 9, 1))
    book.move("갑전기", "등록완료", date(2026, 9, 20))
    book.find("갑전기").payment_terms = "현금 30일"
    book.save()
    again = LeadBook.load(path)
    lead = again.find("1234567890")
    assert lead.stage == "등록완료" and lead.payment_terms == "현금 30일"
    assert lead.history[-1] == ("등록완료", "2026-09-20")


def test_broken_leads_file_does_not_stop_the_app(tmp_path):
    from prime_contractor.leads import LeadBook
    path = tmp_path / "leads.json"
    path.write_text("{망가진", encoding="utf-8")
    book = LeadBook.load(path)
    assert book.leads == []
    assert (tmp_path / "leads.broken.json").exists()   # 지우지 않고 옆에 남긴다


def test_adding_the_same_company_twice_keeps_one(tmp_path):
    from prime_contractor.leads import Lead, LeadBook
    book = LeadBook(path=tmp_path / "leads.json")
    _, first = book.add(Lead(name="갑전기", bizno="111"))
    _, second = book.add(Lead(name="갑전기", bizno="111"))
    assert first and not second and len(book.leads) == 1


def test_unknown_stage_is_rejected(tmp_path):
    from prime_contractor.leads import Lead, LeadBook
    book = LeadBook(path=tmp_path / "leads.json")
    book.add(Lead(name="갑전기"))
    with pytest.raises(ValueError):
        book.move("갑전기", "계약서명")


def test_overdue_and_weekly_contacts(tmp_path):
    from datetime import date
    from prime_contractor.leads import Lead, LeadBook
    book = LeadBook(path=tmp_path / "leads.json")
    for name in ("갑", "을", "병"):
        book.add(Lead(name=name), date(2026, 9, 1))
    book.move("갑", "연락함", date(2026, 9, 22))        # 이번 주 월요일
    book.move("을", "연락함", date(2026, 9, 10))        # 지난 주 이전
    book.find("을").next_date = "2026-09-17"
    assert book.contacts_in_week(date(2026, 9, 23)) == 1
    assert [l.name for l in book.overdue(date(2026, 9, 23))] == ["을"]


def test_actual_rates_wait_for_enough_records(tmp_path):
    """두세 곳 기록으로 '전환율 50%' 라고 하면 오해를 산다."""
    from prime_contractor.leads import Lead, LeadBook
    book = LeadBook(path=tmp_path / "leads.json")
    for i in range(3):
        book.add(Lead(name=f"업체{i}"))
        book.move(f"업체{i}", "연락함")
    book.move("업체0", "미팅")
    assert book.actual_rates()["연락 → 미팅"] is None
    for i in range(3, 6):
        book.add(Lead(name=f"업체{i}"))
        book.move(f"업체{i}", "연락함")
    assert book.actual_rates()["연락 → 미팅"] == pytest.approx(1 / 6)


def test_progress_shows_this_weeks_target(tmp_path):
    from datetime import date
    from prime_contractor.goal import GoalInputs, build_plan
    from prime_contractor.leads import Lead, LeadBook, progress_lines
    book = LeadBook(path=tmp_path / "leads.json")
    book.add(Lead(name="갑"), date(2026, 9, 21))
    book.move("갑", "연락함", date(2026, 9, 22))
    plan = build_plan(GoalInputs(monthly_revenue=100_000_000))
    text = "\n".join(progress_lines(book, plan, today=date(2026, 9, 23)))
    assert "이번 주 연락 1 / 2곳" in text
    assert "새 원청    0 / 5곳" in text


def test_goal_cli_reads_the_ledger(tmp_path, capsys, monkeypatch):
    from prime_contractor import cli
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = tmp_path / "매출.csv"
    path.write_text("연월,매출\n2026-06,100000000\n2026-07,100000000\n", encoding="utf-8")
    assert cli.main(["goal", "--sales", str(path)]) == 0
    out = capsys.readouterr().out
    assert "이번 주 목표" in out and "새 원청 5곳" in out


def test_empty_asset_list_does_not_hide_a_new_version():
    """GitHub 의 파일 목록은 비어 보여도 파일은 받아질 때가 있다(v1.3~1.5 실제 사례).

    목록이 비었다고 건너뛰면 앱이 새 버전을 영영 알려주지 못한다.
    """
    from prime_contractor.updater import pick_latest
    picked = pick_latest([
        {"tag_name": "finder-v1.5.0", "assets": []},
        {"tag_name": "finder-v1.2.0", "assets": [{"name": "PrimeFinder.exe", "state": "uploaded"}]},
    ])
    assert picked["tag_name"] == "finder-v1.5.0"


def test_release_with_an_unfinished_upload_is_skipped():
    from prime_contractor.updater import pick_latest
    picked = pick_latest([
        {"tag_name": "finder-v1.6.0", "assets": [{"name": "PrimeFinder.exe", "state": "starter"}]},
        {"tag_name": "finder-v1.5.0", "assets": [{"name": "PrimeFinder.exe", "state": "uploaded"}]},
    ])
    assert picked["tag_name"] == "finder-v1.5.0"


# --- 베타 ----------------------------------------------------------------------

def test_beta_build_is_labelled_by_date_and_commit(monkeypatch):
    from prime_contractor import build_info
    monkeypatch.setattr(build_info, "IS_BETA", True)
    monkeypatch.setattr(build_info, "BUILD", "09-23 17:40 a1b2c3d")
    assert build_info.label() == "베타 · 09-23 17:40 a1b2c3d"


def test_beta_from_source_says_so(monkeypatch):
    from prime_contractor import build_info
    monkeypatch.setattr(build_info, "IS_BETA", True)
    monkeypatch.setattr(build_info, "BUILD", "")
    assert build_info.label() == "베타 · 개발용"


def test_beta_does_not_nag_about_updates(monkeypatch):
    """계속 고치는 중이라 켤 때마다 '새 버전' 띠가 뜨면 방해만 된다."""
    from prime_contractor import build_info
    monkeypatch.setattr(build_info, "IS_BETA", True)
    assert build_info.should_check_updates() is False
    monkeypatch.setattr(build_info, "IS_BETA", False)
    assert build_info.should_check_updates() is True


def test_stable_label_uses_version_number(monkeypatch):
    from prime_contractor import __version__, build_info
    monkeypatch.setattr(build_info, "IS_BETA", False)
    assert build_info.label() == f"v{__version__}"


def test_package_is_beta_until_released():
    import prime_contractor
    assert prime_contractor.CHANNEL in ("beta", "stable")


def test_readme_still_documents_every_feature():
    """README 의 한 부분을 갈아 끼우다 다른 절이 통째로 날아간 적이 있다.

    그 뒤로 문서에 넣었다고 생각한 절들이 끼울 자리를 못 찾아 조용히 빠졌다.
    기능마다 설명이 남아 있는지 확인한다.
    """
    from pathlib import Path
    text = (Path(__file__).parent.parent / "prime_contractor" / "README.md").read_text(encoding="utf-8")
    for must in ("finder-beta", "최우선 목표", "영업 진행 기록", "손익분기",
                 "엑셀 장부", "폐업", "모자란 만큼", "적합도", "어림값"):
        assert must in text, must
