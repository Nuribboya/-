"""전자공시(OpenDART) - 후보 업체의 업종코드·주소·규모 보강.

낙찰 데이터에는 업체명·사업자번호밖에 없어서 '업종이 겹치는가' 를 판정할
근거가 부족하다. DART 기업개황(company.json)의 업종코드(induty_code)와
주소(adres)를 붙여 판정 정확도를 올린다.

한계: DART 는 공시대상 법인만 담고 있어 비상장 중소 업체는 조회되지 않는다.
못 찾은 업체는 상호·공고명 키워드로만 판정된다(= 이름 기반 fallback).
"""
from __future__ import annotations

import io
import json
import logging
import threading
import time
from datetime import date
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import requests

from prime_contractor.industry import normalize_name

log = logging.getLogger(__name__)

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"
#: 정기보고서 '타법인 출자현황' — 이 회사가 지분을 가진 회사들(자회사·계열사 찾기에 쓴다).
INVEST_URL = "https://opendart.fss.or.kr/api/otrCprInvstmntSttus.json"
ANNUAL_REPORT = "11011"
#: 출자현황 한 번에 기다리는 최대 초. DART 가 느려지면 30초씩 4천 번 기다리게 된다.
INVEST_TIMEOUT = 10.0
DEFAULT_CACHE = Path.home() / ".cache" / "prime_contractor"


class DartError(RuntimeError):
    pass


class DartClient:
    def __init__(self, api_key: str, cache_dir: Path | str = DEFAULT_CACHE,
                 timeout: float = 30.0, sleep_sec: float = 0.05,
                 session: requests.Session | None = None) -> None:
        if not api_key:
            raise DartError("DART API 키가 없습니다. DART_API_KEY 를 설정하세요.")
        self.key = api_key
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.sleep_sec = sleep_sec
        self.session = session or requests.Session()
        self._shared_session = session is not None       # 밖에서 넘긴 세션(시험용)은 모든 스레드가 쓴다
        self._index: dict[str, str] | None = None
        self._listed: list[tuple[str, str, str]] = []
        self._company_cache: dict[str, dict] = self._load_company_cache()
        self._invest_cache: dict[str, list[dict]] | None = None
        self._invest_lock = threading.Lock()
        self._local = threading.local()
        #: DART 가 '요청 한도 초과'(020)를 주면 그날은 더 묻지 않는다 — 계속 물어도 막히기만 한다.
        self.invest_stopped = ""

    # --- 고유번호 인덱스 -----------------------------------------------------

    @property
    def corp_index(self) -> dict[str, str]:
        """정규화 상호 → corp_code. 최초 1회만 내려받아 캐시한다."""
        if self._index is None:
            self._index = self._load_corp_index()
        return self._index

    @property
    def listed_companies(self) -> list[tuple[str, str, str]]:
        """상장사 (상호, 고유번호, 종목코드) 목록.

        DART 전체는 10만 곳이 넘어 개황을 하나씩 받기엔 너무 많다. 종목코드가
        있는 상장사(2천여 곳)로 좁히면 업종코드 스크리닝이 현실적인 시간에 끝나고,
        원청이 될 만한 규모의 회사는 대부분 여기 들어 있다.
        """
        if not self._listed:
            self.corp_index          # 인덱스를 만들면서 상장사 목록도 채워진다
        return self._listed

    def _load_corp_index(self) -> dict[str, str]:
        cached = self.cache_dir / "corp_index.json"
        if cached.exists():
            raw = json.loads(cached.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("version") == 2:
                self._listed = [tuple(row) for row in raw["listed"]]
                return raw["by_name"]
            log.info("옛 형식 캐시를 새로 받습니다: %s", cached)

        resp = self.session.get(CORP_CODE_URL, params={"crtfc_key": self.key}, timeout=self.timeout)
        resp.raise_for_status()
        if not resp.content[:2] == b"PK":
            # 키 오류 등은 zip 이 아니라 XML 에러로 온다.
            raise DartError(f"고유번호 파일을 받지 못했습니다: {resp.text[:200]}")

        index: dict[str, str] = {}
        listed: list[tuple[str, str, str]] = []
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            with zf.open(zf.namelist()[0]) as fh:
                root = ET.parse(fh).getroot()
        for node in root.findall("list"):
            name = (node.findtext("corp_name") or "").strip()
            code = (node.findtext("corp_code") or "").strip()
            stock = (node.findtext("stock_code") or "").strip()
            if not (name and code):
                continue
            index.setdefault(normalize_name(name), code)
            if stock:
                listed.append((name, code, stock))

        self._listed = listed
        cached.write_text(
            json.dumps({"version": 2, "by_name": index,
                        "listed": [list(r) for r in listed]}, ensure_ascii=False),
            encoding="utf-8")
        log.info("DART 고유번호 %s건(상장 %s곳) 캐시: %s", len(index), len(listed), cached)
        return index

    # --- 기업개황 디스크 캐시 -------------------------------------------------

    @property
    def _company_cache_path(self):
        return self.cache_dir / "companies.json"

    def _load_company_cache(self) -> dict[str, dict]:
        path = self.cache_dir / "companies.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                log.warning("기업개황 캐시가 깨져 새로 만듭니다: %s", path)
        return {}

    def save_company_cache(self) -> None:
        """받아 둔 기업개황을 디스크에 남긴다. 다음 실행이 훨씬 빨라진다."""
        self._company_cache_path.write_text(
            json.dumps(self._company_cache, ensure_ascii=False), encoding="utf-8")

    def _thread_session(self):
        """requests 세션은 스레드끼리 나눠 쓰지 않는다 — 스레드마다 하나씩."""
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._local.session = (
                self.session if self._shared_session
                or threading.current_thread() is threading.main_thread() else requests.Session())
        return session

    # --- 기업개황 ------------------------------------------------------------

    def company(self, corp_code: str) -> dict:
        if corp_code in self._company_cache:
            return self._company_cache[corp_code]
        resp = self._thread_session().get(
            COMPANY_URL, params={"crtfc_key": self.key, "corp_code": corp_code}, timeout=self.timeout
        )
        resp.raise_for_status()
        time.sleep(self.sleep_sec)
        data = resp.json()
        if data.get("status") != "000":
            raise DartError(f"{corp_code}: {data.get('status')} {data.get('message')}")
        self._company_cache[corp_code] = data
        return data

    # --- 타법인 출자현황 (계열사) ----------------------------------------------

    @property
    def _invest_cache_path(self) -> Path:
        return self.cache_dir / "investments.json"

    def _invest(self) -> dict[str, list[dict]]:
        if self._invest_cache is None:
            path = self._invest_cache_path
            try:
                self._invest_cache = (json.loads(path.read_text(encoding="utf-8"))
                                      if path.exists() else {})
            except ValueError:
                self._invest_cache = {}
        return self._invest_cache

    def has_investments(self, corp_code: str) -> bool:
        return corp_code in self._invest()

    def investments(self, corp_code: str, today: date | None = None) -> list[dict]:
        """이 회사가 지분을 가진 회사 [{name, ratio, purpose}]. 최근 사업보고서 기준.

        올해 3월에 낸 작년 사업보고서부터 찾고, 없으면 그 전 해. 한 번 받으면 디스크에
        남겨 다음엔 바로 쓴다(여러 스레드에서 불러도 된다).
        """
        cache = self._invest()
        if corp_code in cache:
            return cache[corp_code]
        if self.invest_stopped:
            raise DartError(self.invest_stopped)
        today = today or date.today()
        session = self._thread_session()
        rows: list[dict] = []
        for year in (today.year - 1, today.year - 2):
            resp = session.get(INVEST_URL, params={
                "crtfc_key": self.key, "corp_code": corp_code, "bsns_year": str(year),
                "reprt_code": ANNUAL_REPORT}, timeout=min(self.timeout, INVEST_TIMEOUT))
            resp.raise_for_status()
            time.sleep(self.sleep_sec)
            data = resp.json()
            status = data.get("status")
            if status == "013":                 # 그 해 자료 없음 → 그 전 해
                continue
            if status == "020":
                self.invest_stopped = "DART 하루 요청 한도를 넘었습니다 — 내일 다시 누르면 이어서 확인합니다."
                raise DartError(self.invest_stopped)
            if status != "000":
                raise DartError(f"{corp_code}: {status} {data.get('message')}")
            for item in data.get("list") or []:
                name = (item.get("inv_prm") or "").strip()
                if not name or name in ("-", "합계", "합 계", "계"):
                    continue
                rows.append({"name": name, "ratio": _ratio(item.get("trmend_blce_qota_rt")),
                             "purpose": (item.get("invstmnt_purps") or "").strip()})
            break
        with self._invest_lock:
            cache[corp_code] = rows
        return rows

    def cached_investments(self, corp_code: str) -> list[dict]:
        """이미 받아 둔 출자현황만 — 인터넷에 묻지 않는다(못 받은 곳은 빈 목록)."""
        return self._invest().get(corp_code, [])

    def save_investment_cache(self) -> None:
        with self._invest_lock:
            self._invest_cache_path.write_text(
                json.dumps(self._invest(), ensure_ascii=False), encoding="utf-8")

    def lookup(self, name: str) -> dict | None:
        """상호로 기업개황을 찾는다. 없으면 None."""
        code = self.corp_index.get(normalize_name(name))
        if not code:
            return None
        try:
            return self.company(code)
        except (DartError, requests.RequestException, ValueError) as exc:
            log.warning("DART 조회 실패 %s(%s): %s", name, code, exc)
            return None


def _ratio(text) -> float:
    """'51.00' · '-' · '' → 51.0 · 0.0."""
    try:
        return float(str(text).replace(",", "").replace("%", "").strip())
    except ValueError:
        return 0.0
