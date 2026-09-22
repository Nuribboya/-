"""원청 후보 탐색에 쓰는 공통 데이터 구조."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Award:
    """나라장터 개찰(낙찰) 1건. 원청 후보의 '발주/수주 활동' 근거가 된다."""

    notice_no: str = ""
    title: str = ""
    demand_org: str = ""          # 수요기관 (발주처)
    winner_name: str = ""         # 낙찰업체 = 원청 후보
    winner_bizno: str = ""
    amount: int = 0               # 낙찰금액(원)
    opening_dt: str = ""          # 개찰일시
    category: str = ""            # cnstwk(공사) / servc(용역) / thng(물품)
    keyword: str = ""             # 어떤 검색어로 걸렸는지


@dataclass
class OverlapVerdict:
    """기존 원청(KC그룹)과의 업종 중복 판정 결과."""

    level: str                    # affiliate | same_industry | adjacent | clear
    reasons: list[str] = field(default_factory=list)

    #: 숫자가 클수록 '겹친다'. 필터 임계값 비교에 쓴다.
    RANK = {"clear": 0, "adjacent": 1, "same_industry": 2, "affiliate": 3}

    @property
    def rank(self) -> int:
        return self.RANK[self.level]

    @property
    def label(self) -> str:
        return {
            "affiliate": "계열사 의심",
            "same_industry": "동일 업종",
            "adjacent": "인접 업종",
            "clear": "무관",
        }[self.level]


@dataclass
class Candidate:
    """원청 후보 1곳. 여러 소스에서 모은 정보를 합쳐 둔다."""

    name: str
    kind: str = "contractor"      # contractor(원청/시공사) | demand_org(발주기관)
    bizno: str = ""
    address: str = ""
    ksic_code: str = ""           # DART 업종코드(한국표준산업분류)
    industry_name: str = ""
    ceo: str = ""
    phone: str = ""
    fax: str = ""
    notice_url: str = ""          # 대표 공고 상세 페이지
    homepage: str = ""
    established: str = ""
    corp_code: str = ""           # DART 고유번호
    region: str = ""              # 주소에서 뽑은 시군구
    distance_km: float | None = None
    awards: list[Award] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)

    # 평가 결과 (scoring 단계에서 채워진다)
    overlap: OverlapVerdict | None = None
    sector: str = ""              # 매칭된 타깃 업종명
    sector_weight: float = 0.0
    score: float = 0.0            # = fitness.total (정렬·표시에 쓰는 대표값)
    grade: str = ""               # A~D
    fitness: object | None = None  # fitness.Fitness (순환 임포트를 피해 느슨하게 둔다)

    @property
    def award_count(self) -> int:
        return len(self.awards)

    @property
    def award_amount(self) -> int:
        return sum(a.amount for a in self.awards)

    @property
    def demand_text(self) -> str:
        """이 회사가 **어디에 납품하는지** 알려주는 텍스트 (공고명 + 수요기관).

        업종 판정의 주 근거다. 종합건설사라도 정수장 공사를 따냈으면 그 회사의
        판넬 수요는 수처리에서 나온다.
        """
        parts = [a.title for a in self.awards[:30]]
        parts += [a.demand_org for a in self.awards[:30]]
        return " ".join(p for p in parts if p)

    @property
    def identity_text(self) -> str:
        """회사가 **무엇을 자처하는지** (상호 + 등록 업종명). 보조 근거."""
        return " ".join(p for p in (self.name, self.industry_name) if p)

    @property
    def haystack(self) -> str:
        """겹침 판정용 전체 텍스트."""
        return " ".join(p for p in (self.identity_text, self.address, self.demand_text) if p)

    def merge(self, other: "Candidate") -> None:
        """같은 업체로 판정된 후보를 흡수한다. 빈 필드만 채운다."""
        for attr in (
            "bizno", "address", "ksic_code", "industry_name", "ceo",
            "phone", "fax", "notice_url", "homepage", "established",
            "corp_code", "region",
        ):
            if not getattr(self, attr) and getattr(other, attr):
                setattr(self, attr, getattr(other, attr))
        if self.distance_km is None:
            self.distance_km = other.distance_km
        seen = {(a.notice_no, a.winner_name) for a in self.awards}
        self.awards += [a for a in other.awards if (a.notice_no, a.winner_name) not in seen]
        self.sources |= other.sources
