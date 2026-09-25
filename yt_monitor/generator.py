"""Ollama로 다음 영상 주제/제목/대본 생성 + outputs/날짜/채널명.md 저장.

흐름
1) prompts/topics.txt     → 주제 후보 N개 + 주제별 제목 후보 + 추천 주제 번호 (JSON)
2) prompts/script_gen.txt → 선택된 주제(기본: 모델이 추천한 주제)의 대본
3) 대본을 TTS용으로 정리 (한 줄 = 한 문장, 마크다운/괄호 지시문/이모지 제거)
4) outputs/YYYY-MM-DD/<채널>.md (+ <채널>_대본.txt) 저장, DB generations 테이블에 이력 기록

프롬프트 파일의 {channel_data} 같은 자리표시자는 아래 이름만 치환된다:
  channel_name, channel_data, num_topics, num_titles, topic, title, reason,
  script_minutes, script_chars

단독 테스트:
    python -m yt_monitor.generator --demo                 # 예시 채널 데이터로 생성 (YouTube API 불필요)
    python -m yt_monitor.generator --channel @내채널       # DB에 수집된 채널 데이터로 생성
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from .ollama_client import OllamaClient
from .paths import resource_dir
from .report import safe_name

log = logging.getLogger(__name__)

CHARS_PER_MINUTE = 330  # 한국어 내레이션 기준 대략적인 분당 글자 수
WORDS_PER_MINUTE = 150  # 영어 내레이션 기준 분당 단어 수
EN_CHARS_PER_MINUTE = 900

TOPICS_PROMPT = "topics.txt"
SCRIPT_PROMPT = "script_gen.txt"


class GenerationError(RuntimeError):
    pass


# ---- 프롬프트 템플릿 --------------------------------------------------------

def localized(name: str, lang: str) -> str:
    """영어 모드면 topics.txt → topics_en.txt (영어판이 있는 템플릿만)."""
    if lang == "en":
        stem, dot, ext = name.rpartition(".")
        en = f"{stem}_en.{ext}"
        if (resource_dir() / "prompts" / en).exists():
            return en
    return name


def load_template(prompts_dir: Path, name: str) -> str:
    """prompts_dir/name 을 읽는다. 없으면 기본 템플릿을 그 자리에 복사해 두고 사용."""
    path = Path(prompts_dir) / name
    if not path.exists():
        default = resource_dir() / "prompts" / name
        if not default.exists():
            raise GenerationError(f"프롬프트 파일이 없습니다: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")
    return path.read_text(encoding="utf-8")


def ensure_prompts(cfg) -> Path:
    """exe 옆(설정 폴더)에 prompts/ 기본 템플릿이 없으면 복사해 둔다 → 사용자가 메모장으로 수정 가능."""
    from .paths import ensure_user_copy

    if cfg.prompts_dir.parent == cfg.base_dir:
        ensure_user_copy(cfg.prompts_dir.name, base=cfg.base_dir)
    return cfg.prompts_dir


def render_template(template: str, values: dict) -> str:
    """{이름} 자리표시자 중 values에 있는 것만 치환 (JSON 예시의 중괄호는 그대로 둔다)."""
    return re.sub(r"\{(\w+)\}",
                  lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0),
                  template)


# ---- 모델 출력 파싱 ---------------------------------------------------------

def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    pairs = [(text.find(o), o, c) for o, c in (("{", "}"), ("[", "]")) if o in text]
    for start, _, close in sorted(pairs):  # 먼저 나오는 괄호 기준 (객체 vs 배열)
        end = text.rfind(close)
        if end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise GenerationError("모델 응답에서 JSON을 찾지 못했습니다:\n" + text[:500])


def _first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k):
            return d[k]
    return default


def parse_topics(text: str, num_topics: int, num_titles: int) -> tuple[list[dict], int]:
    """모델 응답 → ([{"topic","reason","titles"}...], 추천 인덱스(0부터))."""
    data = _extract_json(text)
    items = data if isinstance(data, list) else _first(data, "topics", "주제", "ideas", default=[])
    topics = []
    for it in items:
        if isinstance(it, str):
            it = {"topic": it}
        if not isinstance(it, dict):
            continue
        topic = str(_first(it, "topic", "주제", "title_idea", default="")).strip()
        if not topic:
            continue
        titles = _first(it, "titles", "제목", "title_candidates", "제목 후보", default=[])
        if isinstance(titles, str):
            titles = [titles]
        titles = [str(t).strip() for t in titles if str(t).strip()][:num_titles] or [topic]
        reason = str(_first(it, "reason", "이유", "추천 이유", default="")).strip()
        topics.append({"topic": topic, "reason": reason, "titles": titles})
    if not topics:
        raise GenerationError("모델 응답에 주제 후보가 없습니다:\n" + text[:500])
    topics = topics[:num_topics]

    best = 0
    if isinstance(data, dict):
        try:
            best = int(_first(data, "best", "추천", default=1)) - 1
        except (TypeError, ValueError):
            best = 0
    return topics, best if 0 <= best < len(topics) else 0


_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
_LABEL_RE = re.compile(r"^(내레이션|나레이션|나레이터|내레이터|진행자|화자|narrator|host)\s*[:：]\s*",
                       re.IGNORECASE)
_META_RE = re.compile(r"^(제목|주제|대본|영상 제목|기획 의도|목표 길이|title|topic|script|hook|video title)\s*[:：]",
                      re.IGNORECASE)


# "Sure, here is the voice-over script for …:" 같은 모델의 머리말
_PREAMBLE_RE = re.compile(r"^(sure|okay|ok|certainly|absolutely|of course|here|below|다음은|아래는)\b.*"
                          r"(script|voice-?over|narration|대본|내레이션)", re.IGNORECASE)
_HANGUL_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")


def has_hangul(text: str) -> bool:
    return bool(_HANGUL_RE.search(text or ""))


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).casefold()


def tts_lines(text: str, lang: str | None = None, skip: Iterable[str] = ()) -> list[str]:
    """대본을 TTS에 바로 넣을 수 있게 정리: 한 줄에 한 문장, 읽으면 안 되는 기호 제거.

    lang="en"이면 한글이 섞인 문장을 뺀다 (영어 음성이 한국어를 읽지 않게).
    skip: 모델이 대본 맨 앞에 다시 적은 제목/주제 (첫 내레이션 전에 그대로 나오면 뺀다).
    """
    skip_norm = {_norm(s) for s in skip if s and _norm(s)}
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("```") or re.fullmatch(r"[-=*_~#]{3,}", line):
            continue
        if re.match(r"^#{1,6}\s", line) or _META_RE.match(line.replace("*", "")):
            continue
        plain = line.replace("*", "").strip()
        if _PREAMBLE_RE.match(plain) or (plain.endswith((":", "：")) and len(plain) > 1):
            continue
        if not out and skip_norm and _norm(plain) in skip_norm:
            continue
        line = line.replace("**", "").replace("__", "").replace("`", "")
        line = re.sub(r"^[-*•·>]\s+|^\d+[.)]\s+", "", line)
        line = _LABEL_RE.sub("", line)
        line = re.sub(r"\[[^\]]*\]|\([^)]*\)|（[^）]*）", "", line)   # [효과음] (화면 전환) 같은 지시문
        line = _EMOJI_RE.sub("", line).replace("*", "")
        line = re.sub(r"\s+", " ", line).strip()
        for sentence in re.split(r"(?<=[.!?…])\s+", line):
            sentence = sentence.strip()
            if lang == "en" and has_hangul(sentence):
                continue
            if re.search(r"[0-9A-Za-z가-힣]", sentence):
                out.append(sentence)
    return out


# ---- 결과 구조 --------------------------------------------------------------

@dataclass
class Generation:
    channel_id: str
    channel_title: str
    created_at: datetime
    trigger: str                       # auto | manual | demo
    model: str
    context: str
    topics: list[dict]
    best: int
    selected: int | None = None
    title: str | None = None
    script_lines: list[str] = field(default_factory=list)
    output_path: Path | None = None
    script_path: Path | None = None
    script_prompt: str = SCRIPT_PROMPT        # 트렌드 쇼츠는 shorts_script.txt
    script_minutes: float | None = None       # None이면 설정의 script_minutes
    upload: dict | None = None                # 업로드 정보 (upload_meta.UploadMeta.to_dict())
    upload_hint: str = ""                     # 유행 쇼츠의 카테고리 통계 (업로드 정보 프롬프트용)
    upload_stats: list = field(default_factory=list)

    @property
    def script(self) -> str:
        return "\n".join(self.script_lines)

    @property
    def selected_topic(self) -> dict | None:
        return self.topics[self.selected] if self.selected is not None else None


def render_generation_md(g: Generation, tz: ZoneInfo) -> str:
    when = g.created_at.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
    reason = {"auto": "🚨 성장 둔화 감지 (자동 생성)", "manual": "🖐 수동 생성",
              "demo": "🧪 데모", "trend": "🔥 최근 유행 쇼츠 분석"}.get(g.trigger, g.trigger)
    out = [
        f"# 🎬 {g.channel_title} 다음 영상 기획안",
        "",
        f"- 생성 시각: {when}",
        f"- 생성 사유: {reason}",
        f"- 모델: {g.model} (로컬 Ollama)",
        "",
        "## 주제 후보",
        "",
    ]
    for i, t in enumerate(g.topics):
        marks = []
        if i == g.best:
            marks.append("⭐ 모델 추천")
        if i == g.selected:
            marks.append("📝 대본 작성")
        out.append(f"### {i + 1}. {t['topic']}" + (f"  ({', '.join(marks)})" if marks else ""))
        if t.get("reason"):
            out.append(f"- 추천 이유: {t['reason']}")
        out.append("- 제목 후보:")
        out += [f"  {j}) {title}" for j, title in enumerate(t["titles"], 1)]
        out.append("")
    if g.script_lines:
        sel = g.selected_topic or {}
        out += [
            "## 대본",
            "",
            f"- 주제: {sel.get('topic', '-')}",
            f"- 제목: {g.title or '-'}",
            f"- 분량: {len(g.script_lines)}문장 / {sum(len(s) for s in g.script_lines):,}자",
            "- TTS에 넣을 때는 아래 블록(또는 함께 저장된 _대본.txt)을 그대로 쓰면 됩니다.",
            "",
            "```text",
            g.script,
            "```",
            "",
        ]
    if g.upload:
        from .upload_meta import UploadMeta, render_upload_md

        out += render_upload_md(UploadMeta.from_dict(g.upload))
    out += ["## 참고: 생성에 사용한 채널 데이터", "", "```text", g.context, "```", ""]
    return "\n".join(out)


def save_generation(g: Generation, outputs_dir: Path, tz: ZoneInfo) -> Path:
    """outputs/YYYY-MM-DD/<채널>.md 로 저장 (같은 날 또 만들면 <채널>_HHMM.md)."""
    local = g.created_at.astimezone(tz)
    day_dir = Path(outputs_dir) / local.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    name = safe_name(g.channel_title)
    path = day_dir / f"{name}.md"
    if path.exists():
        path = day_dir / f"{name}_{local:%H%M%S}.md"
    path.write_text(render_generation_md(g, tz), encoding="utf-8")
    g.output_path = path
    if g.script_lines:
        g.script_path = path.with_name(path.stem + "_대본.txt")
        g.script_path.write_text(g.script + "\n", encoding="utf-8")
    return path


# ---- 생성기 -----------------------------------------------------------------

def pick_title(topic: dict, lang: str) -> str:
    """영어 모드에서 7B 모델이 제목을 한국어로 쓰는 경우가 있어, 한글 없는 제목을 먼저 고른다."""
    titles = [t for t in topic.get("titles") or [] if t] or [topic.get("topic", "")]
    if lang == "en":
        for t in [*titles, topic.get("topic", "")]:
            if t and not has_hangul(t):
                return t
    return titles[0]


class ScriptGenerator:
    def __init__(self, client: OllamaClient, prompts_dir: Path, settings: dict, lang: str = "ko"):
        self.client = client
        self.prompts_dir = Path(prompts_dir)
        self.s = settings
        self.lang = lang

    @classmethod
    def from_config(cls, cfg) -> "ScriptGenerator":
        o = cfg.ollama
        client = OllamaClient(o["host"], o["model"], timeout=o["timeout_sec"])
        return cls(client, cfg.prompts_dir, o, lang=cfg.language)

    @property
    def model(self) -> str:
        return self.client.model

    def _options(self) -> dict:
        return {"temperature": self.s.get("temperature", 0.7), "num_ctx": self.s.get("num_ctx", 8192)}

    def generate_topics(self, channel_title: str, context: str, *,
                        template: str = TOPICS_PROMPT,
                        on_token: Callable[[str], None] | None = None,
                        cancel: threading.Event | None = None) -> tuple[list[dict], int]:
        n_topics, n_titles = int(self.s.get("num_topics", 5)), int(self.s.get("num_titles", 3))
        prompt = render_template(load_template(self.prompts_dir, localized(template, self.lang)), {
            "channel_name": channel_title, "channel_data": context,
            "num_topics": n_topics, "num_titles": n_titles,
        })
        text = self.client.chat(prompt, json_mode=True, options=self._options(),
                                on_token=on_token, cancel=cancel)
        return parse_topics(text, n_topics, n_titles)

    def generate_script(self, channel_title: str, context: str, topic: dict, title: str, *,
                        template: str = SCRIPT_PROMPT, minutes: float | None = None,
                        on_token: Callable[[str], None] | None = None,
                        cancel: threading.Event | None = None) -> list[str]:
        minutes = float(minutes if minutes is not None else self.s.get("script_minutes", 3))
        cpm = EN_CHARS_PER_MINUTE if self.lang == "en" else CHARS_PER_MINUTE
        prompt = render_template(load_template(self.prompts_dir, localized(template, self.lang)), {
            "channel_name": channel_title, "channel_data": context,
            "topic": topic["topic"], "title": title, "reason": topic.get("reason", ""),
            "script_minutes": f"{minutes:g}", "script_chars": int(minutes * cpm),
            "script_seconds": int(round(minutes * 60)), "script_words": int(minutes * WORDS_PER_MINUTE),
        })
        if self.lang == "en":
            # 사용자 폴더의 예전 프롬프트 파일에도 적용되게 코드에서 덧붙인다
            prompt += ("\n\nOutput ONLY the spoken lines, all in English. No Korean, no title line, "
                       "and no introduction such as \"Sure, here is the script\". Start with the hook.")
        text = self.client.chat(prompt, options=self._options(), on_token=on_token, cancel=cancel)
        lines = tts_lines(text, self.lang, [title, topic.get("topic", ""), *topic.get("titles", [])])
        if not lines:
            raise GenerationError("모델이 빈 대본을 돌려줬습니다.")
        return lines

    def generate(self, *, channel_id: str, channel_title: str, context: str, now: datetime,
                 trigger: str, selected: int | None = None,
                 topics_prompt: str = TOPICS_PROMPT, script_prompt: str = SCRIPT_PROMPT,
                 script_minutes: float | None = None, upload_hint: str = "", upload_stats: list | None = None,
                 on_status: Callable[[str], None] | None = None,
                 on_token: Callable[[str], None] | None = None,
                 cancel: threading.Event | None = None) -> Generation:
        status = on_status or (lambda msg: log.info(msg))
        status(f"[{channel_title}] 주제 후보 생성 중… ({self.model})")
        topics, best = self.generate_topics(channel_title, context, template=topics_prompt,
                                            on_token=on_token, cancel=cancel)
        g = Generation(channel_id, channel_title, now, trigger, self.model, context, topics, best,
                       script_prompt=script_prompt, script_minutes=script_minutes,
                       upload_hint=upload_hint, upload_stats=list(upload_stats or []))
        idx = best if selected is None or not 0 <= selected < len(topics) else selected
        return self.write_script(g, idx, on_status=status, on_token=on_token, cancel=cancel)

    def write_script(self, g: Generation, index: int, *,
                     on_status: Callable[[str], None] | None = None,
                     on_token: Callable[[str], None] | None = None,
                     cancel: threading.Event | None = None) -> Generation:
        """g.topics[index] 주제로 대본을 (다시) 쓴다. GUI의 '선택한 주제로 대본 생성'에서도 사용."""
        topic = g.topics[index]
        title = pick_title(topic, self.lang)
        if on_status:
            on_status(f"[{g.channel_title}] 대본 작성 중: {topic['topic']}")
        g.script_lines = self.generate_script(g.channel_title, g.context, topic, title,
                                              template=g.script_prompt, minutes=g.script_minutes,
                                              on_token=on_token, cancel=cancel)
        g.selected, g.title = index, title
        g.upload = None
        if self.s.get("upload_meta", True):
            if on_status:
                on_status(f"[{g.channel_title}] 업로드 정보(제목 후보 · 카테고리 · 해시태그) 만드는 중…")
            meta = self.upload_meta(g.script, title, topic, hint=g.upload_hint, stats=g.upload_stats,
                                    cancel=cancel)
            g.upload = meta.to_dict()
            if meta.title and not (self.lang == "en" and has_hangul(meta.title)):
                g.title = meta.title         # 가장 클릭 잘 될 제목을 영상 제목/훅으로
        return g

    def upload_meta(self, script: str, title: str, topic: dict | None = None, *, hint: str = "",
                    stats: list | None = None, cancel: threading.Event | None = None):
        from .upload_meta import generate_upload_meta

        return generate_upload_meta(self.client, self.prompts_dir, lang=self.lang, title=title, script=script,
                                    topic=topic, trend_hint=hint, stats=stats, options=self._options(),
                                    cancel=cancel)


def record_generation(db, g: Generation) -> int:
    gid = db.add_generation(g.channel_id, g.created_at, g.trigger, g.model, g.topics, g.selected,
                            g.title, g.script, str(g.output_path) if g.output_path else None)
    db.commit()
    return gid


def generate_for_channel(cfg, channel_id: str, *, trigger: str = "manual", now: datetime | None = None,
                         generator: ScriptGenerator | None = None, **callbacks) -> Generation:
    """DB에 쌓인 데이터로 채널을 분석하고 주제/대본을 생성해 저장까지 한다 (YouTube API 호출 없음)."""
    from .analyzer import analyze_channel
    from .db import Database, utcnow
    from .report import channel_context

    now = now or utcnow()
    tz = ZoneInfo(cfg.schedule["timezone"])
    generator = generator or ScriptGenerator.from_config(cfg)
    ch = next((c for c in cfg.channels if c.id == channel_id), None)
    settings = ch.analysis if ch else cfg.analysis
    with Database(cfg.db_path) as db:
        a = analyze_channel(db, channel_id, settings, now, tz,
                            max_videos=cfg.youtube["max_videos_per_channel"])
        if not a.videos:
            raise GenerationError("이 채널은 아직 수집된 영상이 없습니다. 먼저 '지금 체크하기'를 실행하세요.")
        g = generator.generate(channel_id=channel_id, channel_title=a.channel_title,
                               context=channel_context(a, tz), now=now, trigger=trigger, **callbacks)
        save_generation(g, cfg.outputs_dir, tz)
        record_generation(db, g)
    return g


DEMO_CONTEXT = """채널: 데모 요리 채널
상태: 성장 둔화 감지
구독자: 12,300명
최근 5개 영상 평균 조회수 증가율: -42.0% (이전 10개 대비)

[최근 영상 성과 (최신순 5개)]
- 냉장고 정리 브이로그 (일평균 조회수 120)
- 주방 도구 언박싱 (일평균 조회수 95)
- 요리 Q&A 라이브 다시보기 (일평균 조회수 80)

[상위 성과 영상 Top 3]
1. 자취생 5천원 일주일 식단 / 노하우/스타일 / 8:12 / 일평균 조회수 1,450
2. 편의점 재료로 만드는 파스타 3종 / 노하우/스타일 / 0:58 / 일평균 조회수 1,210
3. 전자레인지 계란찜 실패 없는 법 / 노하우/스타일 / 0:45 / 일평균 조회수 980

[상위 영상 패턴]
- 반복 키워드: 자취생, 편의점, 5천원
- 형식: 숏폼 비율 66.7%, 평균 길이 3:18
- 게시 타이밍: 요일 토(2), 일(1) / 시간 19시(2)"""


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from .config import load_config
    from .db import utcnow
    from .paths import default_config_path

    parser = argparse.ArgumentParser(description="주제/대본 생성 단독 테스트")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--demo", action="store_true", help="예시 채널 데이터로 생성")
    src.add_argument("--channel", help="DB에 수집된 채널의 ID(UC...) 또는 @핸들")
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--quiet", action="store_true", help="생성 중 글자 스트리밍 출력 안 함")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    stream = None if args.quiet else (lambda t: (sys.stdout.write(t), sys.stdout.flush()))
    status = lambda msg: print(f"\n▶ {msg}")  # noqa: E731

    if args.demo:
        from .config import DEFAULTS, read_raw
        from .paths import resource_dir

        cfg_path = Path(args.config)
        raw = read_raw(cfg_path) if cfg_path.exists() else DEFAULTS
        o = raw["ollama"]
        gen = ScriptGenerator(OllamaClient(o["host"], o["model"], timeout=o["timeout_sec"]),
                              resource_dir() / "prompts", o)
        gen.client.ensure_ready()
        g = gen.generate(channel_id="demo", channel_title="데모 요리 채널", context=DEMO_CONTEXT,
                         now=utcnow(), trigger="demo", on_status=status, on_token=stream)
        out_dir = cfg_path.parent / raw["storage"]["outputs_dir"]
        path = save_generation(g, out_dir, ZoneInfo(raw["schedule"]["timezone"]))
    else:
        cfg = load_config(args.config)
        from .db import Database

        with Database(cfg.db_path) as db:
            row = (db.find_channel_by_handle(args.channel) if args.channel.startswith("@")
                   else db.get_channel(args.channel))
        if row is None:
            print("DB에 없는 채널입니다. 먼저 수집(--check-now)을 실행하세요.")
            return 1
        generator = ScriptGenerator.from_config(cfg)
        generator.client.ensure_ready()
        g = generate_for_channel(cfg, row["channel_id"], trigger="manual", generator=generator,
                                 on_status=status, on_token=stream)
        path = g.output_path
    print(f"\n\n✅ 저장: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
