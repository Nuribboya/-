"""config.yaml (+ 선택적으로 .env) 로딩/저장.

비밀값(YouTube API 키, 텔레그램 봇 토큰/챗 ID) 읽는 순서
1) 환경변수 (config의 *_env 에 적힌 이름, 예: YOUTUBE_API_KEY) — .env 파일도 자동으로 읽음
2) config.yaml 에 직접 적힌 값 (youtube.api_key, telegram.bot_token, telegram.chat_id)
   → exe의 첫 실행 설정 창이 여기에 저장한다. config.yaml을 남에게 공유하지 마세요.

단독 확인: python -m yt_monitor.config [config.yaml 경로]
"""

from __future__ import annotations

import copy
import os
import sys
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

DEFAULT_TRENDS = {  # 최근 유행 쇼츠 분석 (trends.py)
    "region": "KR",
    "language": "ko",
    "lookback_days": 3,            # 최근 며칠 안에 올라온 영상만
    "shorts_max_seconds": 180,     # 쇼츠 최대 길이 (유튜브 쇼츠는 최대 3분)
    "search_queries": ["", "#shorts"],   # 빈 문자열 = 검색어 없이 전체 조회수 상위
    "popular_pages": 4,            # 인기 급상승 목록 페이지 수 (50개씩)
    "korean_only": True,           # 제목에 한글이 있는 영상만
    "top_n": 30,                   # Ollama에 넘길 상위 영상 수
    "breakout_ratio": 3.0,         # 조회수 ≥ 구독자 × 이 값이면 '떡상'
    "min_views": 10000,
    "script_seconds": 50,          # 쇼츠 대본 목표 길이(초)
    "auto_video": False,           # GUI: 대본 생성 후 영상까지 자동으로 만들기
}

DEFAULTS = {
    "youtube": {"api_key": "", "api_key_env": "YOUTUBE_API_KEY", "max_videos_per_channel": 30},
    "channels": [],
    "analysis": DEFAULT_ANALYSIS,
    "telegram": {
        "enabled": True,
        "bot_token": "",
        "chat_id": "",
        "bot_token_env": "TELEGRAM_BOT_TOKEN",
        "chat_id_env": "TELEGRAM_CHAT_ID",
        "send_report_file": True,
        "notify_on_every_check": False,
    },
    "ollama": {
        "enabled": True,
        "host": "http://localhost:11434",
        "model": "qwen2.5:7b",
        "auto_generate_on_slowdown": True,  # 둔화 감지 시 주제/대본 자동 생성
        "num_topics": 5,                    # 주제 후보 수 (3~5 권장)
        "num_titles": 3,                    # 주제별 제목 후보 수 (2~3 권장)
        "script_minutes": 3,                # 대본 목표 길이(분)
        "temperature": 0.7,
        "num_ctx": 8192,
        "timeout_sec": 900,                 # CPU만 있는 PC는 7B 대본 생성에 수 분 걸릴 수 있음
    },
    "trends": DEFAULT_TRENDS,
    "pexels": {                             # 무료 스톡 영상 (https://www.pexels.com/api/)
        "api_key": "",
        "api_key_env": "PEXELS_API_KEY",
        "orientation": "portrait",          # 쇼츠용 세로 영상 우선 검색
        "per_page": 15,
        "timeout_sec": 60,
    },
    "video": {
        "ffmpeg_path": "",                  # 비우면 exe 옆 ffmpeg.exe → PATH 순서로 찾음
        "width": 1080,
        "height": 1920,                     # 9:16 세로
        "fps": 30,
        "clip_max_seconds": 4.0,            # 씬당 클립 길이: 씬이 이보다 길면 클립 여러 개로 나눔
        "min_scene_seconds": 2.5,           # 이보다 짧은 문장은 다음 문장과 합쳐 한 씬으로
        "max_scene_chars": 90,              # 씬 하나에 넣을 최대 글자 수
        "keywords_per_scene": 3,
        "tts_voice": "ko-KR-SunHiNeural",   # edge-tts 음성 (ko-KR-InJoonNeural = 남성)
        "tts_rate": "+10%",                 # 말하기 속도 (쇼츠는 약간 빠르게)
        "tts_volume": "+0%",
        "tts_pitch": "+0Hz",
        "tts_proxy": "",                    # 회사망 등에서 필요할 때만 (예: http://proxy:8080)
        "subtitle_font": "Malgun Gothic",   # 맑은 고딕 (Windows 기본 한글 폰트)
        "subtitle_font_size": 72,           # 1080x1920 기준 픽셀
        "subtitle_max_chars": 14,           # 자막 한 덩어리 최대 글자 수
        "subtitle_margin_bottom": 320,      # 화면 아래에서 자막까지 거리(px). 쇼츠 UI에 안 가리게
        "subtitle_box_opacity": 0.6,        # 자막 뒤 검은 박스 불투명도 (0~1)
        "crf": 21,                          # 화질 (낮을수록 고화질·큰 파일, 18~28)
        "preset": "veryfast",
        "keep_work_files": True,            # 중간 파일(클립/음성/ffmpeg 로그) 보관 → 디버깅용
    },
    "storage": {
        "video_cache_dir": "data/video_cache",   # 다운로드한 스톡 영상 캐시
        "db_path": "data/yt_monitor.db",
        "reports_dir": "reports",
        "outputs_dir": "outputs",
        "prompts_dir": "prompts",
    },
    "schedule": {
        "cron": "0 */6 * * *",
        "interval_hours": None,             # 값이 있으면 cron 대신 N시간 간격
        "timezone": "Asia/Seoul",
        "run_on_start": True,
    },
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
    path: Path | None = None

    @property
    def youtube(self) -> dict:
        return self.raw["youtube"]

    @property
    def telegram(self) -> dict:
        return self.raw["telegram"]

    @property
    def ollama(self) -> dict:
        return self.raw["ollama"]

    @property
    def pexels(self) -> dict:
        return self.raw["pexels"]

    @property
    def video(self) -> dict:
        return self.raw["video"]

    @property
    def schedule(self) -> dict:
        return self.raw["schedule"]

    @property
    def analysis(self) -> dict:
        return self.raw["analysis"]

    def storage_path(self, key: str) -> Path:
        p = Path(self.raw["storage"][key])
        return p if p.is_absolute() else self.base_dir / p

    @property
    def db_path(self) -> Path:
        return self.storage_path("db_path")

    @property
    def reports_dir(self) -> Path:
        return self.storage_path("reports_dir")

    @property
    def outputs_dir(self) -> Path:
        return self.storage_path("outputs_dir")

    @property
    def prompts_dir(self) -> Path:
        return self.storage_path("prompts_dir")

    @property
    def video_cache_dir(self) -> Path:
        return self.storage_path("video_cache_dir")

    def secret(self, section: str, key: str, required: bool = True) -> str | None:
        """환경변수(<key>_env에 적힌 이름) → config.yaml의 <key> 순으로 찾는다."""
        sec = self.raw[section]
        env_name = sec.get(f"{key}_env")
        value = (os.environ.get(env_name, "") if env_name else "").strip()
        if not value:
            value = str(sec.get(key) or "").strip()
        if not value and required:
            raise ConfigError(
                f"{section}.{key} 값이 없습니다. 설정 창(또는 config.yaml)에 입력하거나 "
                f"환경변수 {env_name} 를 지정해주세요."
            )
        return value or None

    @property
    def youtube_api_key(self) -> str:
        return self.secret("youtube", "api_key")  # type: ignore[return-value]


def _validate_analysis(a: dict, where: str) -> None:
    if a["metric"] not in ("views_per_day", "views_at_age"):
        raise ConfigError(f"{where}.metric 은 views_per_day 또는 views_at_age 여야 합니다.")
    if a["aggregate"] not in ("mean", "median"):
        raise ConfigError(f"{where}.aggregate 는 mean 또는 median 이어야 합니다.")
    for key in ("recent_n", "baseline_m", "min_baseline", "top_k"):
        if int(a[key]) < 1:
            raise ConfigError(f"{where}.{key} 는 1 이상이어야 합니다.")


def parse_channels(raw: dict) -> list[ChannelConfig]:
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
    return channels


def read_raw(path: str | Path) -> dict:
    """기본값과 병합된 설정 dict (검증 없음). 파일이 없으면 기본값."""
    path = Path(path)
    user = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
    return _merge(DEFAULTS, user)


def load_config(path: str | Path, load_env: bool = True) -> Config:
    path = Path(path).resolve()
    if not path.exists():
        raise ConfigError(f"설정 파일이 없습니다: {path}")
    base_dir = path.parent

    if load_env:
        from dotenv import load_dotenv

        # OS에 이미 설정된 환경변수가 우선한다.
        load_dotenv(base_dir / ".env", override=False)

    raw = read_raw(path)
    _validate_analysis(raw["analysis"], "analysis")
    channels = parse_channels(raw)
    if not channels:
        raise ConfigError("config.yaml 의 channels 에 최소 1개 채널을 등록해주세요.")
    return Config(raw=raw, base_dir=base_dir, channels=channels, path=path)


def save_config(raw: dict, path: str | Path) -> Path:
    """설정 dict를 config.yaml로 저장 (GUI 설정 창에서 사용)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ("# YouTube 채널 성과 모니터 설정 (프로그램의 [설정] 창에서도 수정할 수 있습니다)\n"
              "# ⚠️ API 키/봇 토큰이 들어 있으니 다른 사람에게 공유하지 마세요.\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return path


def _mask(value: str | None) -> str:
    if not value:
        return "(없음)"
    return value[:4] + "…" + value[-2:] if len(value) > 8 else "****"


if __name__ == "__main__":  # 단독 확인: 설정을 읽어서 요약 출력
    from .paths import default_config_path

    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else default_config_path())
    print(f"설정 파일: {cfg.path}")
    print(f"채널 {len(cfg.channels)}개: " + ", ".join(c.label for c in cfg.channels))
    print(f"YouTube API 키: {_mask(cfg.secret('youtube', 'api_key', required=False))}")
    print(f"텔레그램 토큰: {_mask(cfg.secret('telegram', 'bot_token', required=False))}, "
          f"chat_id: {cfg.secret('telegram', 'chat_id', required=False) or '(없음)'}")
    print(f"Ollama: {cfg.ollama['host']} / 모델 {cfg.ollama['model']}")
    print(f"Pexels API 키: {_mask(cfg.secret('pexels', 'api_key', required=False))}")
    print(f"하락 임계값: {cfg.analysis['drop_threshold_pct']}% / 주기: "
          + (f"{cfg.schedule['interval_hours']}시간" if cfg.schedule.get("interval_hours")
             else f"cron '{cfg.schedule['cron']}'"))
    print(f"DB: {cfg.db_path}")
