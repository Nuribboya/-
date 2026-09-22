"""조달청 나라장터 OpenAPI - 낙찰(개찰결과) 수집.

원청 후보를 찾는 가장 직접적인 신호는 '자동제어·계장 공사를 따낸 업체'다.
그 업체가 판넬을 사서 현장에 넣는 쪽이기 때문이다. 낙찰정보서비스에서
업체명·사업자번호·금액을 긁어 후보 풀을 만든다.

주의
- 엔드포인트가 https 가 아니라 **http** 다 (명세서 기준 SSL 없음).
- 인증 파라미터는 대문자 `ServiceKey`.
- 오퍼레이션 경로가 개정될 때마다 바뀌어서, 후보 경로를 순서대로 찔러보고
  (`probe`) 정상 응답하는 것을 캐시해서 쓴다.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

from prime_contractor.models import Award

log = logging.getLogger(__name__)

SCSBID_BASES = (
    "http://apis.data.go.kr/1230000/as/ScsbidInfoService",
    "http://apis.data.go.kr/1230000/ScsbidInfoService",
)
BIDNOTICE_BASES = (
    "http://apis.data.go.kr/1230000/ad/BidPublicInfoService",
    "http://apis.data.go.kr/1230000/BidPublicInfoService",
)

#: 업무구분 → 오퍼레이션 접미사
CATEGORY_SUFFIX = {"cnstwk": "Cnstwk", "servc": "Servc", "thng": "Thng"}
CATEGORY_LABEL = {"cnstwk": "공사", "servc": "용역", "thng": "물품"}

#: 오퍼레이션 후보. 낙찰자 정보(업체명·금액)를 주는 '낙찰목록현황'을 먼저 쓰고,
#: 안 되면 '개찰결과'로 물러선다. 개찰결과는 업체 정보가 opengCorpInfo 한 칸에
#: 뭉쳐 들어오고 낙찰금액 항목이 없어서 정보량이 적다.
OP_TEMPLATES = ("getScsbidListSttus{}PPSSrch", "getOpengResultListInfo{}PPSSrch")


def ops_for(category: str) -> tuple[str, ...]:
    return tuple(t.format(CATEGORY_SUFFIX[category]) for t in OP_TEMPLATES)

#: 날짜 파라미터 방식이 오퍼레이션마다 달라 두 가지를 모두 시도한다.
DATE_STYLES = (
    ("inqryBgnDt", "inqryEndDt", "%Y%m%d%H%M"),
    ("inqryBgnDate", "inqryEndDate", "%Y%m%d"),
)

#: 응답 필드명 후보 (앞에서부터 첫 유효값)
WINNER_FIELDS = {
    "winner_name": ("bidwinnrNm", "opengCorpNm", "corpNm", "prcbdrNm", "scsbidCorpNm"),
    "winner_bizno": ("bidwinnrBizno", "bizno", "prcbdrBizno", "corpBizno"),
    "amount": ("sucsfbidAmt", "bidwinnrAmt", "opengAmt", "sucsfbidPrce", "presmptPrce"),
    "notice_no": ("bidNtceNo",),
    "title": ("bidNtceNm", "bidNtceNmDtls"),
    "demand_org": ("dminsttNm", "rlDminsttNm", "ntceInsttNm"),
    "opening_dt": ("opengDt", "rlOpengDt", "fnlSucsfDt"),
}

#: 개찰결과 응답은 낙찰업체를 이 한 칸에 몰아 넣는다. 형식이 문서로 공개돼 있지
#: 않아 dict / list / 구분자 문자열을 모두 받아 본다.
CORP_INFO_FIELD = "opengCorpInfo"
_CORP_NAME_KEYS = ("bidwinnrNm", "corpNm", "cmpnyNm", "opengCorpNm", "prcbdrNm")
_CORP_BIZNO_KEYS = ("bidwinnrBizno", "bizno", "corpBizno", "prcbdrBizno")


class G2BError(RuntimeError):
    pass


@dataclass
class ApiPage:
    ok: bool
    result_code: str
    result_msg: str
    total_count: int
    items: list[dict]


def _pick(item: dict, names: tuple[str, ...]) -> str:
    for n in names:
        v = item.get(n)
        if v in (None, "", "null") or isinstance(v, (dict, list)):
            continue
        return str(v).strip()
    return ""


def _corp_info(item: dict) -> tuple[str, str]:
    """opengCorpInfo 에서 (업체명, 사업자번호)를 최대한 뽑아낸다.

    JSON 이면 dict/list 로, 아니면 '업체명|사업자번호|...' 류의 구분자 문자열로
    온다. 어느 쪽으로도 못 읽으면 빈 값을 주고 호출부가 그 건을 버린다.
    """
    raw = item.get(CORP_INFO_FIELD)
    if raw in (None, "", "null"):
        return "", ""
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    if isinstance(raw, dict):
        return _pick(raw, _CORP_NAME_KEYS), _pick(raw, _CORP_BIZNO_KEYS)

    parts = [t.strip() for t in re.split(r"[|^\t]|,\s", str(raw)) if t.strip()]
    name = next((t for t in parts if not t.replace("-", "").isdigit()), "")
    bizno = next((t for t in parts
                  if t.replace("-", "").isdigit() and len(t.replace("-", "")) == 10), "")
    return name, bizno


def _to_int(text: str) -> int:
    digits = "".join(c for c in text if c.isdigit())
    return int(digits) if digits else 0


def _decode_key(key: str) -> str:
    """포털이 주는 Encoding 키를 그대로 넣어도 이중 인코딩되지 않게 한 번 푼다."""
    if "%" in key:
        unquoted = urllib.parse.unquote(key)
        if unquoted != key:
            return unquoted
    return key


def _parse(text: str) -> ApiPage:
    """JSON / XML 두 형태를 모두 받아 공통 구조로 만든다."""
    text = text.strip()
    if text.startswith("{"):
        payload = json.loads(text)
        body = (payload.get("response") or {}).get("body") or {}
        header = (payload.get("response") or {}).get("header") or {}
        items = body.get("items") or []
        if isinstance(items, dict):           # 1건일 때 dict 로 오는 경우
            items = items.get("item") or []
        if isinstance(items, dict):
            items = [items]
        code = str(header.get("resultCode", "")).strip()
        return ApiPage(code in ("00", "0"), code, str(header.get("resultMsg", "")),
                       int(body.get("totalCount") or 0), items)

    root = ET.fromstring(text)
    code = (root.findtext(".//header/resultCode") or root.findtext(".//resultCode") or "").strip()
    msg = (root.findtext(".//header/resultMsg") or root.findtext(".//resultMsg") or "").strip()
    total = _to_int(root.findtext(".//totalCount") or "0")
    items = [{child.tag: (child.text or "").strip() for child in node}
             for node in root.findall(".//items/item")]
    return ApiPage(code in ("00", "0"), code, msg, total, items)


class G2BClient:
    """낙찰정보/입찰공고 조회 클라이언트."""

    def __init__(
        self,
        service_key: str,
        timeout: float = 20.0,
        num_of_rows: int = 100,
        max_pages: int = 20,
        sleep_sec: float = 0.1,
        session: requests.Session | None = None,
    ) -> None:
        if not service_key:
            raise G2BError("조달청 서비스키가 없습니다. G2B_SERVICE_KEY 를 설정하세요.")
        self.key = _decode_key(service_key)
        self.timeout = timeout
        self.num_of_rows = num_of_rows
        self.max_pages = max_pages
        self.sleep_sec = sleep_sec
        self.session = session or requests.Session()
        self._resolved: dict[str, tuple[str, tuple[str, str, str]]] = {}
        self.last_probe: list[str] = []
        #: 공고명 검색이 안 먹혀 전체 수집으로 돌린 업무구분
        self.keyword_fallback: set[str] = set()

    # --- 저수준 호출 ---------------------------------------------------------

    def _call(self, url: str, params: dict) -> ApiPage:
        query = {"ServiceKey": self.key, "type": "json", **params}
        resp = self.session.get(url, params=query, timeout=self.timeout)
        resp.raise_for_status()
        time.sleep(self.sleep_sec)          # 명세상 30 tps 제한
        return _parse(resp.text)

    def _variants(self, bases: tuple[str, ...],
                  ops: tuple[str, ...]) -> list[tuple[str, tuple[str, str, str]]]:
        if isinstance(ops, str):
            # 문자열도 iterable 이라 한 글자씩 돌며 엉뚱한 URL을 조용히 만든다.
            raise TypeError("ops 는 오퍼레이션 이름들의 튜플이어야 합니다 (문자열 아님)")
        return [(f"{base}/{op}", style)
                for op in ops for base in bases for style in DATE_STYLES]

    def _try_variant(self, url: str, style: tuple[str, str, str],
                     begin: datetime, end: datetime, keyword: str = "") -> ApiPage | str:
        """조합 하나를 1건만 찔러본다. 실패하면 사유 문자열을 돌려준다."""
        bgn_p, end_p, fmt = style
        params = {"pageNo": "1", "numOfRows": "1", "inqryDiv": "1",
                  bgn_p: begin.strftime(fmt), end_p: end.strftime(fmt)}
        if keyword:
            params["bidNtceNm"] = keyword
        try:
            return self._call(url, params)
        except (requests.RequestException, ValueError, ET.ParseError) as exc:
            return f"{type(exc).__name__}: {exc}"

    def _resolve(self, key: str, bases: tuple[str, ...], ops: tuple[str, ...],
                 begin: datetime, end: datetime) -> tuple[str, tuple[str, str, str]]:
        """쓸 만한 (URL, 날짜방식) 조합을 찾아 캐시한다.

        경로가 살아있는 것만으로는 부족하다. 오퍼레이션에 따라 낙찰업체명이 아예
        안 들어오기도 해서(개찰결과 계열), **업체명이 실제로 뽑히는 조합**을 최우선
        으로 고른다. 등급은 이렇다.

            3 - 정상 + 건수 있음 + 업체명 추출됨   ← 원하는 것
            2 - 정상 + 건수 있음 (업체명 없음)
            1 - 정상이지만 0건
        """
        if key in self._resolved:
            return self._resolved[key]

        notes: list[str] = []
        best: tuple[int, str, tuple[str, str, str]] | None = None
        for url, style in self._variants(bases, ops):
            outcome = self._try_variant(url, style, begin, end)
            label = f"  {url.rsplit('/1230000/', 1)[-1]} [{style[0]}]"
            if isinstance(outcome, str):
                notes.append(f"{label} → {outcome}")
                continue
            if not outcome.ok:
                notes.append(f"{label} → {outcome.result_code} {outcome.result_msg}")
                continue
            if not outcome.total_count or not outcome.items:
                notes.append(f"{label} → OK, 0건")
                grade = 1
            else:
                has_name = bool(_to_award(outcome.items[0], "servc", "").winner_name)
                grade = 3 if has_name else 2
                notes.append(f"{label} → OK, {outcome.total_count}건, "
                             f"업체명 {'확인' if has_name else '없음'}")
            if best is None or grade > best[0]:
                best = (grade, url, style)
            if grade == 3:
                break                       # 더 볼 필요 없다

        self.last_probe += notes
        if best is None:
            raise G2BError(f"[{key}] 사용 가능한 오퍼레이션을 찾지 못했습니다:\n" + "\n".join(notes))
        grade, url, style = best
        if grade < 3:
            log.warning("[%s] 낙찰업체명이 확인되지 않는 조합을 씁니다(등급 %s). "
                        "probe --dump 로 응답을 확인해 보세요.", key, grade)
        self._resolved[key] = (url, style)
        return url, style

    def supports_keyword(self, url: str, style: tuple[str, str, str],
                         begin: datetime, end: datetime) -> bool:
        """공고명 검색(bidNtceNm)이 실제로 먹히는지 확인.

        명세에 없는 파라미터는 에러가 아니라 그냥 '무시'되기도 한다. 그래서 실제
        키워드가 아니라 **있을 리 없는 문자열**을 넣어 본다. 건수가 그대로면
        파라미터가 무시된 것이고, 줄어들면 제대로 걸러진 것이다. 실제 키워드로
        재면 '그 기간에 그런 공고가 없었을 뿐'인 경우와 구분이 안 된다.
        """
        plain = self._try_variant(url, style, begin, end)
        if isinstance(plain, str) or not plain.ok or plain.total_count == 0:
            return True                     # 비교 기준이 없으면 판단 보류
        probe = self._try_variant(url, style, begin, end, keyword="없을법한공고명ZZQX")
        if isinstance(probe, str) or not probe.ok:
            return False
        return probe.total_count < plain.total_count

    def diagnose(self, categories: tuple[str, ...], sample_keyword: str = "자동제어",
                 days: int = 7, end: datetime | None = None, dump: bool = False) -> list[str]:
        """어떤 경로가 살아있고 무엇이 들어오는지 그대로 찍어 준다 (문제 파악용)."""
        end = end or datetime.now()
        begin = end - timedelta(days=days)
        lines = [f"조회창: {begin:%Y-%m-%d} ~ {end:%Y-%m-%d} / 샘플 키워드: {sample_keyword}"]
        for cat in categories:
            lines.append(f"\n[{CATEGORY_LABEL[cat]}]")
            for url, style in self._variants(SCSBID_BASES, ops_for(cat)):
                outcome = self._try_variant(url, style, begin, end)
                tag = f"  {url.rsplit('/1230000/', 1)[-1]} [{style[0]}]"
                if isinstance(outcome, str):
                    lines.append(f"{tag} → 호출실패 {outcome}")
                    continue
                if not outcome.ok:
                    lines.append(f"{tag} → {outcome.result_code} {outcome.result_msg}")
                    continue
                keyed = self._try_variant(url, style, begin, end, keyword=sample_keyword)
                keyed_cnt = keyed.total_count if isinstance(keyed, ApiPage) and keyed.ok else "실패"
                award = _to_award(outcome.items[0], cat, "") if outcome.items else None
                got = f"낙찰업체 '{award.winner_name}'" if award and award.winner_name else "낙찰업체 추출 실패"
                lines.append(f"{tag} → OK, 전체 {outcome.total_count}건 / "
                             f"'{sample_keyword}' 검색 {keyed_cnt}건 / {got}")
                if dump and outcome.items:
                    for k, v in sorted(outcome.items[0].items()):
                        lines.append(f"      {k} = {str(v)[:70]}")
        return lines

    def _paged(self, url: str, base_params: dict):
        collected = 0
        for page_no in range(1, self.max_pages + 1):
            page = self._call(url, {**base_params, "pageNo": str(page_no),
                                    "numOfRows": str(self.num_of_rows)})
            if not page.ok:
                raise G2BError(f"{page.result_code} {page.result_msg}")
            if not page.items:
                return
            yield from page.items
            collected += len(page.items)
            if collected >= page.total_count or len(page.items) < self.num_of_rows:
                return
        log.warning("%s: 페이지 상한(%s)에 걸려 일부만 수집했습니다.", url, self.max_pages)

    # --- 고수준 조회 ---------------------------------------------------------

    def fetch_awards(
        self,
        keywords: tuple[str, ...],
        categories: tuple[str, ...],
        lookback_days: int = 180,
        end: datetime | None = None,
    ) -> list[Award]:
        """키워드 × 업무구분으로 낙찰 이력을 모은다.

        조회 창이 길면 API 가 거절하므로 15일씩 끊어서 돈다.
        """
        end = end or datetime.now()
        begin = end - timedelta(days=lookback_days)
        awards: list[Award] = []
        seen: set[tuple[str, str]] = set()

        for cat in categories:
            try:
                url, style = self._resolve(f"scsbid:{cat}", SCSBID_BASES, ops_for(cat),
                                           end - timedelta(days=7), end)
            except G2BError as exc:
                log.warning("%s 낙찰 조회 불가: %s", CATEGORY_LABEL[cat], exc)
                continue

            bgn_p, end_p, fmt = style
            keyword_ok = self.supports_keyword(url, style, end - timedelta(days=7), end)
            if not keyword_ok:
                log.warning("[%s] 공고명 검색이 먹히지 않아 전체를 받아 직접 걸러냅니다 "
                            "(시간이 더 걸립니다).", CATEGORY_LABEL[cat])
                self.keyword_fallback.add(cat)

            for w_begin, w_end in _windows(begin, end, days=15):
                window = {bgn_p: w_begin.strftime(fmt), end_p: w_end.strftime(fmt)}
                # 키워드가 먹히면 검색어별로, 아니면 기간 전체를 한 번에 받아 제목으로 거른다.
                queries = [{"bidNtceNm": kw} for kw in keywords] if keyword_ok else [{}]
                for extra in queries:
                    params = {"inqryDiv": "1", **window, **extra}
                    try:
                        items = list(self._paged(url, params))
                    except (G2BError, requests.RequestException, ValueError, ET.ParseError) as exc:
                        log.warning("[%s/%s] %s ~ %s 조회 실패: %s",
                                    CATEGORY_LABEL[cat], extra.get("bidNtceNm", "전체"),
                                    w_begin.date(), w_end.date(), exc)
                        continue
                    for item in items:
                        kw = extra.get("bidNtceNm") or _first_keyword(item, keywords)
                        if not kw:
                            continue            # 전체 수집 모드에서 키워드와 무관한 공고
                        award = _to_award(item, cat, kw)
                        if not award.winner_name:
                            continue
                        dedupe = (award.notice_no, award.winner_name)
                        if dedupe in seen:
                            continue
                        seen.add(dedupe)
                        awards.append(award)
        return awards


def _to_award(item: dict, category: str, keyword: str) -> Award:
    name = _pick(item, WINNER_FIELDS["winner_name"])
    bizno = _pick(item, WINNER_FIELDS["winner_bizno"])
    if not name:
        name, info_bizno = _corp_info(item)
        bizno = bizno or info_bizno
    return Award(
        notice_no=_pick(item, WINNER_FIELDS["notice_no"]),
        title=_pick(item, WINNER_FIELDS["title"]),
        demand_org=_pick(item, WINNER_FIELDS["demand_org"]),
        winner_name=name,
        winner_bizno=bizno,
        amount=_to_int(_pick(item, WINNER_FIELDS["amount"])),
        opening_dt=_pick(item, WINNER_FIELDS["opening_dt"]),
        category=CATEGORY_LABEL.get(category, category),
        keyword=keyword,
    )


def _first_keyword(item: dict, keywords: tuple[str, ...]) -> str:
    """공고명에 걸린 첫 키워드. 전체 수집 모드에서 직접 거를 때 쓴다."""
    title = _pick(item, WINNER_FIELDS["title"]).upper()
    for kw in keywords:
        if kw.upper() in title:
            return kw
    return ""


def _windows(begin: datetime, end: datetime, days: int = 15):
    """[begin, end] 를 days 간격 구간으로 자른다."""
    cur = begin
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt
