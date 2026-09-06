"""나라장터(조달청) 입찰공고정보서비스 - data.go.kr 오픈API 얇은 클라이언트.

data.go.kr에서 '나라장터 입찰공고정보서비스'를 신청하면 서비스키를 받을 수 있고,
G2B_API_KEY 환경변수로 설정해서 쓴다.

주의: data.go.kr 스펙은 물품/공사/용역별로 오퍼레이션이 나뉘어 있고 가끔 개정되니,
실제로 쓰기 전에 신청한 서비스의 최신 스펙(오퍼레이션명, 파라미터)을 data.go.kr에서
직접 확인해서 operation 인자를 맞춰야 한다.
"""
from __future__ import annotations

import requests

BASE_URL = "http://apis.data.go.kr/1230000/ad/BidPublicInfoService"


def search_bid_notices(
    api_key: str,
    keyword: str,
    operation: str = "getBidPblancListInfoServc",
    num_rows: int = 50,
    page_no: int = 1,
) -> list[dict]:
    """키워드(예: '제어반', '자동화설비')가 포함된 입찰공고를 검색한다."""
    resp = requests.get(
        f"{BASE_URL}/{operation}",
        params={
            "serviceKey": api_key,
            "numOfRows": num_rows,
            "pageNo": page_no,
            "type": "json",
            "bidNtceNm": keyword,
        },
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("response", {}).get("body", {}).get("items", [])
    if not items:
        return []
    return items if isinstance(items, list) else [items]
