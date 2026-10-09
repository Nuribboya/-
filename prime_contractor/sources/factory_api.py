"""한국산업단지공단 공장등록 생산정보 조회(공공데이터포털 OpenAPI).

    http://apis.data.go.kr/B550624/fctryRegistPrdctnInfo/<오퍼레이션>
        ?serviceKey=…&cmpnyNm=회사명&type=json&numOfRows=100&pageNo=1

공장 목록 파일을 받지 않고 인증키만으로 공장을 찾으려고 쓴다. 이 API 는 회사 이름
(cmpnyNm)으로만 찾을 수 있어서 전체 목록을 한 번에 받지는 못한다 — 그래서 상장사·
계열사 이름으로 하나씩 물어 그 회사들의 공장 주소·생산품을 모은다.

오퍼레이션 이름과 응답 칸 이름을 문서로 확정하지 못해, 오퍼레이션은 몇 가지를
차례로 찔러 보고(처음 한 번), 응답 칸은 이름에 든 말로 찾는다(COLUMN_KEYS). 한 번
받은 회사는 디스크에 남겨 60일 동안 다시 묻지 않는다 — 하루 호출 한도를 넘겨도 다음
날 이어서 받는다.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)

BASE = "https://apis.data.go.kr/B550624"
#: 포털 활용신청 화면의 서비스 주소. 지금은 fctryRegistInfo(공장등록정보), 예전 이름은
#: fctryRegistPrdctnInfo. 오퍼레이션 이름은 문서로 확인하지 못해 차례로 시도한다.
SERVICES = ("fctryRegistInfo", "fctryRegistPrdctnInfo")
#: 문서로 확인하지 못해 차례로 시도한다. 형제 서비스(필지정보)는 getFctryLndpclService.
_OP_NAMES = ("getFctryRegistInfoService", "getFctryRegistInfo", "getFctryInfoService",
             "getFctryInfo", "getFctryRegistService", "getFctryService",
             "getFctryPrdctnService", "getFctryRegistPrdctnService", "getFctryRegistPrdctnInfo",
             "getFctryPrdctnInfo", "getFctryPrdctnInfoService", "getFctryRegistPrdctnInfoService",
             "getFctryPrdctService", "getFctryPrdlstService", "getFctryRegistInfoList",
             "getFctryRegistList", "getFctryList", "getFctryInfoList", "getFctryPrdctnList",
             "getFctryPrdctnInfoList")
#: '서비스/오퍼레이션' 후보. 맨 앞이 포털 활용신청 화면에 적힌 진짜 주소(회사명으로 조회,
#: 2026-10 확인). 나머지는 이름이 또 바뀔 때를 대비한 예비 — 없는 주소는 하루 한도를 쓰지 않는다.
OPERATIONS = (("fctryRegistInfo/getFctryPrdctnService_v2", "fctryRegistInfo/getFctryPrdctnService")
              + tuple(f"{svc}/{op}" for svc in SERVICES for op in _OP_NAMES) + SERVICES)
PROBE_NAME = "삼성전자"
CACHE_DAYS = 60
PAGE_SIZE = 100

#: 응답 칸 → 우리 칸. 칸 이름(소문자)에 이 말이 들어 있으면 그 칸으로 본다. 앞의 말 먼저.
COLUMN_KEYS = {
    "name": ("cmpnynm", "entrpsnm", "fctrynm", "bsnmnm", "corpnm"),
    "address": ("fctryadres", "fctryrdnmadres", "rdnmadres", "lnmadres", "adres", "addr"),
    "products": ("mainproduct", "prdctn", "prdlst", "product", "prdct"),
    "industry": ("indutynm", "indutycodenm", "induty"),
    "ceo": ("rprsntvnm", "rprsntv", "ceo"),
    "phone": ("telno", "tel", "phone"),
    "employees": ("emplycnt", "empcnt", "nmpr", "wrkrcnt", "emply", "empl"),
    "area": ("mnfcturfcltyar", "fctryar", "bildar", "buldar", "lndar", "area"),
}

_QUOTA = "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"
_NOT_REGISTERED = ("SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "SERVICE ACCESS DENIED",
                   "SERVICE_ACCESS_DENIED_ERROR", "PERMISSION_DENIED", "SERVICE_KEY_IS_NULL",
                   "UNREGISTERED_IP_ERROR")
#: 오퍼레이션 이름이 틀렸을 때 포털이 주는 말 — 다음 이름으로 넘어간다.
_WRONG_OPERATION = ("API not found", "Unexpected errors", "NO_OPENAPI_SERVICE_ERROR",
                    "HTTP ROUTING ERROR", "HTTP_ROUTING_ERROR")
#: 정상 결과 코드. 그 밖의 resultCode 는 무엇이 틀렸는지 그대로 보여 준다.
_OK_CODES = ("00", "0", "000", "INFO-000", "INFO-0")


class FactoryApiError(RuntimeError):
    pass


class QuotaExceeded(FactoryApiError):
    """하루 호출 한도를 넘었다 — 받은 데까지는 쓰고, 나머지는 내일."""


class FactoryApi:
    def __init__(self, service_key: str, cache_dir: Path | str | None = None,
                 timeout: float = 20.0, sleep_sec: float = 0.05,
                 session: requests.Session | None = None) -> None:
        if not service_key:
            raise FactoryApiError("공공데이터포털 인증키가 없습니다.")
        # 포털이 주는 Encoding 키를 그대로 넣어도 이중 인코딩되지 않게 한 번 푼다.
        self.key = urllib.parse.unquote(service_key) if "%" in service_key else service_key
        self.timeout = timeout
        self.sleep_sec = sleep_sec
        self._session = session
        self._local = threading.local()
        self.operation: str | None = None
        self.cache_path = Path(cache_dir or Path.home() / ".cache" / "prime_contractor") \
            / "factory_api_v2.json"
        self._lock = threading.Lock()
        self._cache = self._load_cache()
        #: 시험 조회(삼성전자)에서 공장을 실제로 읽었는가. 안 읽혔으면 빈 결과를 남기지 않는다
        #: — 잘못 읽은 '0곳'을 60일 동안 믿으면 안 된다.
        self.verified = any(v.get("rows") for v in self._cache.values())
        #: 오늘 한도를 넘겼으면 그 뒤로는 묻지 않고 바로 멈춘다(받아 둔 것만 쓴다).
        self.quota_hit = ""
        #: 처음 받은 응답 한 줄의 칸 이름 — 칸을 못 읽을 때 진행 상황에 보여 고치려고.
        self.sample_keys: list[str] = []

    # --- 저수준 -----------------------------------------------------------------

    @property
    def session(self):
        if self._session is not None:
            return self._session
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = self._local.session = requests.Session()
        return sess

    def _call(self, operation: str, company: str, page: int = 1) -> tuple[list[dict], int]:
        params = {"serviceKey": self.key, "cmpnyNm": company, "type": "json",
                  "numOfRows": str(PAGE_SIZE), "pageNo": str(page)}
        resp = self.session.get(f"{BASE}/{operation}", params=params, timeout=self.timeout)
        time.sleep(self.sleep_sec)
        text = resp.text or ""
        if _QUOTA in text:
            self.quota_hit = "공공데이터포털 하루 호출 한도를 넘었습니다 (개발계정은 하루 1,000번)."
            raise QuotaExceeded(self.quota_hit)
        if any(code in text for code in _NOT_REGISTERED):
            raise FactoryApiError(
                "공공데이터포털에서 '한국산업단지공단 공장등록정보(생산정보) 조회서비스'를 활용신청해야 "
                "합니다(나라장터 때 쓰던 같은 계정·같은 키면 됩니다). 신청 직후면 1~2시간 뒤에 "
                f"다시 해 보세요. 받은 답: {_snippet(text)}")
        if resp.status_code in (404, 500) or any(w in text for w in _WRONG_OPERATION):
            raise LookupError(f"{operation}: HTTP {resp.status_code} {_snippet(text)}")
        if resp.status_code >= 400:
            raise FactoryApiError(f"HTTP {resp.status_code}: {_snippet(text)}")
        try:
            payload = resp.json()
        except ValueError:
            payload = _xml_payload(text)
            if payload is None:
                raise FactoryApiError(f"응답을 읽지 못했습니다: {_snippet(text)}") from None
        code, message = _result(payload)
        if code and code not in _OK_CODES and code not in ("03",):     # 03 = 자료 없음
            raise FactoryApiError(f"공공데이터포털 오류 {code} {message}".strip())
        return _items(payload), _total(payload)

    def _resolve(self) -> str:
        if self.operation:
            return self.operation
        tried = []
        for op in OPERATIONS:
            try:
                self._call(op, PROBE_NAME)
            except LookupError as exc:
                tried.append(str(exc))
                continue
            self.operation = op
            log.info("공장 조회 오퍼레이션: %s", op)
            return op
        raise FactoryApiError(
            f"공장등록 조회 API 주소를 찾지 못했습니다 ({len(tried)}가지 시도). "
            "공공데이터포털 → 마이페이지 → 활용신청 현황 → 공장등록생산정보조회서비스 에 "
            "나온 '요청주소'를 캡처해 보내 주세요.\n첫 답: " + (tried[0] if tried else ""))

    def check(self) -> int:
        """본격적으로 묻기 전에 '삼성전자'로 한 번 시험한다. 읽은 공장 수.

        0곳이면 키·주소·응답 모양 중 무언가가 틀린 것 — 수천 번 헛물어 하루 한도를 쓰기
        전에 받은 답을 그대로 보여 주고 멈춘다.
        """
        op = self._resolve()
        resp_items, _total_count = self._call(op, PROBE_NAME)
        if resp_items and not self.sample_keys:
            self.sample_keys = sorted(resp_items[0])
        found = [r for r in (_record(i) for i in resp_items) if r.get("name")]
        if not found:
            keys = ", ".join(self.sample_keys) or "없음"
            raise FactoryApiError(
                f"시험 조회('{PROBE_NAME}')에서 공장을 하나도 못 읽었습니다 (오퍼레이션 {op}, "
                f"받은 칸: {keys}). 이 창을 캡처해 보내 주세요.")
        self.verified = True
        return len(found)

    # --- 회사 이름으로 공장 찾기 -------------------------------------------------

    def factories_of(self, company: str, today: date | None = None) -> list[dict[str, str]]:
        """이 회사 이름으로 등록된 공장 [{name, address, products, …}]. 60일 캐시."""
        today = today or date.today()
        with self._lock:
            hit = self._cache.get(company)
        if hit and date.fromisoformat(hit["at"]) >= today - timedelta(days=CACHE_DAYS):
            return hit["rows"]
        if self.quota_hit:
            raise QuotaExceeded(self.quota_hit)
        op = self._resolve()
        rows: list[dict[str, str]] = []
        page = 1
        while True:
            items, total = self._call(op, company, page)
            if items and not self.sample_keys:
                self.sample_keys = sorted(items[0])
            rows += [_record(item) for item in items]
            if not items or page * PAGE_SIZE >= total or page >= 10:
                break
            page += 1
        rows = [r for r in rows if r.get("name")]
        if rows or self.verified:
            with self._lock:
                self._cache[company] = {"at": today.isoformat(), "rows": rows}
        return rows

    def save_cache(self) -> None:
        with self._lock:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, ensure_ascii=False),
                                       encoding="utf-8")

    def cached(self, company: str) -> bool:
        return company in self._cache

    def _load_cache(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


def _items(payload) -> list[dict]:
    """data.go.kr 응답 어디에 있든 '칸 이름 → 값' 묶음의 목록을 찾는다."""
    if isinstance(payload, list):
        if payload and all(isinstance(x, dict) for x in payload) and _flat(payload[0]):
            return payload
        for x in payload:
            found = _items(x)
            if found:
                return found
        return []
    if isinstance(payload, dict):
        for key in ("items", "item", "data", "list", "body", "response"):
            if key in payload:
                found = _items(payload[key])
                if found:
                    return found
        if _flat(payload) and len(payload) >= 3:
            return [payload]
        for value in payload.values():
            found = _items(value)
            if found:
                return found
    return []


def _snippet(text: str, size: int = 200) -> str:
    """오류 화면에 보일 응답 앞부분 — 키가 들어 있으면 가린다."""
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"(?i)(servicekey=)[^&\s\"'<]+", r"\1***", text)
    return text[:size] or "(빈 응답)"


def _xml_payload(text: str):
    """JSON 대신 XML 로 온 답을 {'items': [...], 'totalCount': n, 'resultCode': …} 로."""
    try:
        root = ET.fromstring(text.strip().encode("utf-8"))
    except ET.ParseError:
        return None
    items = [{child.tag: (child.text or "").strip() for child in item}
             for item in root.iter("item")]
    payload: dict = {"items": items}
    for tag in ("totalCount", "resultCode", "resultMsg", "returnReasonCode", "returnAuthMsg"):
        node = next(root.iter(tag), None)
        if node is not None:
            payload[tag] = (node.text or "").strip()
    return payload


def _result(payload) -> tuple[str, str]:
    """응답 머리의 결과 코드·문구. 없으면 ('', '')."""
    text = json.dumps(payload, ensure_ascii=False)
    code = re.search(r'"resultCode"\s*:\s*"?([\w-]+)', text)
    msg = re.search(r'"resultMsg"\s*:\s*"([^"]*)', text)
    return (code.group(1) if code else "", msg.group(1) if msg else "")


def _flat(d: dict) -> bool:
    return all(not isinstance(v, (dict, list)) for v in d.values())


def _total(payload) -> int:
    text = json.dumps(payload, ensure_ascii=False)
    m = re.search(r'"totalCount"\s*:\s*"?(\d+)', text)
    return int(m.group(1)) if m else 0


def _record(item: dict) -> dict[str, str]:
    lowered = {str(k).lower(): ("" if v is None else str(v).strip()) for k, v in item.items()}
    record: dict[str, str] = {}
    used: set[str] = set()
    for field, hints in COLUMN_KEYS.items():
        for hint in hints:
            key = next((k for k in lowered if hint in k and k not in used and lowered[k]), None)
            if key:
                record[field] = lowered[key]
                used.add(key)
                break
    return record
