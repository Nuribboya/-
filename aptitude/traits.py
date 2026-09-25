"""홀랜드(Holland) RIASEC 진로적성 6개 유형 정의."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Trait:
    code: str
    name: str
    description: str


TRAITS: dict[str, Trait] = {
    "R": Trait(
        "R",
        "실재형(Realistic)",
        "손과 도구, 기계를 다루거나 몸을 움직여 눈에 보이는 결과물을 만드는 것을 선호합니다.",
    ),
    "I": Trait(
        "I",
        "탐구형(Investigative)",
        "관찰·분석·논리적 사고로 현상의 원리를 깊이 파고드는 것을 선호합니다.",
    ),
    "A": Trait(
        "A",
        "예술형(Artistic)",
        "틀에 얽매이지 않는 자유로운 발상과 창작·표현 활동을 선호합니다.",
    ),
    "S": Trait(
        "S",
        "사회형(Social)",
        "사람을 가르치고 돕고 소통하는 활동에서 보람을 느낍니다.",
    ),
    "E": Trait(
        "E",
        "진취형(Enterprising)",
        "목표를 세우고 사람들을 설득·주도하여 성과를 만드는 것을 선호합니다.",
    ),
    "C": Trait(
        "C",
        "관습형(Conventional)",
        "규칙과 체계 속에서 자료를 정확하게 다루고 정리하는 것을 선호합니다.",
    ),
}

TRAIT_ORDER: list[str] = ["R", "I", "A", "S", "E", "C"]
