"""데스크톱 앱의 설정 저장과 화면 입력 → 탐색 설정 변환.

화면(tkinter) 코드와 분리해 둔다. GUI 없이도 테스트할 수 있어야 하고,
'입력을 어떻게 해석하는가'는 화면보다 오래 사는 규칙이기 때문이다.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from prime_contractor.config import ScreenConfig, load_config

APP_NAME = "PrimeFinder"

#: 화면에서 고를 수 있는 탐색 방식
MODE_PUBLIC = "공공 낙찰 (나라장터)"
MODE_INDUSTRY = "업종 훑기 (DART 상장사)"
MODE_SAMPLE = "샘플 데이터 (키 없이 시험)"
MODES = (MODE_PUBLIC, MODE_INDUSTRY, MODE_SAMPLE)

#: 거리 선택지 (표시 문구 → km, None 이면 제한 없음)
DISTANCE_CHOICES: dict[str, float | None] = {
    "50km 이내": 50.0,
    "70km 이내": 70.0,
    "100km 이내": 100.0,
    "150km 이내": 150.0,
    "전국 (제한 없음)": None,
}

#: 겹침 허용 범위 (표시 문구 → max_overlap_rank)
OVERLAP_CHOICES: dict[str, int] = {
    "KC 계열사만 제외 (반도체 포함)": 2,
    "반도체 등 같은 업종까지 제외": 1,
    "인접 업종까지 모두 제외": 0,
}

SECTOR_ALL = "전체 업종"


def settings_path() -> Path:
    """설정 파일 위치. 윈도우는 %APPDATA%, 그 외는 홈 아래."""
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME / "settings.json"


def load_settings() -> dict:
    path = settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def save_settings(values: dict) -> Path:
    """입력값을 저장한다. 인증키가 평문으로 들어가니 공용 PC 에서는 주의."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_config(options: dict, base: ScreenConfig | None = None) -> ScreenConfig:
    """화면 입력값을 ScreenConfig 로 바꾼다.

    빈 칸이나 알 수 없는 값은 기본값으로 둔다. 화면에서 잘못 고른 것 때문에
    탐색이 멈추는 것보다, 기본값으로라도 돌아가는 편이 낫다.
    """
    cfg = base or load_config(options.get("config_path") or None)

    patch: dict = {}
    days = _as_int(options.get("days"))
    if days:
        patch["lookback_days"] = days

    label = options.get("distance")
    if label in DISTANCE_CHOICES:
        patch["within_km"] = DISTANCE_CHOICES[label]

    overlap = options.get("overlap")
    if overlap in OVERLAP_CHOICES:
        patch["max_overlap_rank"] = OVERLAP_CHOICES[overlap]

    min_awards = _as_int(options.get("min_awards"))
    if min_awards is not None:
        patch["min_awards"] = min_awards

    if "include_demand_orgs" in options:
        patch["include_demand_orgs"] = bool(options["include_demand_orgs"])

    for key, field in (("g2b_key", "g2b_service_key"), ("dart_key", "dart_api_key")):
        value = (options.get(key) or "").strip()
        if value:
            patch[field] = value

    return replace(cfg, **patch) if patch else cfg


def sector_names(cfg: ScreenConfig) -> list[str]:
    """업종 드롭다운 항목."""
    return [SECTOR_ALL] + [s.name for s in cfg.sectors]


def _as_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
