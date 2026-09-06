"""DART(전자공시시스템) Open API에서 후보 원청 기업 정보를 가져오는 얇은 클라이언트.

https://opendart.fss.or.kr 에서 무료로 API 키(crtfc_key)를 발급받아
DART_API_KEY 환경변수로 설정한 뒤 사용한다.
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

import requests

BASE_URL = "https://opendart.fss.or.kr/api"


@dataclass
class CorpCode:
    corp_code: str
    corp_name: str
    stock_code: str


def fetch_corp_codes(api_key: str) -> list[CorpCode]:
    """전체 기업의 고유번호 목록을 내려받는다. 자주 안 바뀌니 한 번 받아서 캐싱해두고 재사용하는 걸 권장."""
    resp = requests.get(f"{BASE_URL}/corpCode.xml", params={"crtfc_key": api_key}, timeout=30)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_bytes = zf.read(zf.namelist()[0])

    root = ET.fromstring(xml_bytes)
    return [
        CorpCode(
            corp_code=(item.findtext("corp_code") or "").strip(),
            corp_name=(item.findtext("corp_name") or "").strip(),
            stock_code=(item.findtext("stock_code") or "").strip(),
        )
        for item in root.iter("list")
    ]


def search_corps_by_keyword(corp_codes: list[CorpCode], keyword: str) -> list[CorpCode]:
    """회사명에 특정 키워드(예: '반도체', '검사장비')가 들어간 후보만 필터링한다."""
    return [c for c in corp_codes if keyword in c.corp_name]


def fetch_company_overview(api_key: str, corp_code: str) -> dict:
    resp = requests.get(
        f"{BASE_URL}/company.json",
        params={"crtfc_key": api_key, "corp_code": corp_code},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_financial_highlights(
    api_key: str,
    corp_code: str,
    year: int,
    reprt_code: str = "11011",
) -> dict:
    """reprt_code 11011=사업보고서(연간). 매출/영업이익 등 재무제표 원본을 반환한다."""
    resp = requests.get(
        f"{BASE_URL}/fnlttSinglAcntAll.json",
        params={
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": reprt_code,
            "fs_div": "CFS",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
