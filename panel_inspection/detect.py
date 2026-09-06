"""학습된 YOLO 모델로 판넬 사진에서 부품을 탐지한다.

사용법:
    python -m panel_inspection.detect --model runs/detect/train/weights/best.pt --image panel.jpg

ultralytics 패키지가 필요하다: pip install -r panel_inspection/requirements.txt
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def detect(model_path: str, image_path: str, conf: float = 0.4) -> list[dict]:
    from ultralytics import YOLO  # 무거운 의존성이라 실제로 쓸 때만 임포트

    model = YOLO(model_path)
    results = model.predict(image_path, conf=conf, verbose=False)

    detections = []
    for result in results:
        for box in result.boxes:
            detections.append(
                {
                    "class": result.names[int(box.cls[0])],
                    "confidence": float(box.conf[0]),
                    "bbox": [float(x) for x in box.xyxy[0].tolist()],
                }
            )
    return detections


def main() -> None:
    parser = argparse.ArgumentParser(description="판넬 사진에서 부품 탐지")
    parser.add_argument("--model", required=True, help="학습된 모델 가중치 경로 (.pt)")
    parser.add_argument("--image", required=True, help="검사할 판넬 사진 경로")
    parser.add_argument("--conf", type=float, default=0.4, help="탐지 신뢰도 임계값 (기본 0.4)")
    parser.add_argument("--out", default=None, help="탐지 결과 JSON 저장 경로 (선택)")
    args = parser.parse_args()

    detections = detect(args.model, args.image, args.conf)
    output = json.dumps(detections, ensure_ascii=False, indent=2)
    print(output)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
