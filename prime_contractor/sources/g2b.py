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

    # --- 저수준 호출 ---------------------------------------------------------

    def _call(self, url: str, params: dict) -> ApiPage:
        query = {"ServiceKey": self.key, "type": "json", **params}
        resp = self.session.get(url, params=query, timeout=self.timeout)
        resp.raise_for_status()
        time.sleep(self.sleep_sec)          # 명세상 30 tps 제한
        return _parse(resp.text)

    def _variants(self, bases: tuple[str, ...], op: str) -> list[tuple[str, tuple[str, str, str]]]:
        return [(f"{base}/{op}", style) for base in bases for style in DATE_STYLES]

    def _resolve(self, key: str, bases: tuple[str, ...], op: str,
                 begin: datetime, end: datetime) -> tuple[str, tuple[str, str, str]]:
        """살아있는 (URL, 날짜방식) 조합을 찾아 캐시한다."""
        if key in self._resolved:
            return self._resolved[key]

        failures = []
        for url, style in self._variants(bases, op):
            bgn_p, end_p, fmt = style
            params = {"pageNo": "1", "numOfRows": "1", "inqryDiv": "1",
                      bgn_p: begin.strftime(fmt), end_p: end.strftime(fmt)}
            try:
                page = self._call(url, params)
            except (requests.RequestException, ValueError, ET.ParseError) as exc:
                failures.append(f"  {url} [{bgn_p}] → {type(exc).__name__}: {exc}")
                continue
            if page.ok:
                self._resolved[key] = (url, style)
                self.last_probe.append(f"  {url} [{bgn_p}] → OK (총 {page.total_count}건)")
                return url, style
            failures.append(f"  {url} [{bgn_p}] → {page.result_code} {page.result_msg}")

        self.last_probe.extend(failures)
        raise G2BError(f"[{key}] 사용 가능한 오퍼레이션을 찾지 못했습니다:\n" + "\n".join(failures))

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
            for w_begin, w_end in _windows(begin, end, days=15):
                for kw in keywords:
                    params = {"inqryDiv": "1", "bidNtceNm": kw,
                              bgn_p: w_begin.strftime(fmt), end_p: w_end.strftime(fmt)}
                    try:
                        items = list(self._paged(url, params))
                    except (G2BError, requests.RequestException, ValueError, ET.ParseError) as exc:
                        log.warning("[%s/%s] %s ~ %s 조회 실패: %s",
                                    CATEGORY_LABEL[cat], kw,
                                    w_begin.date(), w_end.date(), exc)
                        continue
                    for item in items:
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


def _windows(begin: datetime, end: datetime, days: int = 15):
    """[begin, end] 를 days 간격 구간으로 자른다."""
    cur = begin
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt
