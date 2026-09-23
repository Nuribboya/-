"""영업 진행 기록 — 찾은 회사를 실제로 원청으로 만들기까지.

원청 찾기는 목록을 줄 뿐이다. 그 목록에서 연락하고, 만나고, 서류를 내고,
등록되고, 첫 견적을 받기까지를 기록해야 목표에 얼마나 다가갔는지 알 수 있다.
기록이 쌓이면 '연락한 곳 중 몇 %가 등록까지 가나'를 실제 숫자로 낼 수 있어,
목표 역산에 쓰던 어림값을 바꿀 수 있다.

저장은 이 PC 안의 JSON 파일 하나다 (%APPDATA%\\PrimeFinder\\leads.json).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

#: 앞으로 가는 순서. 뒤 단계에 있으면 앞 단계는 지나온 것으로 본다.
STAGES = ("후보", "연락함", "미팅", "서류제출", "등록완료", "견적요청", "첫수주")
#: 순서 밖의 상태
PAUSED = "보류"
ALL_STAGES = STAGES + (PAUSED,)

#: 목표 역산의 전환율과 맞춰 보는 구간 (그 단계에 '도달한' 곳 수로 센다)
FUNNEL_POINTS = {"연락": "연락함", "미팅": "미팅", "등록": "등록완료", "수주": "첫수주"}


@dataclass
class Lead:
    name: str
    bizno: str = ""
    stage: str = "후보"
    history: list[tuple[str, str]] = field(default_factory=list)   # (단계, YYYY-MM-DD)
    next_action: str = ""
    next_date: str = ""                  # 다음 할 일 날짜
    payment_terms: str = ""              # 결제조건 메모 (현금 30일 / 어음 4개월 …)
    note: str = ""
    grade: str = ""
    sector: str = ""
    region: str = ""
    monthly_revenue: int = 0             # 첫 수주 뒤 실제로 나오는 월 발주

    @property
    def key(self) -> str:
        return self.bizno or self.name

    def reached(self, stage: str) -> bool:
        """이 단계까지 온 적이 있나 (지금 보류여도 지나온 건 센다)."""
        if stage not in STAGES:
            return False
        target = STAGES.index(stage)
        seen = [s for s, _ in self.history] + [self.stage]
        return any(s in STAGES and STAGES.index(s) >= target for s in seen)

    def is_overdue(self, today: date | None = None) -> bool:
        today = today or date.today()
        if not self.next_date or self.stage in ("첫수주",):
            return False
        try:
            return date.fromisoformat(self.next_date) < today
        except ValueError:
            return False


@dataclass
class LeadBook:
    leads: list[Lead] = field(default_factory=list)
    path: Path | None = None

    # --- 저장 ---------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "LeadBook":
        path = path or default_path()
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            # 깨진 파일 때문에 앱이 안 켜지면 안 된다. 옆에 백업해 두고 새로 시작.
            backup = path.with_suffix(".broken.json")
            try:
                path.replace(backup)
            except OSError:
                pass
            return cls(path=path)
        leads = []
        for row in raw.get("leads", []):
            row["history"] = [tuple(h) for h in row.get("history", [])]
            known = {k: v for k, v in row.items() if k in Lead.__dataclass_fields__}
            leads.append(Lead(**known))
        return cls(leads=leads, path=path)

    def save(self) -> Path:
        path = self.path or default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "leads": [asdict(l) for l in self.leads]},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)          # 쓰다가 꺼져도 원본이 반쯤 지워지지 않게
        return path

    # --- 편집 ---------------------------------------------------------------

    def find(self, key: str) -> Lead | None:
        return next((l for l in self.leads if l.key == key or l.name == key), None)

    def add(self, lead: Lead, today: date | None = None) -> tuple[Lead, bool]:
        """새로 넣으면 (lead, True), 이미 있으면 기존 것과 False."""
        existing = self.find(lead.key) or self.find(lead.name)
        if existing:
            return existing, False
        if not lead.history:
            lead.history.append((lead.stage, (today or date.today()).isoformat()))
        self.leads.append(lead)
        return lead, True

    def add_candidate(self, cand, today: date | None = None) -> tuple[Lead, bool]:
        """원청 찾기 결과의 후보를 그대로 넣는다."""
        return self.add(Lead(name=cand.name, bizno=cand.bizno, grade=cand.grade,
                             sector=cand.sector, region=cand.region), today)

    def move(self, key: str, stage: str, today: date | None = None,
             next_action: str = "", next_date: str = "") -> Lead:
        if stage not in ALL_STAGES:
            raise ValueError(f"'{stage}' 는 없는 단계입니다. 가능한 단계: {', '.join(ALL_STAGES)}")
        lead = self.find(key)
        if lead is None:
            raise KeyError(f"'{key}' 를 영업 기록에서 찾지 못했습니다.")
        if stage != lead.stage:
            lead.stage = stage
            lead.history.append((stage, (today or date.today()).isoformat()))
        if next_action:
            lead.next_action = next_action
        if next_date:
            lead.next_date = next_date
        elif stage != PAUSED:
            # 다음 할 일 날짜를 비워 두면 1주 뒤로 잡는다. 날짜 없는 할 일은 잊힌다.
            lead.next_date = ((today or date.today()) + timedelta(days=7)).isoformat()
        return lead

    def remove(self, key: str) -> bool:
        lead = self.find(key)
        if lead:
            self.leads.remove(lead)
        return lead is not None

    # --- 집계 ---------------------------------------------------------------

    def funnel(self) -> dict[str, int]:
        """각 구간에 도달한 곳 수."""
        return {label: sum(l.reached(stage) for l in self.leads)
                for label, stage in FUNNEL_POINTS.items()}

    def actual_rates(self, minimum: int = 5) -> dict[str, float | None]:
        """실제 전환율. 앞 단계가 minimum 곳 미만이면 아직 믿기 어려워 None."""
        f = self.funnel()
        pairs = [("연락 → 미팅", "연락", "미팅"),
                 ("미팅 → 협력업체 등록", "미팅", "등록"),
                 ("등록 → 첫 수주", "등록", "수주")]
        return {name: (f[b] / f[a] if f[a] >= minimum else None) for name, a, b in pairs}

    def contacts_in_week(self, today: date | None = None) -> int:
        """이번 주(월~일)에 '연락함' 으로 넘어간 곳 수."""
        today = today or date.today()
        monday = today - timedelta(days=today.weekday())
        count = 0
        for lead in self.leads:
            for stage, day in lead.history:
                if stage != "연락함":
                    continue
                try:
                    if monday <= date.fromisoformat(day) <= today:
                        count += 1
                        break
                except ValueError:
                    continue
        return count

    def overdue(self, today: date | None = None) -> list[Lead]:
        return sorted((l for l in self.leads if l.is_overdue(today)),
                      key=lambda l: l.next_date)

    def won(self) -> list[Lead]:
        return [l for l in self.leads if l.reached("첫수주")]

    def won_revenue(self) -> int:
        return sum(l.monthly_revenue for l in self.won())


def default_path() -> Path:
    from prime_contractor.app_settings import settings_path
    return settings_path().parent / "leads.json"


def progress_lines(book: LeadBook, plan, today: date | None = None) -> list[str]:
    """목표 대비 지금 어디쯤인지."""
    today = today or date.today()
    f = book.funnel()
    lines = ["진행 상황"]
    if plan is not None and not plan.already_there:
        lines.append(f"  새 원청    {len(book.won())} / {plan.clients_needed}곳")
        lines.append(f"  등록       {f['등록']} / {plan.registrations_needed}곳")
        lines.append(f"  연락       {f['연락']} / {plan.contacts_needed}곳")
        week_goal = max(1, round(plan.contacts_per_week + 0.49))
        done = book.contacts_in_week(today)
        mark = "✔" if done >= week_goal else f"{week_goal - done}곳 남음"
        lines.append(f"  이번 주 연락 {done} / {week_goal}곳  {mark}")
        if book.won_revenue():
            share = plan.anchor_revenue / (plan.anchor_revenue + book.won_revenue())
            lines.append(f"  새 원청 월 발주 합계 {book.won_revenue() / 1e4:,.0f}만원 "
                         f"→ 기존 원청 비중 약 {share * 100:.0f}%")
    else:
        lines.append(f"  연락 {f['연락']} · 미팅 {f['미팅']} · 등록 {f['등록']} · 수주 {f['수주']}")

    late = book.overdue(today)
    if late:
        lines.append("")
        lines.append(f"미룬 일 {len(late)}건")
        for l in late[:5]:
            lines.append(f"  · {l.name} — {l.next_action or l.stage + ' 다음 단계'} "
                         f"({l.next_date} 예정)")

    rates = book.actual_rates()
    measured = {k: v for k, v in rates.items() if v is not None}
    if measured:
        lines.append("")
        lines.append("실제 전환율 (기록 기준)")
        for name, value in measured.items():
            lines.append(f"  {name} {value * 100:.0f}%")
    return lines
