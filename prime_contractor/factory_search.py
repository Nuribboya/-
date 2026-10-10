"""공장 찾기 한 번 — 화면(gui.py)과 떼어 둔 본체.

    상장사 공장 (공공데이터포털 + DART 키)      ─┐
    비상장 공장 (전국등록공장현황 파일, 선택)    ─┼─ 합치기 → 거리(카카오, 선택) → 상장·그룹·투자 표시
                                                ─┘

- 상장사 공장: DART 상장사 중 제조업·지주회사와 그 자회사 이름으로 공장등록 API 를 묻는다.
- 비상장 공장: 파일이 있으면 거기서 직원 NONLISTED_MIN_STAFF 명 이상인 공장을 더한다.
  상장사만으로는 판넬 물량이 가장 꾸준한 100~300명 비상장 중견 공장이 빠진다.
- 둘 다 있으면 같은 공장(회사+주소)은 한 번만 남긴다.

say(text) 로 진행 상황을 알리고, 끝나면 SearchResult 를 돌려준다. 오류는 그대로 올린다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from prime_contractor.makers import (
    REFINE_MARGIN_KM, SUBSIDIARY_RATIO, affiliate_names, build_group_map, collect_by_name,
    company_key, drop_far_companies, find_factories, listed_names, mark_dart_registered,
    mark_groups, mark_investments, near_first, read_factory_file, refine_distances, sort_listed)
from prime_contractor.models import Candidate

#: 파일에서 더하는 비상장 공장의 최소 직원 수. 화면의 '직원 50명 이상'까지는 볼 수 있게.
NONLISTED_MIN_STAFF = 50
#: 카카오로 위치를 찾을 공장 수 상한 — 넘으면 읍·면·동 거리로 둔다(하루 한도·시간).
MAX_GEOCODE = 5000


@dataclass
class SearchOptions:
    data_key: str = ""
    dart_key: str = ""
    kakao_key: str = ""
    factory_file: str = ""
    within_km: float | None = 70.0
    incumbent: object | None = None
    cache_dir: Path = field(default_factory=lambda: Path.home() / ".cache" / "prime_contractor")

    @property
    def uses_api(self) -> bool:
        return bool(self.data_key and self.dart_key)

    def problem(self) -> str:
        """시작할 수 없는 이유. 없으면 ''."""
        if self.factory_file and not Path(self.factory_file).exists():
            return f"{self.factory_file}\n\n공장 목록 파일을 다시 고르거나 칸을 비워 두세요."
        if not self.uses_api and not self.factory_file:
            return ("'공공데이터포털 인증키'와 '기업정보 인증키'를 둘 다 넣어 주세요.\n\n"
                    "받는 곳은 도움말 탭에 적어 두었습니다.")
        return ""


@dataclass
class SearchResult:
    factories: list[Candidate]
    precise: bool = False          # 카카오로 번지 단위 거리를 쟀는가
    knows_listed: bool = False     # 상장사인지 확인했는가 (DART 키)
    has_unlisted: bool = False     # 비상장 공장도 들어 있는가 (파일)


def run_search(opts: SearchOptions, say=print, log=lambda _t: None) -> SearchResult:
    from prime_contractor.sources.dart import DartClient

    dart = DartClient(opts.dart_key, cache_dir=opts.cache_dir) if opts.dart_key else None
    coarse = (opts.within_km + REFINE_MARGIN_KM
              if opts.within_km is not None and opts.kakao_key else opts.within_km)

    listed, makers, heads, group_map = [], [], [], {}
    if dart:
        log("상장사 목록 읽기")
        listed = [row for row in dart.listed_companies if row[2]]
        say(f"상장사 {len(listed)}곳 중 제조업만 고릅니다 — 처음 한 번은 몇 분 걸리고, "
            "다음부터는 저장해 둔 걸 씁니다…")
        makers, heads = sort_listed(listed, dart.company,
                                    progress=lambda d, t: say(f"  업종 확인 {d}/{t}"))
        dart.save_company_cache()
        group_map = _groups(dart, makers, heads, say)

    factories: list[Candidate] = []
    if opts.uses_api:
        factories += _listed_plants(opts, dart, makers, heads, coarse, say, log)
    if opts.factory_file:
        factories = _merge(factories, _file_plants(opts, dart, listed, coarse, say))

    factories, precise = _refine(factories, opts, say)
    if dart:
        mark_dart_registered(factories, dart.corp_index, listed_names(listed))
        mark_groups(factories, group_map, listed, opts.incumbent)
        _investments(factories, dart, say)
    unlisted = sum(1 for c in factories if not (c.stock_code or c.group))
    say(f"거리 안의 공장 {len(factories)}곳 (상장사·계열사 {len(factories) - unlisted}곳, "
        f"비상장 {unlisted}곳). 연락은 그 공장 시설팀·공무팀에 하세요.")
    return SearchResult(factories, precise, knows_listed=bool(dart),
                        has_unlisted=bool(opts.factory_file))


# --- 단계별 ----------------------------------------------------------------------------

def _groups(dart, makers, heads, say) -> dict[str, str]:
    """상장사가 지분을 가진 회사 → 그 상장사 (계열 표시용)."""
    say(f"제조업 상장사 {len(makers)}곳. 이들과 지주회사 {len(heads) - len(makers)}곳의 "
        "계열사를 확인합니다…")
    failed: list[str] = []

    def progress(done: int, total: int) -> None:
        say(f"  계열사 확인 {done}/{total}")
        if done % 500 == 0:                    # 중간에 창을 닫아도 받은 데까지는 남긴다
            dart.save_investment_cache()

    group_map = build_group_map(heads, dart.investments, failed=failed, progress=progress)
    dart.save_investment_cache()
    if dart.invest_stopped:
        say(f"⚠ {dart.invest_stopped}")
    elif failed:
        say(f"  {len(failed)}곳은 DART 응답이 없어 건너뜀 — 다음에 누르면 다시 확인합니다.")
    return group_map


def _listed_plants(opts, dart, makers, heads, coarse, say, log) -> list[Candidate]:
    """상장사·자회사 이름으로 공장등록 API 를 물어 공장을 모은다."""
    from prime_contractor.sources.factory_api import FactoryApi, QuotaExceeded

    log("공장 API 준비")
    api = FactoryApi(opts.data_key, cache_dir=opts.cache_dir)
    # 묻는 순서 = 받는 순서. 하루 1,000번에서 끊겨도 가까운 곳부터 채워진다.
    near, far, dropped = near_first(makers, dart.company, opts.within_km)
    subsidiaries = affiliate_names(heads, dart.cached_investments, min_ratio=SUBSIDIARY_RATIO)
    say(f"  자회사 {len(subsidiaries)}곳의 본사 위치를 확인합니다…")
    subsidiaries, dropped_subs = drop_far_companies(subsidiaries, dart.lookup)
    dart.save_company_cache()
    queries = list(dict.fromkeys([name for name, _c, _s in near + far] + subsidiaries))
    say(f"  본사가 전라·경상·강원·제주인 {dropped + dropped_subs}곳은 뺐습니다.")
    say(f"  조회 순서: 본사가 거리 안인 제조 상장사 {len(near)}곳 → 나머지 제조 상장사 "
        f"{len(far)}곳 → 자회사(지분 50%↑, 금융·유통·서비스 제외) {len(subsidiaries)}곳")

    fresh = sum(1 for q in queries if not api.cached(q))
    if fresh and not api.verified:
        say("공장 조회 API 를 시험합니다…")
        try:
            say(f"  시험 조회 성공 — 삼성전자 공장 {api.check()}곳을 읽었습니다.")
        except QuotaExceeded:
            if not any(api.cached(q) for q in queries):
                raise
            say("  오늘 한도를 이미 다 써서, 받아 둔 공장만 보여 줍니다.")
    say(f"회사 {len(queries)}곳의 공장을 공공데이터포털에 묻습니다"
        + (f" (새로 {fresh}곳 — 처음엔 오래 걸립니다)…" if fresh else "…"))
    if fresh > 900:
        say("  공공데이터포털 개발계정은 하루 1,000번까지라 며칠에 나눠 받습니다 — "
            "받은 건 저장되니 다음 날 다시 누르면 이어서 받습니다.")
    # 이름만 같은 다른 회사를 거르려고 DART 대표자 이름을 같이 넘긴다(받아 둔 것만 쓴다).
    ceo_of = {name: _ceo(dart.company, code) for name, code, _s in near + far}
    for name in subsidiaries:
        ceo_of.setdefault(name, (dart.lookup(name) or {}).get("ceo_nm", ""))
    records, stopped = collect_by_name(queries, api.factories_of, ceo_of=ceo_of,
                                       progress=lambda d, t: say(f"  공장 조회 {d}/{t}"))
    api.save_cache()
    if stopped:
        say(f"⚠ {stopped} 받은 데까지만 보여 줍니다 — 내일 다시 누르면 이어서 받습니다.")
    if not records and api.sample_keys:
        say("공장 칸을 못 읽었습니다. 받은 칸 이름: " + ", ".join(api.sample_keys))
    if not records and not stopped:
        raise RuntimeError("공장을 하나도 받지 못했습니다. 이 창과 진행 기록을 캡처해 보내 주세요.")
    plants = find_factories(records, within_km=coarse)
    say(f"  상장사·자회사 공장 {len(plants)}곳 (거리 안).")
    return plants


def _file_plants(opts, dart, listed, coarse, say) -> list[Candidate]:
    """전국등록공장현황 파일에서 거리 안 공장. API 도 쓰면 비상장 NONLISTED_MIN_STAFF 명+ 만."""
    say("공장 목록 파일을 읽는 중입니다 (몇십만 줄이면 1분쯤 걸립니다)…")
    plants = find_factories(read_factory_file(opts.factory_file), within_km=coarse)
    if not opts.uses_api:
        say(f"  파일에서 거리 안 공장 {len(plants)}곳.")
        return plants
    if dart:
        mark_dart_registered(plants, dart.corp_index, listed_names(listed))
    kept = [c for c in plants if not c.stock_code and c.employees >= NONLISTED_MIN_STAFF]
    say(f"  파일에서 비상장 직원 {NONLISTED_MIN_STAFF}명 이상 공장 {len(kept)}곳을 더합니다.")
    return kept


def _merge(first: list[Candidate], second: list[Candidate]) -> list[Candidate]:
    """같은 공장(회사 + 주소 앞부분)은 먼저 것만 남긴다."""
    def key(c):
        return company_key(c.name), re.sub(r"\([^)]*\)|\s", "", c.address)[:24]
    seen = {key(c) for c in first}
    return first + [c for c in second if key(c) not in seen]


def _refine(factories, opts, say) -> tuple[list[Candidate], bool]:
    """카카오 키가 있으면 공장마다 번지 단위로 거리를 다시 잰다."""
    within = opts.within_km
    if not opts.kakao_key:
        return factories, False
    if len(factories) > MAX_GEOCODE:
        say(f"공장이 {len(factories):,}곳이라 번지 단위 거리는 건너뜁니다(읍·면·동 기준).")
        return [c for c in factories if within is None or c.distance_km <= within], False
    from prime_contractor.sources.kakao_geo import KakaoGeocoder
    geo = KakaoGeocoder(opts.kakao_key, opts.cache_dir)
    say(f"공장 {len(factories)}곳의 정확한 위치를 카카오 지도로 찾습니다 "
        "(처음 한 번만 몇 분, 다음부터는 저장해 둔 걸 씁니다)…")
    kept, exact, stopped = refine_distances(factories, geo, within,
                                            progress=lambda d, t: say(f"  위치 확인 {d}/{t}"))
    geo.save()
    if stopped:
        from prime_contractor.textutil import redact_secrets
        say(f"⚠ {redact_secrets(stopped)}")
    say(f"  {exact}곳은 번지 단위로, 나머지는 읍·면·동 중심으로 거리를 쟀습니다.")
    return kept, exact > 0


def _investments(factories, dart, say) -> None:
    """상장사 공장에 최근 시설투자 공시를 붙인다 — 라인을 늘리는 회사 = 판넬 일이 곧 나올 곳."""
    say("상장사들의 최근 2년 시설투자 공시를 확인합니다 (처음엔 몇 분, 일주일 동안 저장)…")
    count, stopped = mark_investments(factories, dart.facility_notices,
                                      progress=lambda d, t: say(f"  투자 공시 확인 {d}/{t}"))
    dart.save_notice_cache()
    if stopped:
        say(f"⚠ {stopped}")
    say(f"  최근 시설투자 공시가 있는 회사 {count}곳 — '투자' 칸에 날짜가 붙습니다.")


def _ceo(company, code: str) -> str:
    try:
        return company(code).get("ceo_nm", "") or ""
    except Exception:
        return ""
