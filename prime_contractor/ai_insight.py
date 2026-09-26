"""장부 숫자를 실제 AI(Gemini)에게 넘겨 한 번 더 생각해 보게 하기.

diagnosis.py 의 규칙 진단은 미리 정해둔 조건만 본다("적자면 이거", "비율
넘으면 저거"). 여기는 그 규칙이 놓칠 수 있는 패턴을 매번 새로 판단해
보라고, 정리한 숫자를 언어모델에 그대로 던진다.

인터넷과 무료 API 키가 있어야 하고, 없거나 호출이 실패하면 빈 결과와
오류 문구를 돌려준다 — 규칙 기반 진단은 이것 없이도 그대로 동작해야
하므로, 이 기능은 있으면 좋고 없어도 그만인 얹는 기능이다.
"""
from __future__ import annotations

from prime_contractor.breakeven import CostModel
from prime_contractor.diagnosis import Diagnosis
from prime_contractor.sales import SalesBook
from prime_contractor.sources.gemini import GeminiClient, GeminiError

#: 프롬프트에 넣을 최근 달 수. 너무 많이 넣으면 요청이 무거워지기만 한다.
MAX_MONTHS_IN_PROMPT = 12

_INSTRUCTION = (
    "작은 자동제어 판넬(배전반) 제조 회사의 매출 담당자입니다. 아래 숫자를 "
    "보고 무엇을 먼저 챙겨야 할지, 미리 정해둔 규칙으로는 못 잡을 수 있는 "
    "패턴이나 위험이 있으면 짧게 짚어 주세요. 규칙이 이미 짚은 것과 "
    "겹치는 얘기는 반복하지 말고, 숫자에 없는 걸 지어내지도 마세요. "
    "3~5문장, 실무자가 바로 알아들을 말로, 존댓말로 답해 주세요."
)


def build_prompt(book: SalesBook, diag: Diagnosis, cost: CostModel | None = None) -> str:
    lines = [_INSTRUCTION, "", "최근 월별 매출(만원):"]
    for m in book.sorted_months()[-MAX_MONTHS_IN_PROMPT:]:
        target = f" (목표 {m.target / 1e4:,.0f})" if m.target else ""
        lines.append(f"  {m.ym}: {m.revenue / 1e4:,.0f}{target}")

    if cost:
        lines.append("")
        lines.append(f"월 고정비 {cost.monthly_fixed / 1e4:,.0f}만원, "
                     f"재료·외주비 비율 {cost.variable_ratio * 100:.0f}%, "
                     f"손익분기 매출 {cost.breakeven / 1e4:,.0f}만원")
    if diag.latest_month_profit is not None:
        lines.append(f"{diag.latest_month} 추정 손익: {diag.latest_month_profit / 1e4:+,.0f}만원")
    if diag.revenue_per_employee is not None:
        lines.append(f"직원당 매출(최근 평균): {diag.revenue_per_employee / 1e4:,.0f}만원/인")

    if diag.priorities:
        lines.append("")
        lines.append("규칙 기반 진단이 이미 짚은 것:")
        for p in diag.priorities:
            lines.append(f"  - {p.text}: {p.reason}")

    return "\n".join(lines)


def ask(book: SalesBook, diag: Diagnosis, api_key: str, cost: CostModel | None = None,
       model: str | None = None, client: GeminiClient | None = None) -> tuple[str, str]:
    """(본문, 오류) 튜플. 성공하면 오류가 빈 문자열, 실패하면 본문이 빈 문자열."""
    if client is None:
        if not api_key:
            return "", "Gemini API 키가 없습니다."
        try:
            client = GeminiClient(api_key, model=model) if model else GeminiClient(api_key)
        except GeminiError as exc:
            return "", str(exc)

    prompt = build_prompt(book, diag, cost)
    try:
        text = client.generate(prompt)
    except GeminiError as exc:
        return "", str(exc)
    return text, ""
