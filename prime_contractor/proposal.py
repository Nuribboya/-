"""후보 회사별 맞춤 제안서 초안 만들기.

'영업 목록에 넣기'는 진행 상황을 기록하는 것이고, 여긴 그 회사에 실제로
보낼 수 있는 글을 만든다. 우리 회사 소개(CompanyProfile)와 그 후보의 낙찰
이력·적합도 근거(Candidate.fitness)를 엮어서, 왜 연락드리는지가 그 회사
얘기로 시작하게 한다. 어디까지나 규칙으로 짜맞춘 초안이라 보내기 전에
손봐야 한다 — 상호명 오탈자, 담당자 존칭 같은 건 사람이 확인해야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from prime_contractor.models import Award, Candidate


@dataclass
class CompanyProfile:
    """제안서에 넣을 우리 회사 정보. 화면 입력을 그대로 옮겨 담는다."""

    name: str = ""
    founded_year: str = ""
    certifications: str = ""      # "ISO9001, 벤처기업인증" 처럼 사람이 적은 그대로
    track_record: str = ""
    contact_name: str = ""
    contact_phone: str = ""


def _top_award(cand: Candidate) -> Award | None:
    """제안서 근거로 들 만한 대표 낙찰 1건 — 금액이 가장 큰 것."""
    return max(cand.awards, key=lambda a: a.amount, default=None)


def build_proposal(cand: Candidate, profile: CompanyProfile) -> str:
    """규칙 기반 한 장짜리 제안서 초안."""
    us = profile.name.strip() or "저희 회사"
    lines: list[str] = [f"{cand.name} 담당자님께", "", f"안녕하십니까, {us} 입니다."]

    top = _top_award(cand)
    if top and top.title:
        lines.append(
            f"{(top.demand_org or '발주처') + '의 ' if top.demand_org else ''}"
            f"「{top.title}」 관련하여 연락드립니다. 해당 공사에 들어가는 "
            "자동제어 판넬(배전반·제어반) 공급을 협의드리고 싶습니다."
        )
    else:
        lines.append("자동제어 판넬(배전반·제어반) 공급 협력을 제안드리고자 연락드립니다.")

    fitness = getattr(cand, "fitness", None)
    detail_lines = []
    if cand.region:
        detail_lines.append(f"  · 지역: {cand.region}")
    if fitness is not None and getattr(fitness, "headline", ""):
        detail_lines.append(f"  · 저희가 연락드리는 이유: {fitness.headline}")
    if detail_lines:
        lines += ["", f"[{cand.name} 관련 참고]", *detail_lines]

    profile_lines = []
    if profile.founded_year:
        profile_lines.append(f"  · 설립: {profile.founded_year}년")
    if profile.certifications:
        profile_lines.append(f"  · 보유 인증: {profile.certifications}")
    if profile.track_record:
        profile_lines.append(f"  · 주요 실적: {profile.track_record}")
    if profile_lines:
        lines += ["", f"[{us} 소개]", *profile_lines]

    lines += [
        "",
        "판넬 사양·수량이 확인되는 대로 견적 드리겠습니다. 편하신 시간에 연락 주시면",
        "자세히 안내드리겠습니다.",
    ]
    contact = " / ".join(p for p in (profile.contact_name, profile.contact_phone) if p)
    if contact:
        lines += ["", f"연락처: {contact}"]

    return "\n".join(lines)
