"""탐지된 부품 리스트와 BOM(필요 부품 목록)을 비교해서 빠지거나 초과된 부품을 찾는다.

여기는 학습이 필요 없는 순수 로직이다 - 딥러닝이 필요한 부분은 사진에서 부품을
찾아내는 것(detect.py)뿐이고, 그 결과와 도면 BOM을 대조하는 건 그냥 개수 비교다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class BomDiff:
    missing: dict[str, int]  # 부족한 부품: {부품명: 부족한 개수}
    extra: dict[str, int]  # 남는(또는 BOM에 없는) 부품: {부품명: 초과 개수}

    @property
    def is_complete(self) -> bool:
        return not self.missing


def load_bom(path: str | Path) -> dict[str, int]:
    """BOM YAML(부품명: 필요수량, panel_inspection/bom_example.yaml 참고)을 읽는다."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def counts_from_detections(detections: list[dict]) -> dict[str, int]:
    """detect.py가 뱉는 [{'class': '차단기', 'confidence': 0.9, ...}, ...] 형태에서 부품별 개수를 센다."""
    counter: Counter[str] = Counter(d["class"] for d in detections)
    return dict(counter)


def compare_to_bom(detected_counts: dict[str, int], bom: dict[str, int]) -> BomDiff:
    missing: dict[str, int] = {}
    extra: dict[str, int] = {}

    for part, required in bom.items():
        found = detected_counts.get(part, 0)
        if found < required:
            missing[part] = required - found
        elif found > required:
            extra[part] = found - required

    for part, found in detected_counts.items():
        if part not in bom and found > 0:
            extra[part] = extra.get(part, 0) + found

    return BomDiff(missing=missing, extra=extra)
