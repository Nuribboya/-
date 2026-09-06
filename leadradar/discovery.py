"""DART 공개데이터에서 키워드에 맞는 후보 회사를 찾아 Candidate 리스트로 만든다.

전체 기업(10만 개 이상)을 다 LLM으로 채점하면 비용/시간이 너무 크기 때문에,
회사명 키워드로 먼저 좁힌 뒤 그 후보들만 재무정보를 붙여서 내보낸다.
LLM 채점(scoring.py)은 여기서 하지 않고, 여기서는 후보를 추려서 Candidate로
만드는 것까지만 한다 - 이 결과를 leadradar.cli의 --candidates 로 넘기면 된다.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .models import Candidate
from .sources.dart import (
    CorpCode,
    fetch_company_overview,
    fetch_corp_codes,
    fetch_financial_highlights,
    parse_revenue_growth_pct,
    search_corps_by_keyword,
)
from .sources.geocode import geocode_address, haversine_distance_km

DEFAULT_CORP_CODE_CACHE = ".dart_corpcodes_cache.json"


def load_or_fetch_corp_codes(
    api_key: str,
    cache_path: str | Path = DEFAULT_CORP_CODE_CACHE,
) -> list[CorpCode]:
    """전체 기업 고유번호 목록을 로컬 캐시에서 읽거나, 없으면 DART에서 받아 캐싱한다.

    이 목록은 자주 안 바뀌고 받는 데 시간이 걸리니, 한 번 받으면 파일로 저장해두고
    재사용한다. 강제로 새로 받고 싶으면 캐시 파일을 지우면 된다.
    """
    cache_file = Path(cache_path)
    if cache_file.exists():
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        return [CorpCode(**item) for item in data]

    corp_codes = fetch_corp_codes(api_key)
    cache_file.write_text(
        json.dumps([asdict(c) for c in corp_codes], ensure_ascii=False),
        encoding="utf-8",
    )
    return corp_codes


def discover_candidates(
    api_key: str,
    keywords: list[str],
    year: int | None = None,
    limit_per_keyword: int = 50,
    kakao_api_key: str | None = None,
    origin_address: str = "경기도 안성시",
) -> list[Candidate]:
    """키워드로 회사명을 좁힌 뒤, 상장사만 재무정보를 붙여 Candidate 리스트를 만든다.

    비상장사는 DART에 재무제표가 없는 경우가 많아 이번 단계에서는 상장사(stock_code
    있는 회사)만 다룬다. kakao_api_key를 주면 회사 주소를 위경도로 변환해
    origin_address(기본: 안성) 기준 직선거리(km)도 함께 채운다.
    """
    year = year or (date.today().year - 1)
    corp_codes = load_or_fetch_corp_codes(api_key)

    matched: dict[str, CorpCode] = {}
    for keyword in keywords:
        for corp in search_corps_by_keyword(corp_codes, keyword)[:limit_per_keyword]:
            matched[corp.corp_code] = corp

    origin_coords = geocode_address(kakao_api_key, origin_address) if kakao_api_key else None

    candidates: list[Candidate] = []
    for corp in matched.values():
        if not corp.stock_code:
            continue

        overview = fetch_company_overview(api_key, corp.corp_code)
        induty_code = overview.get("induty_code", "정보없음")
        business_description = f"{overview.get('corp_name', corp.corp_name)} (업종코드 {induty_code})"

        try:
            financials = fetch_financial_highlights(api_key, corp.corp_code, year)
            revenue_growth_pct = parse_revenue_growth_pct(financials)
        except Exception:
            revenue_growth_pct = None

        distance_km = None
        address = overview.get("adres")
        if origin_coords and address:
            try:
                coords = geocode_address(kakao_api_key, address)
            except Exception:
                coords = None
            if coords:
                distance_km = haversine_distance_km(*origin_coords, *coords)

        candidates.append(
            Candidate(
                name=corp.corp_name,
                business_description=business_description,
                revenue_growth_pct=revenue_growth_pct,
                distance_km=distance_km,
                source="dart",
            )
        )

    return candidates


def save_candidates_json(candidates: list[Candidate], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps([asdict(c) for c in candidates], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
