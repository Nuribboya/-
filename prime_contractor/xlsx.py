"""엑셀(.xlsx) 읽기 — 외부 라이브러리 없이.

xlsx 는 XML 을 zip 으로 묶은 것이라 표준 라이브러리만으로 읽을 수 있다.
openpyxl 을 쓰면 편하지만, exe 로 묶을 때 용량이 커지고 설치 단계가 하나
늘어난다. 우리가 필요한 것은 '칸에 뭐가 적혀 있나'뿐이라 직접 읽는다.

서식·수식·차트는 무시한다. 수식 칸은 엑셀이 저장해 둔 계산 결과를 쓴다.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

#: 셀 주소 'BC12' → ('BC', 12)
_REF = re.compile(r"([A-Z]+)(\d+)")

#: 시트 하나 = {(행번호, 열이름): 값}. 값은 문자열 또는 float.
Grid = dict[tuple[int, str], object]


def col_index(letters: str) -> int:
    """'A'→1, 'Z'→26, 'AA'→27. 열 순서를 비교할 때 쓴다."""
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def read_sheets(path: str | Path) -> dict[str, Grid]:
    """{시트이름: 칸 목록}. 시트 순서는 엑셀에 보이는 순서 그대로."""
    with zipfile.ZipFile(Path(path)) as zf:
        shared = _shared_strings(zf)
        rel_target = _workbook_rels(zf)
        sheets: dict[str, Grid] = {}
        for name, rid in _sheet_order(zf):
            target = rel_target.get(rid)
            if not target:
                continue
            member = "xl/" + target.lstrip("/").removeprefix("xl/")
            if member not in zf.namelist():
                continue
            with zf.open(member) as fh:
                sheets[name] = _read_grid(ET.parse(fh).getroot(), shared)
    return sheets


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    with zf.open("xl/sharedStrings.xml") as fh:
        root = ET.parse(fh).getroot()
    # <si> 안에 <t> 가 여러 조각으로 나뉘어 있을 수 있다(서식이 섞인 글자).
    return ["".join(t.text or "" for t in si.iter(NS + "t")) for si in root]


def _workbook_rels(zf: zipfile.ZipFile) -> dict[str, str]:
    with zf.open("xl/_rels/workbook.xml.rels") as fh:
        root = ET.parse(fh).getroot()
    return {r.get("Id"): r.get("Target") for r in root}


def _sheet_order(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    with zf.open("xl/workbook.xml") as fh:
        root = ET.parse(fh).getroot()
    return [(s.get("name"), s.get(REL_NS + "id")) for s in root.iter(NS + "sheet")]


def _read_grid(sheet_root: ET.Element, shared: list[str]) -> Grid:
    grid: Grid = {}
    for cell in sheet_root.iter(NS + "c"):
        ref = cell.get("r") or ""
        match = _REF.match(ref)
        if not match:
            continue
        letters, row = match.group(1), int(match.group(2))
        kind = cell.get("t")

        if kind == "inlineStr":
            text = "".join(t.text or "" for t in cell.iter(NS + "t"))
            if text:
                grid[(row, letters)] = text
            continue

        node = cell.find(NS + "v")
        if node is None or node.text is None:
            continue
        raw = node.text
        if kind == "s":
            index = int(raw)
            if 0 <= index < len(shared):
                grid[(row, letters)] = shared[index]
        elif kind in (None, "n"):
            try:
                grid[(row, letters)] = float(raw)
            except ValueError:
                grid[(row, letters)] = raw
        else:                       # 불리언·에러 등은 글자 그대로 둔다
            grid[(row, letters)] = raw
    return grid
