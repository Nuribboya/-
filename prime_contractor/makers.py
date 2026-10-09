"""기계·장비 제작사 찾기 — 판넬이 '원래 계속' 필요한 회사.

나라장터는 '판넬 일감이 나온 곳'(공사를 따낸 회사)을 보여 준다. 그런데 포장기·
식품기계·컨베이어·환경설비·자동화 설비를 만드는 회사는 기계 하나마다 제어반이
하나씩 들어가고, 같은 사양을 반복해서 밖에 맡기는 곳이 많다. 작고 꾸준한 일의
전형인데 민간 거래라 나라장터에도, 상장사 목록에도 안 나온다(대부분 비상장
중소기업).

그래서 한국산업단지공단의 '전국 등록공장 현황'(공공데이터포털·팩토리온에서 CSV/
엑셀로 받는다 — 회사명·생산품·공장주소 등)을 읽어, 거리 안에 있고 생산품이 기계·
장비인 곳만 고른다. 판넬을 만드는 곳(경쟁사)과 부품만 만드는 곳은 뺀다.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from prime_contractor.geo import distance_from_home
from prime_contractor.models import Candidate

#: (분야, 생산품에 이 말이 있으면). 위에서부터 먼저 맞는 분야로 묶는다.
FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
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
    ("기타 기계·장비", ("기계", "장비", "설비", "장치", "플랜트")),
)

#: 판넬을 만드는 곳 — 고객이 아니라 경쟁사.
COMPETITOR_WORDS = ("배전반", "분전반", "제어반", "수배전", "MCC", "판넬", "패널",
                    "전기제어장치", "전동기제어", "계장반", "큐비클")
#: 이 말만 있고 '기계를 만든다'는 말이 없으면 부품·소재 업체로 본다(판넬 수요가 없다).
PART_WORDS = ("부품", "부분품", "소재", "금형", "베어링", "볼트", "너트", "스프링", "가스켓")

#: 머리글에서 칸을 찾는 말. 파일마다 이름이 조금씩 달라서 여러 개를 본다.
COLUMN_HINTS = {
    "name": ("회사명", "업체명", "기업명", "상호"),
    "products": ("생산품", "주생산품", "생산제품", "제품"),
    "address": ("공장주소", "공장소재지", "소재지", "주소"),
    "industry": ("업종명", "업종"),
    "ceo": ("대표자", "대표"),
    "phone": ("전화번호", "전화", "연락처"),
}


def classify(products: str, industry: str = "") -> str:
    """생산품으로 분야를 정한다. 기계·장비 제작사가 아니면 빈 문자열."""
    text = f"{products} {industry}"
    if not text.strip() or any(w in text for w in COMPETITOR_WORDS):
        return ""
    for field, words in FIELDS:
        if any(w in text for w in words):
            if field == "기타 기계·장비" and _parts_only(text):
                return ""
            return field
    return ""


def _parts_only(text: str) -> bool:
    """'기계부품'·'자동차 부품'처럼 부품만 만드는 곳."""
    if not any(w in text for w in PART_WORDS):
        return False
    stripped = text
    for w in PART_WORDS:
        stripped = stripped.replace(w, " ")
    stripped = stripped.replace("기계 ", " ").replace("기계", " ")
    return not any(w in stripped for w in ("장비", "설비", "장치", "플랜트"))


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
        record = {key: (row[i].strip() if i < len(row) and row[i] else "")
                  for key, i in columns.items()}
        if record.get("name"):
            out.append(record)
    return out


def find_makers(records: list[dict[str, str]], within_km: float | None = 70.0) -> list[Candidate]:
    """거리 안의 기계·장비 제작사만, 가까운 순."""
    makers: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    for r in records:
        field = classify(r.get("products", ""), r.get("industry", ""))
        if not field:
            continue
        region, dist = distance_from_home(r.get("address", ""))
        if dist is None or (within_km is not None and dist > within_km):
            continue
        key = (r["name"], r.get("address", ""))
        if key in seen:                      # 같은 공장이 여러 줄(생산품별)로 나오기도 한다
            continue
        seen.add(key)
        makers.append(Candidate(
            name=r["name"], kind="maker", address=r.get("address", ""), ceo=r.get("ceo", ""),
            phone=r.get("phone", ""), products=r.get("products", ""), sector=field,
            industry_name=r.get("industry", ""), region=region, distance_km=dist,
            sources={"공장등록"}))
    return sorted(makers, key=lambda c: c.distance_km)


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
        by_row.setdefault(row, {})[col_index(col)] = str(value)
    rows = []
    for row in sorted(by_row):
        cells = by_row[row]
        width = max(cells) + 1
        rows.append([cells.get(i, "") for i in range(width)])
    return rows
