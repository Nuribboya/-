"""config.yaml + .env 로딩.

비밀값(API 키, 봇 토큰, 챗 ID)은 config.yaml에 직접 쓰지 않고
"어떤 환경변수에서 읽을지"만 적는다. 실제 값은 .env(또는 OS 환경변수)에 둔다.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_ANALYSIS = {
    # views_per_day : 누적 조회수 / 게시 후 경과일 (수집 첫날부터 바로 사용 가능)
    # views_at_age  : 게시 후 age_days일 시점의 조회수 (시계열이 쌓인 뒤 더 공정한 비교)
    "metric": "views_per_day",
    "age_days": 7,
    "min_age_days": 1.0,          # 이보다 어린 영상은 값이 불안정하므로 비교에서 제외
    "recent_n": 5,                # 최근 N개 영상
    "baseline_m": 10,             # 비교 대상: 그 이전 M개 영상
    "min_baseline": 3,            # 이전 영상이 이보다 적으면 판단 보류
    "aggregate": "mean",          # mean | median
    "drop_threshold_pct": 30.0,   # 최근 N개 평균이 이전 M개 대비 X% 이상 하락 → 둔화
    "weekly_drop_threshold_pct": None,  # 채널 주간 조회수 증가량 하락률 기준 (None이면 비활성)
    "top_k": 3,
    "alert_cooldown_hours": 24,
    "shorts_max_seconds": 180,
}

DEFAULTS = {
    "youtube": {"api_key_env": "YOUTUBE_API_KEY", "max_videos_per_channel": 30},
    "channels": [],
    "analysis": DEFAULT_ANALYSIS,
    "telegram": {
        "enabled": True,
        "bot_token_env": "TELEGRAM_BOT_TOKEN",
        "chat_id_env": "TELEGRAM_CHAT_ID",
        "send_report_file": True,
        "notify_on_every_check": False,
    },
    "storage": {"db_path": "data/yt_monitor.db", "reports_dir": "reports"},
    "schedule": {"cron": "0 */6 * * *", "timezone": "Asia/Seoul", "run_on_start": True},
}


class ConfigError(ValueError):
    pass


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class ChannelConfig:
    id: str | None
    handle: str | None
    name: str | None
    analysis: dict

    @property
    def label(self) -> str:
        return self.name or self.handle or self.id or "?"


@dataclass
class Config:
    raw: dict
    base_dir: Path
    channels: list[ChannelConfig] = field(default_factory=list)

    @property
    def youtube(self) -> dict:
        return self.raw["youtube"]

    @property
    def telegram(self) -> dict:
        return self.raw["telegram"]

    @property
    def schedule(self) -> dict:
        return self.raw["schedule"]

    @property
    def analysis(self) -> dict:
        return self.raw["analysis"]

    def path(self, key: str) -> Path:
        p = Path(self.raw["storage"][key])
        return p if p.is_absolute() else self.base_dir / p

    @property
    def db_path(self) -> Path:
        return self.path("db_path")

    @property
    def reports_dir(self) -> Path:
        return self.path("reports_dir")

    def secret(self, env_name_key: str, section: str, required: bool = True) -> str | None:
        env_name = self.raw[section][env_name_key]
        value = os.environ.get(env_name, "").strip()
        if not value and required:
            raise ConfigError(
                f"환경변수 {env_name} 가 비어 있습니다. {self.base_dir / '.env'} 에 값을 넣어주세요."
            )
        return value or None

    @property
    def youtube_api_key(self) -> str:
        return self.secret("api_key_env", "youtube")  # type: ignore[return-value]


def _validate_analysis(a: dict, where: str) -> None:
    if a["metric"] not in ("views_per_day", "views_at_age"):
        raise ConfigError(f"{where}.metric 은 views_per_day 또는 views_at_age 여야 합니다.")
    if a["aggregate"] not in ("mean", "median"):
        raise ConfigError(f"{where}.aggregate 는 mean 또는 median 이어야 합니다.")
    for key in ("recent_n", "baseline_m", "min_baseline", "top_k"):
        if int(a[key]) < 1:
            raise ConfigError(f"{where}.{key} 는 1 이상이어야 합니다.")


def load_config(path: str | Path, load_env: bool = True) -> Config:
    path = Path(path).resolve()
    if not path.exists():
        raise ConfigError(f"설정 파일이 없습니다: {path}")
    base_dir = path.parent

    if load_env:
        from dotenv import load_dotenv

        # OS에 이미 설정된 환경변수가 우선한다.
        load_dotenv(base_dir / ".env", override=False)

    with open(path, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    raw = _merge(DEFAULTS, user)
    _validate_analysis(raw["analysis"], "analysis")

    channels = []
    for i, item in enumerate(raw.get("channels") or []):
        if isinstance(item, str):
            item = {"handle": item} if item.startswith("@") else {"id": item}
        if not item.get("id") and not item.get("handle"):
            raise ConfigError(f"channels[{i}] 에 id 또는 handle 이 필요합니다.")
        handle = item.get("handle")
        if handle and not handle.startswith("@"):
            handle = "@" + handle
        analysis = _merge(raw["analysis"], item.get("analysis") or {})
        _validate_analysis(analysis, f"channels[{i}].analysis")
        channels.append(ChannelConfig(item.get("id"), handle, item.get("name"), analysis))
    if not channels:
        raise ConfigError("config.yaml 의 channels 에 최소 1개 채널을 등록해주세요.")

    return Config(raw=raw, base_dir=base_dir, channels=channels)
