"""공장 찾기 — 판넬을 실제로 쓰는 '규모 있는 공장', 특히 상장사 공장을 찾는다.

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
    ("포장·충진기계", ("포장기", "포장기계", "충진", "충전기계", "실링기", "라벨러", "랩핑")),
    ("식품기계", ("식품기계", "식품가공기", "살균기", "제과기", "제빵기", "튀김기",
                "세척기", "탈수기", "조리기")),
    ("컨베이어·물류설비", ("컨베이어", "이송장치", "이송기", "리프트", "크레인", "호이스트",
                       "자동창고", "스태커", "분류기", "소터", "적재기")),
    ("환경·수처리설비", ("집진기", "집진", "수처리", "폐수처리", "정화장치", "탈취", "소각로",
                      "스크러버", "여과기", "탈수설비")),
    ("자동화·로봇", ("자동화설비", "자동화장비", "자동화기계", "로봇", "공장자동화", "FA설비")),
    ("공작·산업기계", ("공작기계", "프레스", "사출기", "성형기", "절단기", "가공기", "용접기",
                     "절곡기", "선반", "밀링", "연삭기", "산업기계", "산업용기계")),
    ("열·공조설비", ("보일러", "건조기", "오븐", "냉동기", "냉각기", "공조기", "열교환기", "히터",
                   "항온항습기", "칠러")),
    ("펌프·송풍·유체기계", ("펌프", "송풍기", "블로워", "압축기", "컴프레서", "교반기", "믹서",
                        "분쇄기", "선별기")),
)
GENERIC_MACHINE = ("기타 기계·장비", ("기계", "장비", "설비", "장치", "플랜트"))

#: 일반 제조 공장 분야와 경기를 덜 타는 정도(0~1, config.py 의 steady 와 같은 눈금).
#: 이런 공장은 라인 증설·개조·유지보수 때 판넬이 들어간다.
PLANT_FIELDS: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("식품·음료", ("식품", "음료", "제과", "제빵", "육가공", "유가공", "김치", "소스", "주류",
                 "사료", "떡", "면류", "냉동식품", "커피", "두부", "장류", "도시락", "라면", "스낵",
                 "과자", "빵", "햄", "소시지", "만두", "즉석", "간편식", "조미", "식용유", "설탕",
                 "밀가루", "전분", "생수", "맥주", "소주", "우유", "치즈", "아이스크림", "수산",
                 "축산물", "통조림", "건강식품", "스프", "채소", "농산", "반찬", "양념", "젓갈",
                 "가공식품", "음식", "조리식품", "곡물", "부분육", "포장육", "지육", "식육",
                 "도축", "훈제", "가공육", "육류", "닭", "오리", "계란", "난황"), 0.9),
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
#: 손일이 많아 판넬 물량이 거의 없는 업종 — 목록에서 뺀다. 기계를 만드는 곳('가구 가공기계')은
#: 먼저 기계로 잡히므로 빠지지 않는다.
LOW_DEMAND_WORDS = ("의류", "의복", "봉제", "셔츠", "니트", "속옷", "양말", "신발", "구두", "운동화",
                    "가방", "핸드백", "지갑", "가구", "목재", "원목", "침대", "소파", "귀금속",
                    "장신구", "액세서리", "악세사리", "주얼리", "안경", "문구", "필기구", "완구",
                    "장난감", "인형", "악기", "피아노", "단순조립", "단순 조립", "임가공",
                    "포장대행", "포장 대행")
#: 공장등록에 섞여 나오는 공장 아닌 일 — 임대·소프트웨어·광고 등. 판넬 수요가 없다.
NOT_MANUFACTURING = re.compile(r"(^\s*창고\s*$|임대|소프트웨어|S/W|\bSW\b|컨설팅|광고|콜센터|마케팅|게임|"
                               r"교육|부동산|플랫폼|엔지니어링\s*서비스|대행)", re.I)
#: 같은 업종의 한국표준산업분류(DART 업종코드 앞자리) — 조회 전에 상장사를 거르는 데 쓴다.
#: 14 의복, 15 가죽·가방·신발, 16 목재, 32 가구, 331 귀금속·장신구, 332 악기, 333 운동·완구,
#: 3399 문구 등, 27402 안경.
LOW_DEMAND_CODES = ("14", "15", "16", "32", "331", "332", "333", "3399", "27402")
#: 기계 제작사만 볼 때, 이 말만 있고 '기계를 만든다'는 말이 없으면 부품·소재 업체로 본다.
PART_WORDS = ("부품", "부분품", "소재", "금형", "베어링", "볼트", "너트", "스프링", "가스켓")

#: '규모 있는 공장' 기준 — 하나라도 넘으면.
MIN_EMPLOYEES = 30
#: 이보다 적은 공장은 목록에서 아예 뺀다(인원이 적혀 있을 때만).
MIN_SITE_EMPLOYEES = 5
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


#: 다른 분야 말을 품은 낱말 — 분야를 고르기 전에 바꿔 둔다('스프링'의 '스프', '인쇄회로기판'의
#: '인쇄', '공기살균기'의 '살균기', '전기차 충전기'의 '충전기').
_MISLEADING = (("스프링", "spring"), ("인쇄회로", "PCB"), ("공기살균기", "공기정화장치"),
               ("식품첨가물", "첨가물"), ("식품포장재", "포장재"), ("식품포장용", "포장용"))


def classify(products: str, industry: str = "") -> tuple[str, bool, float]:
    """(분야, 기계·장비 제작사인가, 경기를 덜 타는 정도). 판넬 업체·빈 칸이면 분야가 ''."""
    text = f"{products} {industry}"
    for word, plain in _MISLEADING:
        text = text.replace(word, plain)
    if not text.strip() or any(w in text for w in COMPETITOR_WORDS):
        return "", False, 0.0
    for field, words in MACHINE_FIELDS:
        if any(w in text for w in words):
            return field, True, MACHINE_STEADY
    if any(w in text for w in LOW_DEMAND_WORDS) or NOT_MANUFACTURING.search(text):
        return "", False, 0.0
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
        employees = _number(r.get("employees", ""))
        if 0 < employees < MIN_SITE_EMPLOYEES:   # 몇 명짜리 작업장 — 판넬 수요도, 원청도 아니다
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
            employees=employees, area_m2=_number(r.get("area", "")),
            sources={"공장등록"})
        found.append(cand)
    return sorted(found, key=lambda c: c.distance_km)


#: 등록공장 회사명엔 공장 이름이 붙기도 한다: '(주)농심 안성공장', '오뚜기 제2공장'.
#: 띄어 쓴 마지막 낱말이 공장 이름이면 뗀다('안성공장', '인천1공장', '제2공장').
_PLANT_WORD = re.compile(r"\s+\S*(공장|사업장|지점|센터|캠퍼스)\s*$")
#: 붙여 쓴 번호 공장('오뚜기제2공장')만 뗀다. 지명까지 붙여 쓴 건 상호와 구분이 안 된다.
_PLANT_NUMBER = re.compile(r"제?\d+공장\s*$")


def company_key(name: str) -> str:
    """공장 이름을 뗀 회사 이름 — DART 상호와 맞춰 보는 열쇠."""
    base = _PLANT_NUMBER.sub("", _PLANT_WORD.sub("", (name or "").strip()))
    return normalize_name(base) or normalize_name(name)


def mark_dart_registered(factories: list[Candidate], corp_index: dict[str, str],
                         listed: dict[str, str] | None = None) -> tuple[int, int]:
    """금감원(DART) 등록 회사면 corp_code, 상장사면 stock_code 를 채운다. (등록, 상장) 수.

    DART 등록 = 외부감사 받는 규모(대략 자산 100억 이상). 상장사 = 코스피·코스닥.
    본사 주소가 아니라 이 파일의 '공장 주소'로 거리를 쟀으니, 본사가 서울인 회사의
    안성 공장도 잡힌다(농심처럼).
    """
    registered = on_market = 0
    for c in factories:
        keys = {normalize_name(c.name), company_key(c.name)}
        code = next((corp_index[k] for k in keys if k in corp_index), "")
        stock = next((listed[k] for k in keys if listed and k in listed), "")
        if code:
            c.corp_code = code
            c.sources.add("DART")
            registered += 1
        if stock:
            c.stock_code = stock
            on_market += 1
    return registered, on_market


def listed_names(listed_companies) -> dict[str, str]:
    """DART 상장사 목록 [(상호, 고유번호, 종목코드)] → {정규화 상호: 종목코드}."""
    return {normalize_name(name): stock for name, _code, stock in listed_companies if stock}


# --- 계열사 ---------------------------------------------------------------------
#
# 케이씨이노베이션에 들어가 있으면 케이씨텍·케이씨이앤씨 같은 같은 그룹 회사로 넓히기가
# 쉽다 — 구매·설비 담당끼리 업체를 돌려 쓰고, '그룹사 납품 실적'이 그대로 통한다.
# 상장사가 지분을 가진 회사(자회사·계열사)를 DART '타법인 출자현황'으로 모아, 비상장
# 계열사 공장도 'OO 계열'로 묶는다.

#: 이만큼 넘게 가지면 계열사로 본다. '경영참여'·'자회사' 목적이면 이보다 낮아도 본다.
AFFILIATE_RATIO = 30.0
AFFILIATE_PURPOSE_RATIO = 15.0


def is_affiliate_stake(ratio: float, purpose: str) -> bool:
    if ratio >= AFFILIATE_RATIO:
        return True
    return ratio >= AFFILIATE_PURPOSE_RATIO and any(
        w in purpose for w in ("경영", "자회사", "계열", "지배"))


def build_group_map(listed_companies, fetch, progress=None, workers: int = 4,
                    failed: list | None = None) -> dict[str, str]:
    """{정규화 상호: 모회사(상장사) 이름}. fetch(corp_code) → [{name, ratio, purpose}].

    상장사마다 한 번씩 물어보므로 처음엔 오래 걸린다(2천여 곳). fetch 쪽이 캐시를 둔다.
    한 회사를 여러 상장사가 가지면 지분이 가장 큰 쪽을 그룹으로 본다.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    companies = [(name, code) for name, code, stock in listed_companies if stock]
    failed = failed if failed is not None else []
    best: dict[str, tuple[float, str]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch, code): name for name, code in companies}
        for future in as_completed(futures):
            parent = futures[future]
            done += 1
            if progress and (done % 100 == 0 or done == len(companies)):
                progress(done, len(companies))
            try:
                rows = future.result()
            except Exception:                    # 한 곳 실패로 전체를 멈추지 않는다
                failed.append(parent)
                continue
            for row in rows:
                if not is_affiliate_stake(row.get("ratio", 0.0), row.get("purpose", "")):
                    continue
                key = company_key(row["name"])
                if key and key != company_key(parent) and (
                        key not in best or row["ratio"] > best[key][0]):
                    best[key] = (row["ratio"], parent)
    return {key: parent for key, (_ratio, parent) in best.items()}


def mark_groups(factories: list[Candidate], group_map: dict[str, str],
                listed_companies=(), incumbent=None) -> int:
    """공장마다 그룹을 붙인다: 상장사 자신이면 그 이름, 계열사면 'OO 계열'. 붙인 수."""
    display = {normalize_name(name): name for name, _code, stock in listed_companies if stock}
    prefixes = tuple(getattr(incumbent, "affiliate_prefixes", ()) or ())
    names = {normalize_name(n) for n in getattr(incumbent, "affiliate_names", ()) or ()}
    tagged = 0
    for c in factories:
        keys = [normalize_name(c.name), company_key(c.name)]
        if any(k in names or (prefixes and k.startswith(tuple(p.upper() for p in prefixes)))
               for k in keys):
            c.group = f"{getattr(incumbent, 'name', '기존 원청')}(거래 중)"
        elif c.stock_code:
            c.group = next((display[k] for k in keys if k in display), c.name)
        else:
            parent = next((group_map[k] for k in keys if k in group_map), "")
            c.group = f"{parent} 계열" if parent else ""
        tagged += bool(c.group)
    return tagged


# --- 파일 없이: 상장사·계열사 이름으로 공장 모으기 -----------------------------------

#: 제조업(한국표준산업분류 10~34). 상장사 중 이 업종만 공장을 물어본다 — 금융·유통·
#: 서비스 회사까지 물으면 하루 호출 한도를 금방 넘긴다.
MANUFACTURING = tuple(str(n) for n in range(10, 35))
#: 해외 법인 이름 — 국내 공장 목록에 없으니 묻지 않는다.
_FOREIGN = re.compile(r"(LTD|INC|LLC|GMBH|PTE|CORP|CO\.|S\.A|B\.V|有限|유한공사|"
                      r"VIETNAM|CHINA|AMERICA|USA|JAPAN|INDIA|MEXICO|EUROPE)", re.I)


def is_manufacturer(info: dict) -> bool:
    """제조업이고, 손일이 많은 업종(의복·신발·가구·완구 등)은 아닌 곳."""
    code = str(info.get("induty_code", ""))
    return code.startswith(MANUFACTURING) and not code.startswith(LOW_DEMAND_CODES)


#: 지주회사(64992)·회사 본부(7151) — 공장은 없어도 계열 공장들을 거느린다.
HOLDING = ("6499", "715")


def is_holding(info: dict) -> bool:
    return str(info.get("induty_code", "")).startswith(HOLDING)


def sort_listed(listed_companies, company, progress=None, workers: int = 4):
    """상장사를 (제조업, 계열사를 볼 회사 = 제조업+지주회사) 로 나눈다. 여러 곳을 한꺼번에 묻는다.

    금융·유통·서비스 상장사의 출자현황까지 다 보면 두 배 넘게 오래 걸리는데, 그쪽 계열사는
    공장이 거의 없다.
    """
    from concurrent.futures import ThreadPoolExecutor

    def kind(row):
        try:
            info = company(row[1])
        except Exception:                        # 한 곳 실패로 멈추지 않는다
            return False, False
        return is_manufacturer(info), is_holding(info)

    makers, heads = [], []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for i, (row, (maker, holding)) in enumerate(
                zip(listed_companies, pool.map(kind, listed_companies)), 1):
            if maker:
                makers.append(row)
            if maker or holding:
                heads.append(row)
            if progress and (i % 300 == 0 or i == len(listed_companies)):
                progress(i, len(listed_companies))
    return makers, heads


#: 공장이 없을 이름 — 금융·투자·유통·서비스 계열사는 공장 조회에서 뺀다(하루 한도 아끼기).
_NOT_FACTORY = re.compile(r"(금융|캐피탈|투자|증권|보험|자산운용|파트너스|리츠|인베스트|벤처스|"
                          r"펀드|조합|신탁|유통|리테일|쇼핑|백화점|호텔|리조트|레저|골프|"
                          r"엔터|미디어|방송|컨설팅|서비스|아이티|정보통신|건설|개발|부동산|"
                          r"재단|병원|학교|SPC|PEF)", re.I)
#: 공장을 찾아볼 계열사 지분 기준 — 절반 넘게 가진 자회사만.
SUBSIDIARY_RATIO = 50.0


def affiliate_names(listed_companies, fetch, min_ratio: float = 0.0) -> list[str]:
    """상장사들이 계열사로 가진 국내 회사 이름(원래 표기).

    fetch 는 인터넷에 다시 묻지 않는 것을 넘긴다 — 앞에서 못 받은 곳을 여기서 한 줄씩
    다시 물으면 진행 표시 없이 몇십 분 멈춘 것처럼 보인다.
    """
    names: dict[str, str] = {}
    for _name, code, stock in listed_companies:
        if not stock:
            continue
        try:
            rows = fetch(code)
        except Exception:
            continue
        for row in rows:
            raw = row["name"]
            ratio = row.get("ratio", 0.0)
            if (is_affiliate_stake(ratio, row.get("purpose", "")) and ratio >= min_ratio
                    and re.search(r"[가-힣]", raw) and not _FOREIGN.search(raw)
                    and not (min_ratio and (_NOT_FACTORY.search(raw)
                                            or any(w in raw for w in LOW_DEMAND_WORDS)))):
                names.setdefault(company_key(raw), raw)
    return list(names.values())


#: 안성에서 멀어 영업 범위 밖인 도. 본사가 여기면 공장도 대개 그 지역이라 묻지 않는다.
#: '광주'는 경기도 광주시와 헷갈리므로 '광주광역시'로만 본다.
FAR_PROVINCES = ("전라", "전북", "전남", "광주광역시", "전남광주", "강원", "경상", "경북", "경남",
                 "부산", "대구", "울산", "제주")


def in_far_province(address: str) -> bool:
    return (address or "").strip().startswith(FAR_PROVINCES)


def near_first(makers, company, within_km: float):
    """제조 상장사를 (본사가 거리 안, 그 밖) 으로 나누고, 본사가 먼 도(FAR_PROVINCES)면 뺀다.

    본사 주소는 묻는 순서와 빼기에만 쓴다 — 목록의 거리는 공장 주소마다 따로 잰다.
    돌려주는 값: (가까운 곳, 나머지, 뺀 수).
    """
    from prime_contractor.geo import distance_from_home

    near, rest, dropped = [], [], 0
    scored = []
    for row in makers:
        try:
            address = company(row[1]).get("adres", "")
        except Exception:
            address = ""
        if in_far_province(address):
            dropped += 1
            continue
        _region, dist = distance_from_home(address) if address else ("", None)
        scored.append((dist if dist is not None else 9999.0, row))
    for dist, row in sorted(scored, key=lambda x: x[0]):
        (near if dist <= within_km else rest).append(row)
    return near, rest, dropped


def drop_far_companies(names: list[str], lookup, workers: int = 4) -> tuple[list[str], int]:
    """본사가 먼 도에 있는 회사 이름을 뺀다. DART 에 없어 모르는 곳은 남긴다."""
    from concurrent.futures import ThreadPoolExecutor

    def far(name):
        try:
            info = lookup(name)
        except Exception:
            return False
        return bool(info) and in_far_province(info.get("adres", ""))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        flags = list(pool.map(far, names))
    kept = [n for n, f in zip(names, flags) if not f]
    return kept, len(names) - len(kept)


def same_company(record_name: str, query: str) -> bool:
    """API 는 이름 일부만 맞아도 돌려준다('삼성전자' → '삼성전자서비스'). 같은 회사만."""
    return company_key(record_name) == company_key(query)


def same_ceo(a: str, b: str) -> bool:
    """대표자 이름이 하나라도 겹치는가. '홍길동, 김철수(각자대표)' 처럼 여럿일 수 있다."""
    def names(text):
        return {t for t in re.split(r"[^가-힣A-Za-z]+", text or "") if len(t) >= 2
                and t not in ("각자대표", "공동대표", "대표이사", "대표")}
    return bool(names(a) & names(b))


def collect_by_name(queries: list[str], fetch, progress=None, workers: int = 4,
                    ceo_of: dict[str, str] | None = None) -> tuple[list[dict[str, str]], str]:
    """회사 이름마다 공장을 물어 모은다. (공장 줄, 멈춘 이유 — 한도 초과면 그 문구).

    이름이 같은 다른 회사가 많다('제이에스', '태성'). ceo_of 에 DART 대표자가 있으면 공장
    대표자와 맞는 줄만 남긴다 — 그래야 동네 작은 '제이에스'가 상장사로 둔갑하지 않는다.
    """
    ceo_of = ceo_of or {}
    from concurrent.futures import ThreadPoolExecutor, as_completed

    records: list[dict[str, str]] = []
    stopped = ""
    other_errors: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch, q): q for q in queries}
        for future in as_completed(futures):
            query = futures[future]
            done += 1
            if progress and done % 100 == 0:
                progress(done, len(queries))
            try:
                rows = future.result()
            except Exception as exc:           # 한도 초과·키 오류는 이유를 남기고 계속 모은다
                if not stopped and type(exc).__name__ in ("QuotaExceeded", "FactoryApiError"):
                    stopped = str(exc)
                elif type(exc).__name__ not in ("QuotaExceeded", "FactoryApiError"):
                    other_errors.append(f"{type(exc).__name__}: {exc}")
                continue
            expected = ceo_of.get(query, "")
            records += [r for r in rows if same_company(r.get("name", ""), query)
                        and (not expected or not r.get("ceo") or same_ceo(expected, r["ceo"]))]
    if other_errors and not stopped:        # 조용히 넘기면 '0곳'만 보고 이유를 모른다
        stopped = f"{len(other_errors)}곳 조회 실패 — 첫 오류: {other_errors[0][:200]}"
    return records, stopped


def is_sizable(c: Candidate) -> bool:
    """규모 있는 공장인가 — 종업원·면적 기준을 넘거나 DART 등록 회사."""
    return (c.employees >= MIN_EMPLOYEES or c.area_m2 >= MIN_AREA_M2 or bool(c.corp_code))


def size_text(c: Candidate) -> str:
    bits = []
    if c.employees:
        bits.append(f"{c.employees}명")
    if c.area_m2:
        bits.append(f"{c.area_m2:,}㎡")
    if c.stock_code:
        bits.append("상장")
    elif c.corp_code:
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


def write_factories_xlsx(factories, path: str | Path, within_km: float | None = None,
                        rule: str = "") -> Path:
    """공장 찾기 결과를 인쇄용 엑셀로. 분야·경기·규모로 필터해 볼 수 있다."""
    from collections import Counter
    from datetime import date

    from prime_contractor.xlsx_writer import (
        CENTER, DECIMAL, TEXT, WRAP, Column, Sheet, write_workbook)

    main = Sheet("공장", [
        Column("순위", 5, CENTER), Column("회사 이름", 18, WRAP), Column("그룹", 14, WRAP),
        Column("분야", 11, WRAP), Column("경기", 6, CENTER), Column("규모", 10, WRAP),
        Column("생산품", 20, WRAP), Column("지역", 6, CENTER), Column("거리(km)", 7, DECIMAL),
        Column("대표자", 7, CENTER), Column("전화", 12, CENTER), Column("주소", 24, WRAP),
    ], rows=[[i, c.name, c.group, c.sector, steady_text(c.sector_weight), size_text(c),
              c.products, c.region, c.distance_km, c.ceo, c.phone, c.address]
             for i, c in enumerate(factories, 1)])
    fields = Counter(c.sector for c in factories).most_common()
    limit = f"{within_km:g}km 안" if within_km is not None else "거리 제한 없이"
    guide = Sheet("읽는 법", [Column("항목", 18, TEXT), Column("설명", 80, WRAP)], rows=[
        ["만든 날", date.today().isoformat()],
        ["담은 곳", f"안성에서 {limit} 공장 {len(factories)}곳{(' — ' + rule) if rule else ''}. "
                  "가까운 순."],
        ["분야별", ", ".join(f"{f} {n}곳" for f, n in fields)],
        ["왜 공장인가", "판넬을 실제로 쓰는 곳입니다. 라인 증설·개조·교체 때 제어반이 들어가고, "
                     "기계·장비를 만드는 공장은 기계마다 제어반을 반복해서 밖에 맡깁니다."],
        ["규모", "종업원 수·면적은 공장등록 자료에 있을 때만 나옵니다. '외감(DART)'은 금감원 "
               "공시에 등록된 회사 — 외부감사를 받는 규모(대략 자산 100억 이상)입니다."],
        ["그룹", "상장사면 그 회사 이름, 상장사가 지분 30% 넘게(경영참여 목적이면 15% 넘게) "
               "가진 회사면 'OO 계열'입니다(금감원 사업보고서의 타법인 출자현황). 한 그룹에 "
               "들어가면 같은 그룹 회사로 넓히기 쉽습니다."],
        ["경기", "덜 탐 = 식품·제약·환경처럼 불황에도 돌아가는 분야, 많이 탐 = 반도체·자동차·"
               "철강처럼 경기가 꺾이면 투자부터 끊는 분야."],
        ["뺀 곳", "판넬을 만드는 곳(경쟁사), 주소를 모르거나 거리 밖인 곳."],
        ["연락할 때", "본사 구매팀보다 그 공장 시설팀·공무팀(기계 제작사면 설계팀·생산팀)에 "
                    "'판넬 교체·라인 개조 때 견적 낼 수 있게 해 달라'고 하세요."],
        ["출처", "한국산업단지공단 전국 등록공장 현황(공공데이터포털·팩토리온). 생산품은 공장 "
               "등록 때 적은 것이라 지금과 다를 수 있습니다."],
    ], landscape=False)
    return write_workbook(path, [main, guide])


# --- 지난 결과 남기기 (앱을 껐다 켜도 표가 그대로) ------------------------------------

_SAVED_FIELDS = ("name", "kind", "address", "ceo", "phone", "products", "employees", "area_m2",
                 "stock_code", "group", "corp_code", "region", "distance_km", "sector",
                 "sector_weight", "industry_name")


def save_last_factories(path, factories: list[Candidate], **meta) -> None:
    import json
    from datetime import datetime
    from pathlib import Path
    rows = [{f: getattr(c, f) for f in _SAVED_FIELDS} for c in factories]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps({"saved_at": datetime.now().strftime("%m-%d %H:%M"), **meta,
                               "factories": rows}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)


def load_last_factories(path) -> tuple[list[Candidate], dict]:
    """(공장들, 저장할 때 같이 둔 값들). 없거나 깨졌으면 ([], {})."""
    import json
    from pathlib import Path
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        factories = [Candidate(**{k: v for k, v in row.items() if k in _SAVED_FIELDS})
                     for row in data.pop("factories")]
    except (OSError, ValueError, KeyError, TypeError):
        return [], {}
    for c in factories:
        c.sources = {"공장등록"}
        # 거리 계산이 바뀌었을 수 있으니 저장된 값 대신 주소로 다시 잰다.
        region, dist = distance_from_home(c.address)
        if dist is not None:
            c.region, c.distance_km = region, dist
    return factories, data
