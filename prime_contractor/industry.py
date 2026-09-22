"""업종 판정 - (1) 기존 원청과 겹치는가, (2) 판넬 수요가 있는 업종인가.

업종코드(KSIC)만으로 판정하면 DART 미등록 업체가 전부 빠지고, 상호·공고명
키워드만 쓰면 오탐이 많다. 둘을 합쳐 '코드가 있으면 코드 우선, 없으면 키워드'
로 판정한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from prime_contractor.models import Candidate, OverlapVerdict

#: 상호 표기 흔들림 제거용 (㈜/주식회사/공백/마침표)
_NAME_NOISE = re.compile(r"\(주\)|\(유\)|㈜|㈐|주식회사|유한회사|유한책임회사|\s|\.|,|·")


def normalize_name(name: str) -> str:
    """'㈜케이씨 텍' / '주식회사 케이씨텍' → '케이씨텍' 처럼 비교 가능한 형태로."""
    return _NAME_NOISE.sub("", name or "").upper()


@dataclass(frozen=True)
class IndustryProfile:
    """기존 원청(= 피하고 싶은 쪽)의 업종 정의."""

    name: str
    #: 그룹 계열사를 이름으로 잡아내는 접두사. KC그룹은 계열사 상호가 대부분 '케이씨~'.
    affiliate_prefixes: tuple[str, ...] = ()
    #: 접두사로 안 걸리는 계열사는 여기에 직접 적는다.
    affiliate_names: tuple[str, ...] = ()
    #: 같은 업종으로 볼 KSIC 코드 접두사.
    ksic_prefixes: tuple[str, ...] = ()
    #: 업종명/상호/공고명에 이게 있으면 같은 시장으로 본다.
    core_keywords: tuple[str, ...] = ()
    #: 직접 경쟁은 아니지만 전방·후방으로 맞물리는 영역.
    adjacent_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetSector:
    """자동제어 판넬 수요가 나오는 업종. weight 가 클수록 판넬 물량이 많다."""

    name: str
    #: 실수요를 가리키는 말. '정수장', '소각로'처럼 현장이 특정되는 단어.
    keywords: tuple[str, ...]
    #: 업체의 '형태'만 알려주는 말. '건설', '엔지니어링'처럼 상호에 흔해서
    #: 이것만으로 업종을 정하면 종합건설사가 전부 한 칸에 몰린다.
    weak_keywords: tuple[str, ...] = ()
    ksic_prefixes: tuple[str, ...] = ()
    #: 업종코드 일치에 줄 무게. 업종코드는 그 회사가 '무엇을 만드는지'지
    #: '누구에게 파는지'가 아니라서 수주 근거보다 약하게 잡는다. 41·42(건설·
    #: 전기공사업)처럼 시공사면 다 갖고 있는 코드는 더 낮춘다.
    ksic_strength: float = 0.6
    weight: float = 1.0
    note: str = ""


def _hit(text: str, words: tuple[str, ...]) -> list[str]:
    up = text.upper()
    return [w for w in words if w.upper() in up]


def _ksic_hit(code: str, prefixes: tuple[str, ...]) -> str:
    code = (code or "").strip()
    if not code:
        return ""
    for p in prefixes:
        if code.startswith(p):
            return p
    return ""


def judge_overlap(cand: Candidate, incumbent: IndustryProfile) -> OverlapVerdict:
    """후보가 기존 원청과 업종이 겹치는지 판정한다.

    affiliate      - 같은 그룹 계열사로 보인다 (상호 접두/직접 지정)
    same_industry  - KSIC 코드 또는 핵심 키워드가 일치
    adjacent       - 전후방 연관 키워드만 일치
    clear          - 겹치지 않는다
    """
    norm = normalize_name(cand.name)
    reasons: list[str] = []

    for prefix in incumbent.affiliate_prefixes:
        if norm.startswith(normalize_name(prefix)):
            return OverlapVerdict("affiliate", [f"상호가 '{prefix}'(으)로 시작 - {incumbent.name} 계열 가능성"])
    for fixed in incumbent.affiliate_names:
        if norm == normalize_name(fixed):
            return OverlapVerdict("affiliate", [f"{incumbent.name} 계열사 목록에 등재된 상호"])

    code_hit = _ksic_hit(cand.ksic_code, incumbent.ksic_prefixes)
    if code_hit:
        reasons.append(f"업종코드 {cand.ksic_code} 가 {incumbent.name} 업종({code_hit}~)에 속함")

    core = _hit(cand.haystack, incumbent.core_keywords)
    if core:
        reasons.append("핵심 키워드 일치: " + ", ".join(core[:4]))

    if code_hit or core:
        return OverlapVerdict("same_industry", reasons)

    adj = _hit(cand.haystack, incumbent.adjacent_keywords)
    if adj:
        return OverlapVerdict("adjacent", ["연관 키워드 일치: " + ", ".join(adj[:4])])

    return OverlapVerdict("clear", ["기존 원청과 겹치는 신호 없음"])


#: match_sector 가 낼 수 있는 최대 strength (정규화용)
#: 업종코드 0.6 + 수주 1.2 + 상호 0.5 + 일반어 0.25
MAX_SECTOR_STRENGTH = 2.55


def match_sector(cand: Candidate, sectors: tuple[TargetSector, ...]) -> tuple[str, float, list[str]]:
    """판넬 수요 업종 중 가장 잘 맞는 것을 고른다. (업종명, 강도, 근거).

    근거마다 무게가 다르다. **무엇을 수주했는지**(공고명·수요기관)가 가장 세고,
    상호는 약하게, '건설' 같은 일반 명사는 더 약하게 본다. 이렇게 안 하면
    '○○종합건설'이 실제로 정수장 일을 해도 전부 건설업으로 뭉뚱그려진다.
    """
    demand, identity = cand.demand_text, cand.identity_text
    best: tuple[float, TargetSector | None, list[str]] = (0.0, None, [])

    for sec in sectors:
        why: list[str] = []
        strength = 0.0

        if _ksic_hit(cand.ksic_code, sec.ksic_prefixes):
            strength += sec.ksic_strength
            why.append(f"업종코드 {cand.ksic_code}")

        demand_hits = _hit(demand, sec.keywords)
        if demand_hits:
            strength += min(len(demand_hits), 3) / 3 * 1.2
            why.append("수주: " + ", ".join(demand_hits[:3]))

        identity_hits = _hit(identity, sec.keywords)
        if identity_hits:
            strength += min(len(identity_hits), 2) / 2 * 0.5
            why.append("상호/업종명: " + ", ".join(identity_hits[:2]))

        weak_hits = _hit(demand + " " + identity, sec.weak_keywords)
        if weak_hits:
            strength += min(len(weak_hits), 2) / 2 * 0.25
            why.append("일반: " + ", ".join(weak_hits[:2]))

        if strength == 0:
            continue
        score = strength * sec.weight
        if score > best[0]:
            best = (score, sec, why)

    if best[1] is None:
        return "", 0.0, []
    return best[1].name, best[0], best[2]
