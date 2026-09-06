"""라벨링된 판넬 사진 데이터셋(YOLO 포맷)으로 부품 탐지 모델을 학습시킨다.

데이터 준비 순서:
1. Roboflow(roboflow.com, 무료) 같은 도구로 판넬 사진을 올리고 부품별로
   바운딩박스 라벨링 ("차단기", "릴레이", "단자대" 등 클래스 이름 직접 정하기)
2. "YOLOv8" 포맷으로 export하면 images/, labels/, data.yaml이 담긴 zip을 줌
3. 압축 풀고 이 스크립트에 data.yaml 경로를 넘겨서 학습

    python -m panel_inspection.train --data path/to/data.yaml

사전학습된 YOLOv8n(가장 가벼운 버전)을 우리 데이터로 파인튜닝하는 방식이라,
사진이 10~20장 정도로 적어도 일단 돌아가는 모델은 나온다 - 다만 정확도는
사진이 더 쌓일수록(각 부품이 다양한 각도/조명으로 최소 수십 장씩) 좋아진다.

ultralytics 패키지가 필요하다: pip install -r panel_inspection/requirements.txt
"""
from __future__ import annotations

import argparse


def train(data_yaml: str, epochs: int = 100, imgsz: int = 640, base_model: str = "yolov8n.pt") -> str:
    from ultralytics import YOLO  # 무거운 의존성이라 실제로 쓸 때만 임포트

    model = YOLO(base_model)
    results = model.train(data=data_yaml, epochs=epochs, imgsz=imgsz)
    return str(results.save_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="판넬 부품탐지 모델 학습")
    parser.add_argument("--data", required=True, help="라벨링 데이터의 data.yaml 경로")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument(
        "--base-model",
        default="yolov8n.pt",
        help="사전학습된 베이스 모델 (기본: nano, 가장 가볍고 빠름)",
    )
    args = parser.parse_args()

    save_dir = train(args.data, epochs=args.epochs, imgsz=args.imgsz, base_model=args.base_model)
    print(f"학습 완료. 결과(가중치 포함) 저장 위치: {save_dir}")


if __name__ == "__main__":
    main()
