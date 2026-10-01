"""엑셀(.xlsx) 쓰기 — 외부 라이브러리 없이, 보기 좋은 서식만 조금.

읽기(xlsx.py)처럼 표준 라이브러리로 XML 을 zip 에 담는다. openpyxl 을 넣으면
exe 가 커지고 설치 단계가 늘어난다. 필요한 건 '머리글 굵게·칸 너비·첫 줄
고정·필터·숫자 쉼표·등급 색' 정도라 직접 쓴다.
"""
from __future__ import annotations

import math
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

# 서식 번호 — styles.xml 의 cellXfs 순서와 맞춘다.
TEXT, HEADER, WRAP, MONEY, DECIMAL, GRADE_A, GRADE_B, GRADE_C, GRADE_D, CENTER = range(10)
GRADE_STYLE = {"A": GRADE_A, "B": GRADE_B, "C": GRADE_C, "D": GRADE_D}


@dataclass
class Column:
    header: str
    width: float
    style: int = TEXT


@dataclass
class Sheet:
    name: str
    columns: list[Column]
    rows: list[list[object]] = field(default_factory=list)
    #: 칸마다 서식을 따로 줄 때 {(행 번호 0부터, 열 번호 0부터): 서식}. 없으면 열 서식.
    cell_styles: dict[tuple[int, int], int] = field(default_factory=dict)
    #: 인쇄: A4 가로, 가로 한 장 폭에 맞춤, 장마다 첫 줄(머리글) 반복.
    landscape: bool = True


#: 한 줄 높이(pt). 글자 10pt 기준.
LINE_HEIGHT = 14


def text_units(text: str) -> float:
    """엑셀 칸 너비 단위로 본 글자 폭. 한글·한자는 숫자 두 개 폭쯤 된다."""
    return sum(1.8 if unicodedata.east_asian_width(ch) in "WF" else 1.0 for ch in text)


def row_height(values: list[object], columns: list[Column]) -> float | None:
    """줄바꿈 칸이 몇 줄이 될지 어림해 행 높이를 정한다. 한 줄이면 None(기본 높이).

    엑셀은 줄바꿈 칸의 행 높이를 저장된 파일에서 늘 알아서 맞춰 주지 않는다 —
    그러면 인쇄할 때 글이 잘린다. 그래서 높이를 직접 적어 둔다.
    """
    lines = 1
    for value, col in zip(values, columns):
        if col.style != WRAP or not value:
            continue
        usable = max(col.width - 1.5, 4)
        count = sum(max(1, math.ceil(text_units(part) / usable))
                    for part in str(value).split("\n"))
        lines = max(lines, count)
    return None if lines == 1 else lines * LINE_HEIGHT + 4


def write_workbook(path: str | Path, sheets: list[Sheet]) -> Path:
    path = Path(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types(len(sheets)))
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("xl/workbook.xml", _workbook(sheets))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheets)))
        zf.writestr("xl/styles.xml", _STYLES)
        for i, sheet in enumerate(sheets, 1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", _sheet(sheet))
    return path


def _col_letter(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _cell(ref: str, value: object, style: int) -> str:
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, bool):
        value = "예" if value else "아니오"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet(sheet: Sheet) -> str:
    cols = "".join(f'<col min="{i}" max="{i}" width="{c.width}" customWidth="1"/>'
                   for i, c in enumerate(sheet.columns, 1))
    last = _col_letter(len(sheet.columns) - 1)
    rows = ['<row r="1">' + "".join(
        _cell(f"{_col_letter(i)}1", c.header, HEADER) for i, c in enumerate(sheet.columns))
        + "</row>"]
    for r, values in enumerate(sheet.rows):
        cells = []
        for i, col in enumerate(sheet.columns):
            value = values[i] if i < len(values) else ""
            style = sheet.cell_styles.get((r, i), col.style)
            cells.append(_cell(f"{_col_letter(i)}{r + 2}", value, style))
        height = row_height(values, sheet.columns)
        attrs = f' ht="{height}" customHeight="1"' if height else ""
        rows.append(f'<row r="{r + 2}"{attrs}>' + "".join(cells) + "</row>")
    end = len(sheet.rows) + 1
    orientation = "landscape" if sheet.landscape else "portrait"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
        # 첫 줄(머리글)을 고정해 내려도 무슨 칸인지 보이게.
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews>'
        f'<sheetFormatPr defaultRowHeight="18"/><cols>{cols}</cols>'
        f'<sheetData>{"".join(rows)}</sheetData>'
        f'<autoFilter ref="A1:{last}{end}"/>'
        # 인쇄: 여백 좁게, A4, 가로 한 장 폭에 맞추고 세로는 필요한 만큼, 쪽 번호.
        '<printOptions horizontalCentered="1"/>'
        '<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>'
        f'<pageSetup paperSize="9" orientation="{orientation}" fitToWidth="1" fitToHeight="0"/>'
        f'<headerFooter><oddHeader>&amp;L&amp;"맑은 고딕,굵게"{escape(sheet.name)}</oddHeader>'
        '<oddFooter>&amp;C&amp;P / &amp;N</oddFooter></headerFooter>'
        '</worksheet>')


def _content_types(count: int) -> str:
    sheets = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, count + 1))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f'{sheets}</Types>')


_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')


def _workbook(sheets: list[Sheet]) -> str:
    items = "".join(f'<sheet name="{escape(s.name[:31])}" sheetId="{i}" r:id="rId{i}"/>'
                    for i, s in enumerate(sheets, 1))
    # 필터는 시트마다 숨은 이름(_FilterDatabase)이 있어야 엑셀이 경고 없이 연다.
    names = "".join(
        f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
        f"'{escape(s.name[:31])}'!$A$1:${_col_letter(len(s.columns) - 1)}${len(s.rows) + 1}"
        "</definedName>"
        # 인쇄할 때 장마다 첫 줄(머리글)을 다시 찍는다.
        f'<definedName name="_xlnm.Print_Titles" localSheetId="{i}">'
        f"'{escape(s.name[:31])}'!$1:$1</definedName>"
        for i, s in enumerate(sheets))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{items}</sheets><definedNames>{names}</definedNames></workbook>')


def _workbook_rels(count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/'
        f'2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, count + 1))
    rels += (f'<Relationship Id="rId{count + 1}" Type="http://schemas.openxmlformats.org/'
             'officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>")


def _fill(rgb: str) -> str:
    return f'<fill><patternFill patternType="solid"><fgColor rgb="FF{rgb}"/></patternFill></fill>'


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0"/></numFmts>'
    '<fonts count="2">'
    '<font><sz val="10"/><name val="맑은 고딕"/></font>'
    '<font><b/><sz val="10"/><name val="맑은 고딕"/></font>'
    '</fonts>'
    # 0·1 은 엑셀이 예약한 채우기. 2: 머리글 회색, 3: A 초록, 4: B 파랑, 5: C 노랑
    '<fills count="6"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    + _fill("D9D9D9") + _fill("C6EFCE") + _fill("DDEBF7") + _fill("FFF2CC") +
    '</fills>'
    '<borders count="2"><border/>'
    '<border><left style="thin"><color rgb="FFBFBFBF"/></left>'
    '<right style="thin"><color rgb="FFBFBFBF"/></right>'
    '<top style="thin"><color rgb="FFBFBFBF"/></top>'
    '<bottom style="thin"><color rgb="FFBFBFBF"/></bottom></border></borders>'
    '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
    '<cellXfs count="10">'
    # TEXT
    '<xf fontId="0" fillId="0" borderId="1" applyBorder="1" applyAlignment="1">'
    '<alignment vertical="top"/></xf>'
    # HEADER
    '<xf fontId="1" fillId="2" borderId="1" applyFont="1" applyFill="1" applyBorder="1" '
    'applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
    # WRAP
    '<xf fontId="0" fillId="0" borderId="1" applyBorder="1" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    # MONEY (#,##0)
    '<xf numFmtId="3" fontId="0" fillId="0" borderId="1" applyNumberFormat="1" '
    'applyBorder="1" applyAlignment="1"><alignment vertical="top"/></xf>'
    # DECIMAL (0.0)
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="1" applyNumberFormat="1" '
    'applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>'
    # GRADE_A / B / C / D
    '<xf fontId="1" fillId="3" borderId="1" applyFont="1" applyFill="1" applyBorder="1" '
    'applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>'
    '<xf fontId="1" fillId="4" borderId="1" applyFont="1" applyFill="1" applyBorder="1" '
    'applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>'
    '<xf fontId="0" fillId="5" borderId="1" applyFill="1" applyBorder="1" '
    'applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>'
    '<xf fontId="0" fillId="0" borderId="1" applyBorder="1" '
    'applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>'
    # CENTER
    '<xf fontId="0" fillId="0" borderId="1" applyBorder="1" '
    'applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')
