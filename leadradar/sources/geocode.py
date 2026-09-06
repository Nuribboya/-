"""카카오 로컬 API로 주소를 위경도로 변환하고, 두 좌표 사이 거리를 계산한다.

https://developers.kakao.com 에서 애플리케이션을 만들고 REST API 키를 발급받아
KAKAO_API_KEY 환경변수로 설정한 뒤 사용한다.
"""
from __future__ import annotations

import math
from typing import Optional

import requests

_GEOCODE_URL = "https://dapi.kakao.com/v2/local/search/address.json"


def geocode_address(api_key: str, address: str) -> Optional[tuple[float, float]]:
    """주소를 (위도, 경도)로 변환한다. 주소를 못 찾으면 None을 반환한다."""
    resp = requests.get(
        _GEOCODE_URL,
        headers={"Authorization": f"KakaoAK {api_key}"},
        params={"query": address},
        timeout=10,
    )
    resp.raise_for_status()
    documents = resp.json().get("documents", [])
    if not documents:
        return None
    doc = documents[0]
    return float(doc["y"]), float(doc["x"])  # y=위도, x=경도


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 사이의 직선거리(km, 지구 곡률 반영)를 계산한다."""
    earth_radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return round(earth_radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)
