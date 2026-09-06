"""판넬 사진 한 장을 검사해서 빠진/초과 부품을 바로 보여준다 (탐지 + BOM 대조).

사용법:
    python -m panel_inspection.check \\
        --model runs/detect/train/weights/best.pt \\
        --image panel.jpg \\
        --bom panel_inspection/bom_example.yaml
"""
from __future__ import annotations

import argparse

from .bom import compare_to_bom, counts_from_detections, load_bom
from .detect import detect


def main() -> None:
    parser = argparse.ArgumentParser(description="판넬 사진 검사 (부품탐지 + BOM 대조)")
    parser.add_argument("--model", required=True, help="학습된 모델 가중치 경로 (.pt)")
    parser.add_argument("--image", required=True, help="검사할 판넬 사진 경로")
    parser.add_argument("--bom", required=True, help="BOM YAML 경로 (panel_inspection/bom_example.yaml 참고)")
    parser.add_argument("--conf", type=float, default=0.4, help="탐지 신뢰도 임계값 (기본 0.4)")
    args = parser.parse_args()

    detections = detect(args.model, args.image, args.conf)
    detected_counts = counts_from_detections(detections)
    bom = load_bom(args.bom)
    diff = compare_to_bom(detected_counts, bom)

    if diff.is_complete:
        print("[OK] BOM 기준 빠진 부품 없음")
    else:
        print("[!!] 빠진 부품:")
        for part, count in diff.missing.items():
            print(f"  - {part}: {count}개 부족")

    if diff.extra:
        print("참고: BOM에 없거나 더 탐지된 부품:")
        for part, count in diff.extra.items():
            print(f"  - {part}: +{count}")


if __name__ == "__main__":
    main()
