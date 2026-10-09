"""공장 찾기 — 판넬을 실제로 쓰는 '규모 있는 공장' 자체를 찾는다.

나라장터는 '판넬 일감이 나온 곳'(공사를 따낸 회사)을 보여 주는데, 그중엔 판넬을
직접 만드는 경쟁사도 섞인다. 판넬을 실제로 쓰는 쪽은 공장이다 — 라인 증설·개조·
교체 때마다 제어반이 들어가고, 기계·장비를 만드는 공장은 기계 하나마다 제어반을
반복해서 밖에 맡긴다. 이런 곳은 민간 거래라 나라장터에 안 나오고 대부분 비상장이다.

그래서 한국산업단지공단의 '전국 등록공장 현황'(공공데이터포털·팩토리온, CSV/엑셀
— 회사명·생산품·공장주소 등)을 읽어, 거리 안의 공장을 분야별로 나누고 규모로
거른다. 규모는 파일에 종업원 수·면적 칸이 있으면 그걸로, 없으면 DART 에 등록된
회사(외부감사 대상, 대략 자산 100억 이상)인지로 본다. 판넬 업체(경쟁사)는 뺀다.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from prime_contractor.geo import distance_from_home
from prime_contractor.industry import normalize_name
from prime_contractor.models import Candidate

#: 기계·장비 제작 분야 — 기계마다 제어반이 들어가 반복 수요가 있다.
#: (분야, 생산품에 이 말이 있으면). 위에서부터 먼저 맞는 분야로 묶는다.
MACHINE_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("포장·충진기계", ("포장기", "포장기계", "충진", "충전기", "실링기", "라벨러", "랩핑")),
    ("식품기계", ("식품기계", "식품가공기", "살균기", "제과기", "제빵기", "오븐", "튀김기",
                "세척기", "탈수기", "조리기")),
    ("컨베이어·물류설비", ("컨베이어", "이송장치", "이송기", "리프트", "크레인", "호이스트",
                       "자동창고", "스태커", "분류기", "소터", "적재기")),
    ("환경·수처리설비", ("집진기", "집진", "수처리", "폐수처리", "정화장치", "탈취", "소각로",
                      "스크러버", "여과기", "탈수설비")),
    ("자동화·로봇", ("자동화설비", "자동화장비", "자동화기계", "로봇", "공장자동화", "FA설비")),
    ("공작·산업기계", ("공작기계", "프레스", "사출기", "성형기", "절단기", "가공기", "용접기",
                     "절곡기", "선반", "밀링", "연삭기", "산업기계", "산업용기계")),
    ("열·공조설비", ("보일러", "건조기", "냉동기", "냉각기", "공조기", "열교환기", "히터",
                   "항온항습기", "칠러")),
    ("펌프·송풍·유체기계", ("펌프", "송풍기", "블로워", "압축기", "컴프레서", "교반기", "믹서",
                        "분쇄기", "선별기")),
)
GENERIC_MACHINE = ("기타 기계·장비", ("기계", "장비", "설비", "장치", "플랜트"))

#: 일반 제조 공장 분야와 경기를 덜 타는 정도(0~1, config.py 의 steady 와 같은 눈금).
#: 이런 공장은 라인 증설·개조·유지보수 때 판넬이 들어간다.
PLANT_FIELDS: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("식품·음료", ("식품", "음료", "제과", "제빵", "육가공", "유가공", "김치", "소스", "주류",
                 "사료", "떡", "면류", "냉동식품", "커피", "두부", "장류", "도시락"), 0.9),
    ("제약·바이오·화장품", ("의약", "제약", "바이오", "화장품", "건강기능", "백신", "원료의약",
                       "의료기기"), 0.9),
    ("환경·재활용", ("재활용", "폐기물", "재생원료", "재생"), 0.9),
    ("화학·플라스틱·고무", ("화학", "수지", "플라스틱", "고무", "도료", "접착제", "필름", "비닐",
                       "세제", "합성"), 0.5),
    ("섬유·제지·인쇄", ("섬유", "원단", "염색", "제지", "골판지", "인쇄", "포장재", "종이"), 0.5),
    ("금속·철강", ("철강", "주물", "주조", "단조", "도금", "열처리", "강관", "알루미늄", "금속",
                 "판금"), 0.4),
    ("비금속·건자재", ("시멘트", "레미콘", "콘크리트", "유리", "도자기", "석재", "벽돌", "건자재"), 0.4),
    ("자동차·운송부품", ("자동차", "차량", "타이어"), 0.3),
    ("전자·반도체", ("반도체", "디스플레이", "전자", "PCB", "인쇄회로", "센서", "LED"), 0.2),
)
#: 기계·장비 제작은 고객 공장들의 설비 투자에 따라 오르내린다 — 중간.
MACHINE_STEADY = 0.6
OTHER_STEADY = 0.6

#: 판넬을 만드는 곳 — 고객이 아니라 경쟁사.
COMPETITOR_WORDS = ("배전반", "분전반", "제어반", "수배전", "MCC", "판넬", "패널",
                    "전기제어장치", "전동기제어", "계장반", "큐비클")
#: 기계 제작사만 볼 때, 이 말만 있고 '기계를 만든다'는 말이 없으면 부품·소재 업체로 본다.
PART_WORDS = ("부품", "부분품", "소재", "금형", "베어링", "볼트", "너트", "스프링", "가스켓")

#: '규모 있는 공장' 기준 — 하나라도 넘으면.
MIN_EMPLOYEES = 30
MIN_AREA_M2 = 3_000

#: 머리글에서 칸을 찾는 말. 파일마다 이름이 조금씩 달라서 여러 개를 본다.
COLUMN_HINTS = {
    "name": ("회사명", "업체명", "기업명", "상호"),
    "products": ("생산품", "주생산품", "생산제품", "제품"),
    "address": ("공장주소", "공장소재지", "소재지", "주소"),
    "industry": ("업종명", "업종"),
    "ceo": ("대표자", "대표"),
    "phone": ("전화번호", "전화", "연락처"),
    "employees": ("종업원합계", "종업원수", "종업원", "근로자수", "근로자", "인원"),
    "area": ("제조시설면적", "공장면적", "건축면적", "용지면적", "면적"),
}


def classify(products: str, industry: str = "") -> tuple[str, bool, float]:
    """(분야, 기계·장비 제작사인가, 경기를 덜 타는 정도). 판넬 업체·빈 칸이면 분야가 ''."""
    text = f"{products} {industry}"
    if not text.strip() or any(w in text for w in COMPETITOR_WORDS):
        return "", False, 0.0
    for field, words in MACHINE_FIELDS:
        if any(w in text for w in words):
            return field, True, MACHINE_STEADY
    for field, words, steady in PLANT_FIELDS:
        if any(w in text for w in words):
            return field, False, steady
    if any(w in text for w in GENERIC_MACHINE[1]) and not _parts_only(text):
        return GENERIC_MACHINE[0], True, MACHINE_STEADY
    return "기타 제조", False, OTHER_STEADY


def _parts_only(text: str) -> bool:
    """'기계부품'·'자동차 부품'처럼 부품만 만드는 곳."""
    if not any(w in text for w in PART_WORDS):
        return False
    stripped = text
    for w in PART_WORDS:
        stripped = stripped.replace(w, " ")
    stripped = stripped.replace("기계", " ")
    return not any(w in stripped for w in ("장비", "설비", "장치", "플랜트"))


def steady_text(steady: float) -> str:
    return "덜 탐" if steady >= 0.8 else ("많이 탐" if steady <= 0.4 else "보통")


def read_factory_file(path: str | Path) -> list[dict[str, str]]:
    """등록공장 CSV(또는 엑셀)를 {칸: 값} 줄 목록으로. 칸 이름은 COLUMN_HINTS 키."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        rows = _xlsx_rows(path)
    else:
        rows = list(csv.reader(io.StringIO(_decode(path.read_bytes()))))
    header_at, columns = _find_header(rows)
    if header_at is None:
        raise ValueError("공장 목록에서 '회사명'·'주소' 머리글을 찾지 못했습니다. 공공데이터포털의 "
                         "'전국등록공장현황' 파일이 맞는지 확인해 주세요.")
    out = []
    for row in rows[header_at + 1:]:
        record = {key: (str(row[i]).strip() if i < len(row) and row[i] else "")
                  for key, i in columns.items()}
        if record.get("name"):
            out.append(record)
    return out


def has_size_columns(records: list[dict[str, str]]) -> bool:
    return any(r.get("employees") or r.get("area") for r in records[:200])


def find_factories(records: list[dict[str, str]],
                   within_km: float | None = 70.0) -> list[Candidate]:
    """거리 안의 공장(판넬 업체 제외)을 가까운 순으로. 규모로 거르는 건 is_sizable 로 따로."""
    found: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    for r in records:
        field, is_machine, steady = classify(r.get("products", ""), r.get("industry", ""))
        if not field:
            continue
        region, dist = distance_from_home(r.get("address", ""))
        if dist is None or (within_km is not None and dist > within_km):
            continue
        key = (r["name"], r.get("address", ""))
        if key in seen:                      # 같은 공장이 여러 줄(생산품별)로 나오기도 한다
            continue
        seen.add(key)
        cand = Candidate(
            name=r["name"], kind="maker" if is_machine else "plant",
            address=r.get("address", ""), ceo=r.get("ceo", ""), phone=r.get("phone", ""),
            products=r.get("products", ""), sector=field, sector_weight=steady,
            industry_name=r.get("industry", ""), region=region, distance_km=dist,
            employees=_number(r.get("employees", "")), area_m2=_number(r.get("area", "")),
            sources={"공장등록"})
        found.append(cand)
    return sorted(found, key=lambda c: c.distance_km)


def mark_dart_registered(factories: list[Candidate], corp_index: dict[str, str]) -> int:
    """DART 에 등록된 회사(외부감사 대상 — 규모 있는 회사)면 corp_code 를 채운다. 몇 곳인지."""
    hits = 0
    for c in factories:
        code = corp_index.get(normalize_name(c.name))
        if code:
            c.corp_code = code
            c.sources.add("DART")
            hits += 1
    return hits


def is_sizable(c: Candidate) -> bool:
    """규모 있는 공장인가 — 종업원·면적 기준을 넘거나 DART 등록 회사."""
    return (c.employees >= MIN_EMPLOYEES or c.area_m2 >= MIN_AREA_M2 or bool(c.corp_code))


def size_text(c: Candidate) -> str:
    bits = []
    if c.employees:
        bits.append(f"{c.employees}명")
    if c.area_m2:
        bits.append(f"{c.area_m2:,}㎡")
    if c.corp_code:
        bits.append("외감(DART)")
    return " · ".join(bits) or "-"


def _number(text: str) -> int:
    digits = re.sub(r"[^\d.]", "", text or "")
    try:
        return int(float(digits)) if digits else 0
    except ValueError:
        return 0


def _decode(raw: bytes) -> str:
    """공공데이터포털 CSV 는 대개 CP949(EUC-KR), 가끔 UTF-8 이다."""
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp949", errors="replace")


def _find_header(rows: list[list[str]]) -> tuple[int | None, dict[str, int]]:
    for at, row in enumerate(rows[:30]):
        cells = [str(c or "").strip() for c in row]
        columns: dict[str, int] = {}
        for key, hints in COLUMN_HINTS.items():
            # 앞의 말일수록 더 정확한 이름이라 그 순서로 찾는다('공장주소'가 '주소'보다 먼저).
            for hint in hints:
                hit = next((i for i, c in enumerate(cells)
                            if hint in c and i not in columns.values()), None)
                if hit is not None:
                    columns[key] = hit
                    break
        if "name" in columns and "address" in columns:
            return at, columns
    return None, {}


def _xlsx_rows(path: Path) -> list[list[str]]:
    from prime_contractor.xlsx import col_index, read_sheets
    grid = next(iter(read_sheets(path).values()), {})
    by_row: dict[int, dict[int, str]] = {}
    for (row, col), value in grid.items():
        text = f"{value:g}" if isinstance(value, float) else str(value)
        by_row.setdefault(row, {})[col_index(col)] = text
    rows = []
    for row in sorted(by_row):
        cells = by_row[row]
        width = max(cells) + 1
        rows.append([cells.get(i, "") for i in range(width)])
    return rows
