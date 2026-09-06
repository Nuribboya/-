"""DART 공개데이터에서 키워드로 후보 회사를 찾아 JSON으로 저장한다.

사용법:

    python -m leadradar.discover_cli --keywords 반도체 자동화 제어반 --out discovered.json

DART_API_KEY 환경변수가 설정되어 있어야 한다 (https://opendart.fss.or.kr 에서 발급).
KAKAO_API_KEY(https://developers.kakao.com 에서 발급) 환경변수를 추가로 설정하면
회사 주소를 위경도로 변환해 --origin 기준 직선거리(km)도 함께 채운다. 없으면
distance_km은 비워둔 채로 진행된다.

여기서 만든 JSON은 leadradar.cli의 --candidates 인자로 그대로 넣으면 된다:

    python -m leadradar.cli --config leadradar/config.yaml --candidates discovered.json --out candidates_scored.csv
"""
from __future__ import annotations

import argparse
import os

from .discovery import discover_candidates, save_candidates_json


def main() -> None:
    parser = argparse.ArgumentParser(description="DART 공개데이터에서 원청 후보 발굴")
    parser.add_argument(
        "--keywords",
        nargs="+",
        required=True,
        help="회사명에서 찾을 키워드들 (예: --keywords 반도체 자동화 제어반)",
    )
    parser.add_argument("--year", type=int, default=None, help="재무정보 기준 연도 (기본: 작년)")
    parser.add_argument("--origin", default="경기도 안성시", help="거리 계산 기준 주소 (기본: 안성)")
    parser.add_argument("--out", default="discovered_candidates.json", help="결과 JSON 경로")
    args = parser.parse_args()

    api_key = os.environ["DART_API_KEY"]
    kakao_api_key = os.environ.get("KAKAO_API_KEY")
    if not kakao_api_key:
        print("KAKAO_API_KEY가 설정되어 있지 않아 거리(distance_km) 계산은 건너뜁니다.")

    candidates = discover_candidates(
        api_key,
        args.keywords,
        year=args.year,
        kakao_api_key=kakao_api_key,
        origin_address=args.origin,
    )
    save_candidates_json(candidates, args.out)
    print(f"{len(candidates)}개 후보를 {args.out} 에 저장했습니다.")


if __name__ == "__main__":
    main()
