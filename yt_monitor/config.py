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
    "region": "US",
    "language": "en",
    "lookback_days": 3,            # 최근 며칠 안에 올라온 영상만
    "shorts_max_seconds": 180,     # 쇼츠 최대 길이 (유튜브 쇼츠는 최대 3분)
    "search_queries": ["", "#shorts"],   # 빈 문자열 = 검색어 없이 전체 조회수 상위
    "popular_pages": 4,            # 인기 급상승 목록 페이지 수 (50개씩)
    "title_language": "en",        # en = 영어 제목만, ko = 한글 제목만, any = 전부
    "top_n": 30,                   # Ollama에 넘길 상위 영상 수
    "breakout_ratio": 3.0,         # 조회수 ≥ 구독자 × 이 값이면 '떡상'
    "min_views": 50000,
    "script_seconds": 50,          # 쇼츠 대본 목표 길이(초)
    "auto_video": False,           # GUI: 대본 생성 후 영상까지 자동으로 만들기
}

# 콘텐츠 언어/시장별 기본값. 설정 창에서 언어를 바꾸면 이 값들이 한꺼번에 들어간다.
LANGUAGE_PRESETS = {
    "en": {  # 영어권 (미국 중심) — 광고 단가가 높고 시청자가 많다
        "trends": {"region": "US", "language": "en", "title_language": "en", "min_views": 50000},
        "video": {"tts_voice": "en-US-GuyNeural", "tts_rate": "+5%", "subtitle_font": "Arial Black",
                  "subtitle_font_size": 76, "subtitle_max_chars": 18, "max_scene_chars": 160},
    },
    "ko": {
        "trends": {"region": "KR", "language": "ko", "title_language": "ko", "min_views": 10000},
        "video": {"tts_voice": "ko-KR-SunHiNeural", "tts_rate": "+10%", "subtitle_font": "Malgun Gothic",
                  "subtitle_font_size": 72, "subtitle_max_chars": 14, "max_scene_chars": 90},
    },
}
LANGUAGE_LABELS = {"en": "영어권 (미국)", "ko": "한국"}


def apply_language(raw: dict, lang: str) -> dict:
    """raw 설정에 언어 프리셋을 덮어쓴다 (유행 분석 지역, 음성, 자막 폰트 등)."""
    if lang not in LANGUAGE_PRESETS:
        raise ConfigError(f"language 는 {', '.join(LANGUAGE_PRESETS)} 중 하나여야 합니다.")
    raw["language"] = lang
    for section, values in LANGUAGE_PRESETS[lang].items():
        raw.setdefault(section, {}).update(values)
    return raw


CONFIG_VERSION = 3
OLD_AI_STYLE = "cinematic, dramatic lighting, high contrast, vivid colors, ultra detailed, 8k photo"
OLD_AI_NEGATIVE = "text, watermark, logo, blurry, low quality, deformed, ugly, nsfw"
REALISTIC_STYLE = "RAW photo, candid photograph, shot on 35mm, natural lighting, realistic skin texture, subtle film grain, sharp focus"
REALISTIC_NEGATIVE = ("cgi, 3d render, illustration, painting, drawing, cartoon, anime, plastic skin, airbrushed, overly sm"
                      "ooth skin, oversaturated, text, watermark, logo, blurry, low quality, deformed hands, extra fingers, ugly, nsfw")


def upgrade_config(raw: dict, user: dict) -> dict:
    """예전 버전 config.yaml 을 새 기본값에 맞춘다. 사용자가 바꾼 값은 건드리지 않는다."""
    version = int(user.get("config_version") or 1)
    if "language" not in user:
        # 언어 설정이 생기기 전의 config.yaml (한국어 값이 그대로 저장돼 있음) → 기본 언어 프리셋 적용
        apply_language(raw, DEFAULTS["language"])
    if version < 2:
        # v2: 쇼츠는 컷이 빨라야 한다 → 예전 기본값(4초)이면 2.5초로
        if float(raw["video"].get("clip_max_seconds") or 0) == 4.0:
            raw["video"]["clip_max_seconds"] = 2.5
    if version < 3:
        # v3: AI 이미지를 실제 사진처럼 → 예전 기본 스타일(영화 같은 · 과한 색감)을 그대로 쓰고 있으면 교체
        ai = raw.get("ai_images") or {}
        if ai.get("style") == OLD_AI_STYLE:
            ai["style"] = REALISTIC_STYLE
        if ai.get("negative") == OLD_AI_NEGATIVE:
            ai["negative"] = REALISTIC_NEGATIVE
    raw["config_version"] = CONFIG_VERSION
    return raw


DEFAULTS = {
    "config_version": CONFIG_VERSION,
    "language": "en",                       # 콘텐츠 언어/시장: en(미국 중심 영어) | ko(한국)
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
        "upload_meta": True,                # 업로드 정보(제목 후보 · 카테고리 · 설명 · 해시태그) 생성
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
        "clip_max_seconds": 2.5,            # 씬당 클립 길이: 씬이 이보다 길면 클립 여러 개로 나눔 (쇼츠는 빠른 컷)
        "zoom": 0.08,                       # 컷마다 천천히 8% 줌인/줌아웃 (0이면 끔)
        "contrast": 1.08,                   # 대비 (1 = 원본)
        "saturation": 1.2,                  # 채도 (1 = 원본). 스톡 영상 특유의 밋밋함을 줄임
        "hook_seconds": 2.5,                # 첫 화면 큰 훅 문구를 보여줄 시간 (0이면 끔)
        "hook_font_size": 96,
        "min_scene_seconds": 2.5,           # 이보다 짧은 문장은 다음 문장과 합쳐 한 씬으로
        "max_scene_chars": 160,             # 씬 하나에 넣을 최대 글자 수
        "keywords_per_scene": 3,
        "voice_mode": "auto",               # auto = 대본 분위기에 맞춰 음성·속도·톤 자동 선택 / fixed = tts_voice 고정
        "mood_voices": {},                  # 분위기별 음성 바꾸기 (선택), 예: {en: {calm: {voice: en-US-JennyNeural}}}
        "tts_voice": "en-US-GuyNeural",     # edge-tts 음성 (en-US-JennyNeural = 여성, ko-KR-SunHiNeural = 한국어)
        "tts_rate": "+5%",                  # 말하기 속도 (쇼츠는 약간 빠르게)
        "tts_volume": "+0%",
        "tts_pitch": "+0Hz",
        "tts_proxy": "",                    # 회사망 등에서 필요할 때만 (예: http://proxy:8080)
        "subtitle_font": "Arial Black",     # 영어 쇼츠용 굵은 폰트 (한국어는 Malgun Gothic)
        "subtitle_font_size": 76,           # 1080x1920 기준 픽셀
        "subtitle_max_chars": 18,           # 자막 한 덩어리 최대 글자 수
        "subtitle_margin_bottom": 320,      # 화면 아래에서 자막까지 거리(px). 쇼츠 UI에 안 가리게
        "subtitle_box_opacity": 0.6,        # 자막 뒤 검은 박스 불투명도 (0~1)
        "crf": 21,                          # 화질 (낮을수록 고화질·큰 파일, 18~28)
        "preset": "veryfast",
        "keep_work_files": True,            # 중간 파일(클립/음성/ffmpeg 로그) 보관 → 디버깅용
        "upload_meta": True,                # 영상 옆에 <이름>_업로드정보.txt
        "bgm_enabled": True,                # bgm/<분위기>/ 폴더의 곡을 배경음악으로 (없으면 목소리만)
        "bgm_volume": 0.15,                 # 배경음악 크기 (0.1 = 작게 ~ 0.3 = 크게)
        "bgm_duck": True,                   # 목소리가 나올 때 음악을 자동으로 줄이기
    },
    "pixabay": {                            # 무료 스톡 영상 추가 소스 (https://pixabay.com/api/docs/)
        "api_key": "",
        "api_key_env": "PIXABAY_API_KEY",
        "per_page": 15,
    },
    "ai_images": {                          # 로컬 AI 이미지 생성 (ComfyUI, 그래픽카드 필요)
        "enabled": False,
        "host": "http://127.0.0.1:8188",
        "comfy_dir": "",                    # ComfyUI portable 폴더. 비우면 다운로드/C:\ 등에서 자동으로 찾음
        "auto_start": True,                 # 영상 만들 때 꺼져 있으면 창 없이 자동 실행
        "auto_stop": True,                  # 프로그램이 켠 ComfyUI는 이미지를 다 만들면 자동 종료
        "start_timeout_sec": 240,
        "auto_tune": True,                  # sdxl_lightning 모델이면 steps·cfg·sampler 자동 설정
        "mode": "mix",                      # mix = 첫 씬(훅) + 스톡 영상을 못 찾은 씬 / all = 모든 씬
        "checkpoint": "",                   # 비우면 ComfyUI에 있는 첫 번째 모델
        "width": 768,
        "height": 1344,                     # 9:16에 가까운 SDXL 해상도
        "steps": 6,                         # Lightning/Turbo 모델 기준. 일반 SDXL은 25~30
        "cfg": 2.0,                         # Lightning 기준. 일반 SDXL은 5~7
        "sampler": "dpmpp_sde",
        "scheduler": "karras",
        # 실제 사진처럼 (AI 티 나는 매끈한 얼굴 · 과한 색감을 피한다)
        "style": REALISTIC_STYLE,
        "negative": REALISTIC_NEGATIVE,
        "timeout_sec": 300,
    },
    "storage": {
        "video_cache_dir": "data/video_cache",   # 다운로드한 스톡 영상 캐시
        "bgm_dir": "bgm",                        # 배경음악 폴더 (분위기별 하위 폴더)
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
    def language(self) -> str:
        return self.raw.get("language") or "en"

    @property
    def pixabay(self) -> dict:
        return self.raw["pixabay"]

    @property
    def ai_images(self) -> dict:
        return self.raw["ai_images"]

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

    @property
    def bgm_dir(self) -> Path:
        return self.storage_path("bgm_dir")

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
    raw = _merge(DEFAULTS, user)
    if user:
        upgrade_config(raw, user)
    return raw


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
