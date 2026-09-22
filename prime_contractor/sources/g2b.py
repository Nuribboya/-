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

#: 날짜 파라미터 방식이 오퍼레이션마다 달라 두 가지를 모두 시도한다.
DATE_STYLES = (
    ("inqryBgnDt", "inqryEndDt", "%Y%m%d%H%M"),
    ("inqryBgnDate", "inqryEndDate", "%Y%m%d"),
)

#: 응답 필드명 후보 (앞에서부터 첫 유효값)
WINNER_FIELDS = {
    "winner_name": ("bidwinnrNm", "opengCorpNm", "corpNm", "prcbdrNm"),
    "winner_bizno": ("bidwinnrBizno", "bizno", "prcbdrBizno"),
    "amount": ("sucsfbidAmt", "bidwinnrAmt", "opengAmt", "presmptPrce"),
    "notice_no": ("bidNtceNo",),
    "title": ("bidNtceNm", "bidNtceNmDtls"),
    "demand_org": ("dminsttNm", "rlDminsttNm", "ntceInsttNm"),
    "opening_dt": ("opengDt", "rlOpengDt", "fnlSucsfDt"),
}


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
        if v not in (None, "", "null"):
            return str(v).strip()
    return ""


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

    def _variants(self, bases: tuple[str, ...], op: str) -> list[tuple[str, tuple[str, str, str]]]:
        return [(f"{base}/{op}", style) for base in bases for style in DATE_STYLES]

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

    def _resolve(self, key: str, bases: tuple[str, ...], op: str,
                 begin: datetime, end: datetime) -> tuple[str, tuple[str, str, str]]:
        """살아있는 (URL, 날짜방식) 조합을 찾아 캐시한다.

        응답코드가 정상이어도 건수가 0이면 '경로는 살아있지만 조회 조건이 안 맞는'
        상태다. 그런 조합을 덜컥 채택하면 전체 수집이 조용히 0건으로 끝나므로,
        **실제로 건수가 잡히는 조합을 우선** 고른다.
        """
        if key in self._resolved:
            return self._resolved[key]

        fallback: tuple[str, tuple[str, str, str]] | None = None
        notes: list[str] = []
        for url, style in self._variants(bases, op):
            outcome = self._try_variant(url, style, begin, end)
            label = f"  {url} [{style[0]}]"
            if isinstance(outcome, str):
                notes.append(f"{label} → {outcome}")
                continue
            if not outcome.ok:
                notes.append(f"{label} → {outcome.result_code} {outcome.result_msg}")
                continue
            notes.append(f"{label} → OK, 총 {outcome.total_count}건")
            if outcome.total_count > 0:
                self._resolved[key] = (url, style)
                self.last_probe += notes
                return url, style
            if fallback is None:
                fallback = (url, style)

        self.last_probe += notes
        if fallback is not None:
            log.warning("[%s] 응답은 정상인데 건수가 0입니다. 조회 조건을 확인하세요.", key)
            self._resolved[key] = fallback
            return fallback
        raise G2BError(f"[{key}] 사용 가능한 오퍼레이션을 찾지 못했습니다:\n" + "\n".join(notes))

    def supports_keyword(self, url: str, style: tuple[str, str, str],
                         begin: datetime, end: datetime, keyword: str) -> bool:
        """이 오퍼레이션이 공고명 부분검색(bidNtceNm)을 실제로 지원하는지 확인.

        명세에 없는 파라미터를 넘기면 에러가 아니라 '0건'으로 조용히 돌아오는
        경우가 있다. 키워드를 넣은 결과와 안 넣은 결과를 비교해서 판단한다.
        """
        plain = self._try_variant(url, style, begin, end)
        if isinstance(plain, str) or not plain.ok or plain.total_count == 0:
            return True          # 비교 기준 자체가 없으면 판단 보류 (원래대로 진행)
        keyed = self._try_variant(url, style, begin, end, keyword=keyword)
        if isinstance(keyed, str) or not keyed.ok:
            return False
        return keyed.total_count > 0

    def diagnose(self, categories: tuple[str, ...], sample_keyword: str = "자동제어",
                 days: int = 7, end: datetime | None = None) -> list[str]:
        """어떤 경로가 살아있고 건수가 잡히는지 그대로 찍어 준다 (문제 파악용)."""
        end = end or datetime.now()
        begin = end - timedelta(days=days)
        lines = [f"조회창: {begin:%Y-%m-%d} ~ {end:%Y-%m-%d} / 샘플 키워드: {sample_keyword}"]
        for cat in categories:
            op = f"getOpengResultListInfo{CATEGORY_SUFFIX[cat]}PPSSrch"
            lines.append(f"\n[{CATEGORY_LABEL[cat]}] {op}")
            for url, style in self._variants(SCSBID_BASES, op):
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
                lines.append(f"{tag} → OK, 전체 {outcome.total_count}건 / "
                             f"'{sample_keyword}' 검색 {keyed_cnt}건")
                if outcome.items:
                    lines.append("      응답 필드: " + ", ".join(sorted(outcome.items[0])[:14]))
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
            op = f"getOpengResultListInfo{CATEGORY_SUFFIX[cat]}PPSSrch"
            try:
                url, style = self._resolve(f"scsbid:{cat}", SCSBID_BASES, op, end - timedelta(days=7), end)
            except G2BError as exc:
                log.warning("%s 낙찰 조회 불가: %s", CATEGORY_LABEL[cat], exc)
                continue

            bgn_p, end_p, fmt = style
            keyword_ok = self.supports_keyword(url, style, end - timedelta(days=7), end, keywords[0])
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
    return Award(
        notice_no=_pick(item, WINNER_FIELDS["notice_no"]),
        title=_pick(item, WINNER_FIELDS["title"]),
        demand_org=_pick(item, WINNER_FIELDS["demand_org"]),
        winner_name=_pick(item, WINNER_FIELDS["winner_name"]),
        winner_bizno=_pick(item, WINNER_FIELDS["winner_bizno"]),
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
