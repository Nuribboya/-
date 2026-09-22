"""탐색 조건 설정.

기본값은 '안성 소재 자동제어 판넬 제조사 / 기존 원청 = KC그룹' 기준이다.
`--config my.json` 으로 일부만 덮어쓸 수 있다 (지정한 키만 교체).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from prime_contractor.industry import IndustryProfile, TargetSector

# --- 기존 원청(KC그룹) 프로필 -------------------------------------------------
#
# KC그룹 계열사는 상호가 대부분 '케이씨~' 로 시작해서 접두사 규칙으로 잡는다.
# 업종코드(KSIC)는 DART 기업개황의 induty_code 기준이며, 실제 코드는
# `python -m prime_contractor.cli dart-lookup 케이씨텍` 으로 확인해서
# 아래 값을 본인이 아는 사실에 맞게 조정하는 것을 권한다.
KC_GROUP = IndustryProfile(
    name="KC그룹",
    affiliate_prefixes=("케이씨", "KC"),
    affiliate_names=("케이씨텍", "케이씨이앤씨", "케이엔솔", "케이피씨"),
    ksic_prefixes=(
        "261",    # 반도체 제조업
        "262",    # 전자부품(디스플레이 패널 등)
        "2029",   # 기타 화학제품 (전자재료·슬러리)
        "2923",   # 반도체·디스플레이 제조용 기계
    ),
    core_keywords=(
        "반도체", "디스플레이", "웨이퍼", "OLED", "LCD", "CMP", "슬러리",
        "클린룸", "클린 룸", "FAB", "팹", "포토레지스트", "식각", "증착",
        "세정장비", "전공정", "후공정",
    ),
    adjacent_keywords=(
        "전자재료", "특수가스", "가스공급장치", "케미컬", "화학소재",
        "2차전지", "이차전지", "태양광",
    ),
)

# --- 판넬 수요가 나오는 타깃 업종 ---------------------------------------------
#
# weight = 자동제어 판넬(MCC/배전반/계장반) 물량이 얼마나 꾸준히 나오는가.
TARGET_SECTORS: tuple[TargetSector, ...] = (
    TargetSector(
        "상하수도·수처리", weight=1.0,
        keywords=("상수도", "하수", "정수장", "배수지", "가압장", "취수",
                  "수처리", "폐수", "하수처리장", "물재생", "관로", "펌프장"),
        ksic_prefixes=("360", "370"),
        note="지자체·수자원공사 발주가 꾸준하고 계장·감시제어반 비중이 크다",
    ),
    TargetSector(
        "환경·폐기물·소각", weight=0.9,
        keywords=("소각", "폐기물", "매립", "자원회수", "바이오가스", "악취", "집진",
                  "분뇨", "자원화", "퇴비", "재활용"),
        ksic_prefixes=("380", "390"),
    ),
    TargetSector(
        "발전·에너지", weight=0.95,
        keywords=("발전소", "열병합", "변전", "수배전", "ESS", "신재생",
                  "연료전지", "보일러", "터빈", "송전"),
        ksic_prefixes=("351", "352", "353"),
    ),
    TargetSector(
        "식품·음료", weight=0.85,
        keywords=("식품", "음료", "유가공", "제과", "도축", "사료", "주류", "급식센터"),
        ksic_prefixes=("10", "11"),
        note="증설·라인교체가 잦고 소규모 제어반 반복 수요",
    ),
    TargetSector(
        "제약·바이오", weight=0.8,
        keywords=("제약", "바이오", "원료의약품", "GMP", "백신", "배양"),
        ksic_prefixes=("21",),
    ),
    TargetSector(
        "화학·정유", weight=0.75,
        keywords=("석유화학", "정유", "플랜트", "도료", "수지", "가스플랜트"),
        ksic_prefixes=("19", "20"),
    ),
    TargetSector(
        "철강·금속·비철", weight=0.8,
        keywords=("제철", "제강", "압연", "주조", "도금", "열처리", "비철"),
        ksic_prefixes=("24", "25"),
    ),
    TargetSector(
        "시멘트·요업·레미콘", weight=0.7,
        keywords=("시멘트", "레미콘", "골재", "석회", "요업", "내화물"),
        ksic_prefixes=("23",),
    ),
    TargetSector(
        "제지·섬유", weight=0.6,
        keywords=("제지", "펄프", "지류", "염색", "섬유가공"),
        ksic_prefixes=("13", "17"),
    ),
    TargetSector(
        "물류·창고 자동화", weight=0.75,
        keywords=("물류센터", "자동창고", "컨베이어", "분류기", "소터",
                  "스태커", "저온창고", "냉동창고"),
        ksic_prefixes=("52",),
    ),
    TargetSector(
        "건설·플랜트 EPC", weight=0.9,
        keywords=("종합건설", "건설", "엔지니어링", "플랜트", "기계설비공사",
                  "전기공사", "토목", "시설공사"),
        ksic_prefixes=("41", "42"),
        note="판넬 제조사 입장에서 가장 전형적인 원청 유형",
    ),
    TargetSector(
        "공조·냉동·기계설비", weight=0.8,
        keywords=("공조", "냉동", "냉각탑", "항온항습", "덕트", "열원", "히트펌프"),
        ksic_prefixes=("291", "292"),
    ),
    TargetSector(
        "데이터센터·통신", weight=0.7,
        keywords=("데이터센터", "IDC", "전산실", "UPS", "무정전", "통신국사"),
        ksic_prefixes=("61", "63"),
    ),
    TargetSector(
        "자동차·기계부품", weight=0.65,
        keywords=("자동차부품", "프레스", "사출", "도장라인", "공작기계"),
        ksic_prefixes=("30", "29"),
    ),
    TargetSector(
        "농축산·스마트팜", weight=0.6,
        keywords=("스마트팜", "축사", "양돈", "양계", "온실", "곡물", "미곡종합처리장", "RPC"),
        ksic_prefixes=("01", "012"),
    ),
)

# --- 나라장터 검색 키워드 -----------------------------------------------------
#
# '판넬을 사가는 공사/용역' 을 잡는 키워드. 너무 일반적인 말(전기)만 쓰면
# 무관한 공고가 쏟아지므로 판넬이 실제로 들어가는 공종 위주로 구성했다.
BID_KEYWORDS: tuple[str, ...] = (
    "자동제어", "제어반", "배전반", "수배전반", "분전반", "MCC",
    "계장", "감시제어", "원격감시", "SCADA", "PLC",
    "전기계장", "자동화설비", "전기공사", "기계설비공사",
)

#: 업무 구분별 오퍼레이션 접미사 (물품/용역/공사)
BID_CATEGORIES: tuple[str, ...] = ("cnstwk", "servc", "thng")


@dataclass
class ScreenConfig:
    """탐색 1회분 설정."""

    incumbent: IndustryProfile = KC_GROUP
    sectors: tuple[TargetSector, ...] = TARGET_SECTORS
    keywords: tuple[str, ...] = BID_KEYWORDS
    categories: tuple[str, ...] = BID_CATEGORIES

    #: 이 등급 이상으로 겹치면 결과에서 제외. clear(0) / adjacent(1) / same_industry(2)
    max_overlap_rank: int = 1
    #: 이 거리(km)를 넘으면 근접 점수 0
    max_distance_km: float = 150.0
    #: 최근 며칠치 낙찰 이력을 볼지
    lookback_days: int = 180
    #: 낙찰 건수가 이보다 적으면 후보에서 뺀다 (일회성 업체 제거)
    min_awards: int = 1
    #: 발주기관(수요기관)도 후보에 넣을지
    include_demand_orgs: bool = True

    dart_api_key: str = ""
    g2b_service_key: str = ""

    weights: dict[str, float] = field(
        default_factory=lambda: {"sector": 40.0, "proximity": 25.0, "activity": 25.0, "profile": 10.0}
    )


def load_config(path: str | Path | None = None, **overrides) -> ScreenConfig:
    """기본 설정에 JSON 파일과 키워드 인자를 순서대로 덮어쓴다."""
    cfg = ScreenConfig()
    if path:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        cfg = _apply_json(cfg, raw)
    clean = {k: v for k, v in overrides.items() if v is not None}
    return replace(cfg, **clean) if clean else cfg


def _apply_json(cfg: ScreenConfig, raw: dict) -> ScreenConfig:
    patch: dict = {}
    if "incumbent" in raw:
        # 기존 원청은 '통째로 교체'한다. 빠진 항목을 KC그룹 기본값으로 메우면
        # 다른 그룹을 지정했는데 KC 계열사 규칙이 남아 오판정이 난다.
        inc = raw["incumbent"]
        patch["incumbent"] = IndustryProfile(
            name=inc.get("name", "기존 원청"),
            affiliate_prefixes=tuple(inc.get("affiliate_prefixes", ())),
            affiliate_names=tuple(inc.get("affiliate_names", ())),
            ksic_prefixes=tuple(inc.get("ksic_prefixes", ())),
            core_keywords=tuple(inc.get("core_keywords", ())),
            adjacent_keywords=tuple(inc.get("adjacent_keywords", ())),
        )
    if "sectors" in raw:
        patch["sectors"] = tuple(
            TargetSector(
                name=s["name"],
                keywords=tuple(s.get("keywords", ())),
                ksic_prefixes=tuple(s.get("ksic_prefixes", ())),
                weight=float(s.get("weight", 1.0)),
                note=s.get("note", ""),
            )
            for s in raw["sectors"]
        )
    for key in ("keywords", "categories"):
        if key in raw:
            patch[key] = tuple(raw[key])
    for key in ("max_overlap_rank", "min_awards", "lookback_days"):
        if key in raw:
            patch[key] = int(raw[key])
    if "max_distance_km" in raw:
        patch["max_distance_km"] = float(raw["max_distance_km"])
    if "include_demand_orgs" in raw:
        patch["include_demand_orgs"] = bool(raw["include_demand_orgs"])
    for key in ("dart_api_key", "g2b_service_key"):
        if key in raw:
            patch[key] = str(raw[key])
    if "weights" in raw:
        patch["weights"] = {**cfg.weights, **{k: float(v) for k, v in raw["weights"].items()}}
    return replace(cfg, **patch)
