"""수집 → 후보 정리 → 보강 → 판정 → 순위."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from prime_contractor.config import ScreenConfig
from prime_contractor.geo import distance_from_home
from prime_contractor.industry import normalize_name
from prime_contractor.models import Award, Candidate
from prime_contractor.scoring import score_candidate, split_by_overlap
from prime_contractor.sources.sample import SAMPLE_COMPANY_INFO, sample_awards

log = logging.getLogger(__name__)

#: 발주기관 후보에서 빼는 이름 (판넬을 직접 사지 않는 기관)
_ORG_STOPWORDS = ("교육청", "학교", "대학교", "경찰", "소방", "법원", "우체국", "도서관")
#: 나라장터가 여러 기관 공동 구매에 붙이는 자리표시 이름. 실제 연락할 곳이 아닌데
#: 공사를 다 모아 '판넬 일감 1,000억'짜리 1등처럼 보였다.
_ORG_PLACEHOLDERS = ("각 수요기관", "각수요기관", "수요기관", "각 기관", "각기관")
#: 이 업무구분으로 낙찰받은 회사는 관공서에 물건(배전반 등)을 '파는' 쪽이라 우리한테
#: 판넬을 줄 원청이 아니라 경쟁사다. 후보에서 빼고 '관공서 판넬 시장'에 따로 담는다.
SUPPLY_CATEGORY = "물품"


@dataclass
class ScreenResult:
    passed: list[Candidate] = field(default_factory=list)
    excluded: list[Candidate] = field(default_factory=list)
    awards: list[Award] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: 관공서가 판넬·전기기기를 물품으로 직접 산 계약 — 누가 사고 누가 팔았나
    market: list[Award] = field(default_factory=list)
    #: 근처 대기업·중견 공장(상장사). 공사 이력이 없어 점수로 줄 세우지 않고 따로 둔다.
    factories: list[Candidate] = field(default_factory=list)

    @property
    def stats(self) -> dict[str, int]:
        return {"낙찰건수": len(self.awards),
                "후보": len(self.passed) + len(self.excluded),
                "통과": len(self.passed),
                "제외": len(self.excluded)}


def build_candidates(awards: list[Award], include_demand_orgs: bool = True) -> list[Candidate]:
    """낙찰 이력을 업체/기관 단위로 묶는다. 사업자번호 우선, 없으면 정규화 상호.

    물품 낙찰자는 관공서에 배전반을 파는 판넬 업체(경쟁사)라 원청 후보로 만들지
    않는다. 물품을 산 기관 쪽(수요기관)은 판넬을 직접 사는 곳이라 그대로 둔다.
    """
    by_key: dict[str, Candidate] = {}

    def _bucket(key: str, cand: Candidate) -> None:
        if key in by_key:
            by_key[key].merge(cand)
        else:
            by_key[key] = cand

    for a in awards:
        if a.winner_name and a.category != SUPPLY_CATEGORY:
            key = f"biz:{a.winner_bizno}" if a.winner_bizno else f"nm:{normalize_name(a.winner_name)}"
            _bucket(key, Candidate(name=a.winner_name, kind="contractor", bizno=a.winner_bizno,
                                   awards=[a], sources={"나라장터"}))
        if (include_demand_orgs and a.demand_org
                and a.demand_org.strip() not in _ORG_PLACEHOLDERS
                and not any(s in a.demand_org for s in _ORG_STOPWORDS)):
            key = f"org:{normalize_name(a.demand_org)}"
            _bucket(key, Candidate(name=a.demand_org, kind="demand_org",
                                   awards=[a], sources={"나라장터(수요기관)"}))
    return list(by_key.values())


def enrich(cands: list[Candidate], dart_client=None, offline_info: dict | None = None) -> list[str]:
    """DART(또는 샘플 정보)로 주소·업종코드를 채우고 거리까지 계산한다."""
    notes: list[str] = []
    hit = 0
    for c in cands:
        info = None
        if dart_client is not None and c.kind == "contractor":
            info = dart_client.lookup(c.name)
        if info is None and offline_info:
            info = offline_info.get(c.name)
        if info:
            hit += 1
            c.address = c.address or info.get("adres", "")
            c.ksic_code = c.ksic_code or info.get("induty_code", "")
            c.ceo = c.ceo or info.get("ceo_nm", "")
            c.homepage = c.homepage or info.get("hm_url", "")
            c.established = c.established or info.get("est_dt", "")
            c.corp_code = c.corp_code or info.get("corp_code", "")
            c.bizno = c.bizno or info.get("bizr_no", "")
            c.sources.add("DART" if dart_client is not None else "샘플정보")
        # 주소가 없으면 기관명에서라도 지역을 건진다 ('평택시 상하수도사업소' 등).
        # 회사 상호는 '세종전기'처럼 도시 이름이 위치와 상관없이 붙어 있어 엄격하게 본다.
        if c.address:
            region, dist = distance_from_home(c.address)
        else:
            region, dist = distance_from_home(c.name, company_name=c.kind == "contractor")
        c.region, c.distance_km = region, dist

    if dart_client is not None and hasattr(dart_client, "save_company_cache"):
        try:
            dart_client.save_company_cache()      # 다음 조회 때 같은 회사는 바로 나온다
        except OSError:
            pass
    contractors = sum(1 for c in cands if c.kind == "contractor")
    if contractors:
        notes.append(f"업종·주소 보강: {hit}/{contractors}개사 (DART 미등록 업체는 상호·공고명 기반으로만 판정)")
    return notes


def filter_sector(result: "ScreenResult", needle: str) -> None:
    """업종 이름에 needle 이 든 후보만 남긴다 (부분 일치). 뺀 것은 사유와 함께 제외 목록으로."""
    dropped = [c for c in result.passed if needle not in c.sector]
    result.passed = [c for c in result.passed if needle in c.sector]
    for c in dropped:
        if c.overlap:
            c.overlap.reasons.append(f"업종 '{c.sector or '미분류'}' 이(가) '{needle}' 와(과) 다름")
    result.excluded = dropped + result.excluded
    result.notes.append(f"업종 필터 '{needle}' 적용 → {len(result.passed)}곳")


#: 업종 스크리닝 모드의 배점. 낙찰 이력이 없는 모드라 '수주 활동'을 빼고
#: 업종 적합도와 거리로 나눈다.
INDUSTRY_WEIGHTS = {"sector": 45.0, "proximity": 35.0, "activity": 0.0, "profile": 20.0}


def run_industry_screen(cfg: ScreenConfig, dart_client, limit: int | None = None,
                        progress_every: int = 200) -> ScreenResult:
    """나라장터 낙찰 이력과 무관하게, **업종코드로** 원청 후보를 훑는다.

    나라장터에는 공공 발주만 올라온다. 반도체 팹처럼 민간이 발주하는 물량은
    아예 안 잡히므로, 그쪽 원청을 찾으려면 업종 자체로 훑는 수밖에 없다.
    DART 상장사 전체를 돌며 업종코드·주소를 보고 후보를 만든다.
    """
    from dataclasses import replace as _replace

    cfg = _replace(cfg, weights={**cfg.weights, **INDUSTRY_WEIGHTS}, min_awards=0)
    result = ScreenResult()

    companies = dart_client.listed_companies
    if limit:
        companies = companies[:limit]
    result.notes.append(f"DART 상장사 {len(companies)}곳의 업종코드·주소를 확인합니다 "
                        f"(첫 실행은 몇 분 걸리고, 이후에는 캐시를 씁니다)")

    cands: list[Candidate] = []
    for i, (name, corp_code, stock) in enumerate(companies, 1):
        if progress_every and i % progress_every == 0:
            log.info("  %s/%s 확인", i, len(companies))
        try:
            info = dart_client.company(corp_code)
        except Exception as exc:                     # 한 곳 실패로 전체를 멈추지 않는다
            log.debug("%s(%s) 개황 실패: %s", name, corp_code, exc)
            continue
        cands.append(Candidate(
            name=info.get("corp_name") or name,
            kind="contractor",
            bizno=info.get("bizr_no", ""),
            address=info.get("adres", ""),
            ksic_code=info.get("induty_code", ""),
            ceo=info.get("ceo_nm", ""),
            homepage=info.get("hm_url", ""),
            established=info.get("est_dt", ""),
            corp_code=corp_code,
            sources={"DART"},
        ))
    dart_client.save_company_cache()

    for c in cands:
        c.region, c.distance_km = (distance_from_home(c.address) if c.address
                                   else distance_from_home(c.name, company_name=True))
        score_candidate(c, cfg)

    # 업종이 전혀 안 잡히는 곳(금융·유통 등)은 후보로 볼 이유가 없다.
    matched = [c for c in cands if c.sector]
    result.notes.append(f"업종이 타깃과 맞는 곳 {len(matched)}곳")
    result.passed, result.excluded = split_by_overlap(matched, cfg)
    return result


def panel_market(awards: list[Award]) -> list[Award]:
    """관공서가 판넬·전기기기를 물품으로 산 계약만, 최근 것부터."""
    from prime_contractor.fitness import _match_level
    picked = [a for a in awards if a.category == SUPPLY_CATEGORY
              and _match_level(a.title) in ("direct", "process")]
    return sorted(picked, key=lambda a: a.opening_dt or "", reverse=True)


def steady_of(cand: Candidate, cfg: ScreenConfig) -> float:
    """후보 업종이 경기를 얼마나 덜 타나 (업종을 모르면 보통 0.6)."""
    return next((s.steady for s in cfg.sectors if s.name == cand.sector), 0.6)


def run_factory_screen(cfg: ScreenConfig, dart_client) -> list[Candidate]:
    """근처 대기업·중견 공장 목록 — 단가가 세고, 공장 유지보수 일이 꾸준히 나온다.

    나라장터엔 공공 공사만 있어 민간 공장은 안 잡힌다. 상장사를 업종코드·주소로
    훑어(run_industry_screen) 거리 안에 있는 곳만 남긴다. 공사 이력이 없어 적합도
    점수가 낮게 나오므로 70점으로 거르지 않고, 경기를 덜 타는 업종 → 가까운 순으로
    줄 세운다. 연락할 곳은 본사 구매팀보다 그 공장 시설·공무팀이 빠르다.
    """
    found = run_industry_screen(cfg, dart_client)
    near = [c for c in found.passed if c.distance_km is not None]    # 주소 모르면 '근처'가 아니다
    return sorted(near, key=lambda c: (
        -steady_of(c, cfg), c.distance_km if c.distance_km is not None else 9999.0))


def run_screen(cfg: ScreenConfig, offline: bool = False,
               g2b_client=None, dart_client=None, nts_client=None) -> ScreenResult:
    """전체 파이프라인 1회 실행."""
    result = ScreenResult()

    if offline:
        result.awards = sample_awards()
        result.notes.append("⚠ 오프라인 샘플 데이터입니다. 실제 업체가 아니므로 영업에 쓰지 마세요.")
    else:
        if g2b_client is None:
            raise ValueError("온라인 모드에는 G2B 클라이언트가 필요합니다.")
        result.awards = g2b_client.fetch_awards(
            keywords=cfg.keywords, categories=cfg.categories, lookback_days=cfg.lookback_days
        )
        result.notes.append(
            f"나라장터 최근 {cfg.lookback_days}일 / 키워드 {len(cfg.keywords)}개 / "
            f"{'·'.join(cfg.categories)} → 낙찰 {len(result.awards)}건"
        )
        if getattr(g2b_client, "keyword_fallback", None):
            result.notes.append(
                "공고명 검색이 지원되지 않아 전체를 받아 직접 걸렀습니다: "
                + ", ".join(sorted(g2b_client.keyword_fallback))
            )

    cands = build_candidates(result.awards, include_demand_orgs=cfg.include_demand_orgs)
    result.market = panel_market(result.awards)
    suppliers = {a.winner_name for a in result.awards
                 if a.category == SUPPLY_CATEGORY and a.winner_name}
    if suppliers:
        result.notes.append(
            f"물품으로 관공서에 납품한 {len(suppliers)}곳은 판넬을 '파는' 쪽(경쟁사일 수 있음)이라 "
            f"후보에서 뺐습니다. 관공서 판넬 구매 {len(result.market)}건은 "
            "[관공서 판넬 시장]에서 볼 수 있습니다.")

    result.notes += enrich(cands, dart_client=dart_client,
                           offline_info=SAMPLE_COMPANY_INFO if offline else None)

    if nts_client is not None and not offline:
        from prime_contractor.sources.nts import apply_statuses
        checked, dead = apply_statuses(cands, nts_client.statuses([c.bizno for c in cands]))
        if checked:
            tail = " — 목록에서 뺐습니다" if dead and cfg.drop_closed_businesses else ""
            result.notes.append(f"국세청에서 {checked}곳의 사업자 상태를 확인했습니다. "
                                f"폐업 {dead}곳{tail}")

    for c in cands:
        score_candidate(c, cfg)
    result.passed, result.excluded = split_by_overlap(cands, cfg)
    return result
