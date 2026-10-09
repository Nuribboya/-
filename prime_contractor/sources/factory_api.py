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
from datetime import date, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)

BASE = "http://apis.data.go.kr/B550624/fctryRegistPrdctnInfo"
#: 문서로 확인하지 못해 차례로 시도한다. 형제 서비스(필지정보)는 getFctryLndpclService.
OPERATIONS = ("getFctryPrdctnService", "getFctryRegistPrdctnService",
              "getFctryRegistPrdctnInfo", "getFctryPrdctnInfo", "getFctryPrdctnInfoService")
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
                   "SERVICE_ACCESS_DENIED_ERROR")


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
            / "factory_api.json"
        self._lock = threading.Lock()
        self._cache = self._load_cache()
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
            raise QuotaExceeded("공공데이터포털 하루 호출 한도를 넘었습니다.")
        if any(code in text for code in _NOT_REGISTERED):
            raise FactoryApiError(
                "공공데이터포털에서 '한국산업단지공단_공장등록생산정보조회서비스'를 활용신청해야 "
                "합니다(나라장터 때 쓰던 같은 계정·같은 키면 됩니다).")
        if resp.status_code == 404 or "API not found" in text or "Unexpected errors" in text:
            raise LookupError(operation)
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as exc:
            raise FactoryApiError(f"응답을 읽지 못했습니다: {text[:120]}") from exc
        return _items(payload), _total(payload)

    def _resolve(self) -> str:
        if self.operation:
            return self.operation
        for op in OPERATIONS:
            try:
                self._call(op, PROBE_NAME)
            except LookupError:
                continue
            self.operation = op
            log.info("공장 조회 오퍼레이션: %s", op)
            return op
        raise FactoryApiError("공장등록 조회 API 주소를 찾지 못했습니다. 화면을 캡처해 보내 주세요.")

    # --- 회사 이름으로 공장 찾기 -------------------------------------------------

    def factories_of(self, company: str, today: date | None = None) -> list[dict[str, str]]:
        """이 회사 이름으로 등록된 공장 [{name, address, products, …}]. 60일 캐시."""
        today = today or date.today()
        with self._lock:
            hit = self._cache.get(company)
        if hit and date.fromisoformat(hit["at"]) >= today - timedelta(days=CACHE_DAYS):
            return hit["rows"]
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
