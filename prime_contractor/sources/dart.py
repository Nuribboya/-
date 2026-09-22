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
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import requests

from prime_contractor.industry import normalize_name

log = logging.getLogger(__name__)

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"
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
        self._index: dict[str, str] | None = None
        self._company_cache: dict[str, dict] = {}

    # --- 고유번호 인덱스 -----------------------------------------------------

    @property
    def corp_index(self) -> dict[str, str]:
        """정규화 상호 → corp_code. 최초 1회만 내려받아 캐시한다."""
        if self._index is None:
            self._index = self._load_corp_index()
        return self._index

    def _load_corp_index(self) -> dict[str, str]:
        cached = self.cache_dir / "corp_index.json"
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))

        resp = self.session.get(CORP_CODE_URL, params={"crtfc_key": self.key}, timeout=self.timeout)
        resp.raise_for_status()
        if not resp.content[:2] == b"PK":
            # 키 오류 등은 zip 이 아니라 XML 에러로 온다.
            raise DartError(f"고유번호 파일을 받지 못했습니다: {resp.text[:200]}")

        index: dict[str, str] = {}
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            with zf.open(zf.namelist()[0]) as fh:
                root = ET.parse(fh).getroot()
        for node in root.findall("list"):
            name = (node.findtext("corp_name") or "").strip()
            code = (node.findtext("corp_code") or "").strip()
            if name and code:
                index.setdefault(normalize_name(name), code)

        cached.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        log.info("DART 고유번호 %s건 캐시: %s", len(index), cached)
        return index

    # --- 기업개황 ------------------------------------------------------------

    def company(self, corp_code: str) -> dict:
        if corp_code in self._company_cache:
            return self._company_cache[corp_code]
        resp = self.session.get(
            COMPANY_URL, params={"crtfc_key": self.key, "corp_code": corp_code}, timeout=self.timeout
        )
        resp.raise_for_status()
        time.sleep(self.sleep_sec)
        data = resp.json()
        if data.get("status") != "000":
            raise DartError(f"{corp_code}: {data.get('status')} {data.get('message')}")
        self._company_cache[corp_code] = data
        return data

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
