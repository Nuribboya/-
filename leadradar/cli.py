"""사용법:

    python -m leadradar.cli --config leadradar/config.yaml \\
        --candidates leadradar/fixtures/sample_candidates.json \\
        --out candidates_scored.csv

ANTHROPIC_API_KEY 환경변수가 설정되어 있어야 한다.
"""
from __future__ import annotations

import argparse

from .config import LeadRadarConfig
from .pipeline import load_candidates_from_json, run, write_results_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="원청 후보 실시간 적합도 분석")
    parser.add_argument("--config", required=True, help="설정 YAML 경로")
    parser.add_argument("--candidates", required=True, help="후보 회사 JSON 경로")
    parser.add_argument("--out", default="candidates_scored.csv", help="결과 CSV 경로")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="앞에서부터 이 개수만 채점 (비용 확인용 테스트). 기본: 전체",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=0,
        help="이 점수 미만인 회사는 결과(CSV/출력)에서 제외 (부적합한 회사 소거용). 기본: 0(전체 표시)",
    )
    args = parser.parse_args()

    config = LeadRadarConfig.from_yaml(args.config)
    candidates = load_candidates_from_json(args.candidates)
    if args.limit is not None:
        candidates = candidates[: args.limit]
    print(f"{len(candidates)}개 후보 채점 시작...")
    results = run(config, candidates, show_progress=True)
    results = [r for r in results if r.fit_score >= args.min_score]
    write_results_csv(results, args.out)

    for r in results:
        flag = " [관계사 충돌 주의]" if r.conflicts_with_excluded_client else ""
        print(f"{r.fit_score:3d}  {r.candidate.name}{flag} - {r.reasoning}")


if __name__ == "__main__":
    main()
