"""대본 → 씬 분리 → 씬별 스톡 영상 검색 키워드 (로컬 Ollama).

- 대본은 generator.tts_lines()로 정리한다 (마크다운, [효과음], 이모지 제거 · 한 줄에 한 문장).
- 문장을 순서대로 모아, 예상 길이가 min_scene_seconds 이상이 될 때까지(단 max_scene_chars 이하) 한 씬으로 묶는다.
- 키워드는 Ollama에 씬 전체를 한 번에 보내 영어 검색어 JSON으로 받는다 (prompts/video_keywords.txt).
  Ollama가 꺼져 있거나 응답이 이상하면 대본에서 뽑은 한국어 단어로 대신 검색한다 (결과는 적을 수 있음).

단독 테스트:
    python -m yt_monitor.video.scenes 대본.txt              # 씬 분리 + Ollama 키워드
    python -m yt_monitor.video.scenes 대본.txt --no-ollama  # 씬 분리 + 간이 키워드
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..generator import GenerationError, _extract_json, load_template, render_template, tts_lines

log = logging.getLogger(__name__)

KEYWORDS_PROMPT = "video_keywords.txt"
CHARS_PER_SECOND = 5.5 * 1.1   # 한국어 내레이션 ≈ 분당 330자, 쇼츠는 약간 빠르게

_COPULA = re.compile(r"(입니다|이에요|예요|이죠|이다|이야|이고|이라)$")
_JOSA = re.compile(r"(으로|에서|에게|까지|부터|처럼|보다|이랑|하고|이나|은|는|이|가|을|를|의|에|도|만|와|과|로)$")
# 동사/형용사로 보이는 끝 (먹으면, 든든한, 맛있, 모았습니다 …) → 검색어에서 뺀다
_VERBISH = re.compile(r"(다|요|까|네|면|고|서|게|지|한|된|운|던|나)$")
_VERB_STEM = re.compile(r"(있|없|했|았|었|하)$")
_STOP = {"여러분", "오늘", "정말", "진짜", "그리고", "그래서", "하지만", "이렇게", "저렇게", "그냥", "바로",
         "지금", "우리", "이번", "다음", "영상", "구독", "좋아요", "알림", "부탁", "드려요", "있습니다",
         "합니다", "입니다", "있어요", "해요", "보세요", "같아요", "번째", "마지막", "처음", "하나", "모두", "the", "and", "you", "this", "that"}


@dataclass
class Scene:
    index: int
    text: str
    keywords: list[str] = field(default_factory=list)
    keyword_source: str = ""        # ollama | fallback


def split_scenes(script: str, min_seconds: float = 2.5, max_chars: int = 90) -> list[Scene]:
    sentences = tts_lines(script)
    scenes: list[str] = []
    cur = ""
    for s in sentences:
        if cur and len(cur) + 1 + len(s) > max_chars:
            scenes.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
        if len(cur) / CHARS_PER_SECOND >= min_seconds:
            scenes.append(cur)
            cur = ""
    if cur:
        if scenes and len(cur) / CHARS_PER_SECOND < min_seconds and len(scenes[-1]) + 1 + len(cur) <= max_chars:
            scenes[-1] += " " + cur
        else:
            scenes.append(cur)
    return [Scene(i, t) for i, t in enumerate(scenes, 1)]


def fallback_keywords(text: str, n: int = 3) -> list[str]:
    """Ollama 없이: 조사를 뗀 2글자 이상 단어 중 긴 것부터 (한국어 검색은 결과가 적을 수 있음)."""
    words = []
    for tok in re.findall(r"[0-9A-Za-z가-힣]+", text):
        w = tok
        if re.search(r"[가-힣]", tok):
            w = _COPULA.sub("", w)
            stripped = _JOSA.sub("", w) if len(w) > 2 else w
            # 조사가 붙어 있으면 명사로 보고, 아니면 어미 모양으로 동사/형용사를 거른다
            if (stripped == w == tok and _VERBISH.search(w)) or _VERB_STEM.search(stripped):
                continue
            w = stripped
        if len(w) >= 2 and w.lower() not in _STOP and not w.isdigit() and w not in words:
            words.append(w)
    words.sort(key=len, reverse=True)
    return words[:n]


def parse_keywords(text: str, count: int, per_scene: int) -> dict[int, list[str]]:
    data = _extract_json(text)
    items = data if isinstance(data, list) else (data.get("scenes") or data.get("씬") or [])
    out: dict[int, list[str]] = {}
    for pos, it in enumerate(items, 1):
        if not isinstance(it, dict):
            continue
        try:
            idx = int(it.get("scene") or it.get("씬") or pos)
        except (TypeError, ValueError):
            idx = pos
        kws = it.get("keywords") or it.get("키워드") or []
        if isinstance(kws, str):
            kws = [k for k in re.split(r"[,/]", kws)]
        kws = [re.sub(r"\s+", " ", str(k)).strip(" \"'") for k in kws]
        kws = [k for k in kws if k and len(k) <= 40][:per_scene]
        if 1 <= idx <= count and kws:
            out[idx] = kws
    return out


def extract_keywords(scenes: list[Scene], *, client=None, prompts_dir: Path | None = None,
                     title: str = "", per_scene: int = 3, options: dict | None = None,
                     cancel: threading.Event | None = None, on_status=None) -> list[Scene]:
    """씬마다 keywords 채우기. client(OllamaClient)가 없거나 실패하면 간이 키워드."""
    got: dict[int, list[str]] = {}
    if client is not None and scenes:
        try:
            template = load_template(prompts_dir, KEYWORDS_PROMPT) if prompts_dir else _default_template()
            prompt = render_template(template, {
                "title": title or "(제목 없음)", "keywords_per_scene": per_scene,
                "scenes": "\n".join(f"{s.index}. {s.text}" for s in scenes)})
            text = client.chat(prompt, json_mode=True, options={"temperature": 0.3, **(options or {})},
                               cancel=cancel)
            got = parse_keywords(text, len(scenes), per_scene)
            log.info("Ollama 키워드: %d/%d개 씬", len(got), len(scenes))
        except GenerationError as exc:
            log.warning("키워드 응답 해석 실패 → 간이 키워드 사용: %s", exc)
        except Exception as exc:
            from ..ollama_client import GenerationCancelled

            if isinstance(exc, GenerationCancelled):
                raise
            log.warning("Ollama 키워드 추출 실패 → 간이 키워드 사용: %s", exc)
            if on_status:
                on_status(f"Ollama 키워드 추출 실패({exc.__class__.__name__}) → 대본 단어로 검색합니다")
    for s in scenes:
        if s.index in got:
            s.keywords, s.keyword_source = got[s.index], "ollama"
        else:
            s.keywords, s.keyword_source = fallback_keywords(s.text, per_scene), "fallback"
    return scenes


def _default_template() -> str:
    return (Path(__file__).resolve().parent.parent / "prompts" / KEYWORDS_PROMPT).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    from ..ollama_client import DEFAULT_HOST, DEFAULT_MODEL, OllamaClient

    parser = argparse.ArgumentParser(description="씬 분리 + 키워드 추출 단독 테스트")
    parser.add_argument("script", help="대본 텍스트 파일")
    parser.add_argument("--no-ollama", action="store_true")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    scenes = split_scenes(Path(args.script).read_text(encoding="utf-8"))
    client = None if args.no_ollama else OllamaClient(args.host, args.model)
    if client is not None and not client.status().ok:
        print(client.status().message + "\n→ 간이 키워드로 진행합니다.")
        client = None
    extract_keywords(scenes, client=client)
    for s in scenes:
        print(f"[{s.index}] ({len(s.text) / CHARS_PER_SECOND:.1f}초 예상) {s.text}\n"
              f"     → {s.keywords} ({s.keyword_source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
