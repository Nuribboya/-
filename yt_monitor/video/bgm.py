"""배경음악(BGM): 대본 분위기에 맞는 곡을 bgm/ 폴더에서 골라 내레이션 밑에 깐다.

음악은 저작권 문제 때문에 인터넷에서 자동으로 받지 않는다. 사용자가 무료 곡을 폴더에 넣어 두면:

    bgm/
      energetic/   신나는 곡 (.mp3 .wav .m4a .ogg)
      dramatic/    긴장감 · 웅장한 곡
      mysterious/  미스터리 · 어두운 곡
      calm/        잔잔한 곡
      playful/     통통 튀는 · 코믹한 곡
      emotional/   감성 · 뭉클한 곡
      (bgm/ 바로 아래에 넣은 곡은 분위기 폴더가 비었을 때 사용)

영상 분위기(mood)에 맞는 폴더에서 한 곡을 무작위로 고르고, 목소리가 나올 때는 음악을 자동으로 줄인다(더킹).
곡 옆에 같은 이름의 .txt (예: song.mp3 + song.txt)를 두면 그 내용을 출처(설명란)에 붙인다.

추천 무료 음원: YouTube 스튜디오 → 오디오 보관함 (YouTube에 올리는 영상에 무료, 저작권 걱정 없음)
"""

from __future__ import annotations

import random
from pathlib import Path

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
LIBRARY_URL = "https://studio.youtube.com → 왼쪽 메뉴 '오디오 보관함'"

# 분위기 → 추천 (YouTube 오디오 보관함의 '분위기/장르' 필터 이름 그대로)
MOOD_BGM: dict[str, dict[str, str]] = {
    "energetic": {"ko": "빠르고 신나는 비트 (BPM 120 이상)", "mood": "Bright, Happy",
                  "genre": "Dance & Electronic, Hip Hop & Rap", "search": "upbeat energetic electronic"},
    "dramatic": {"ko": "긴장감 있는 웅장한 시네마틱", "mood": "Dramatic",
                 "genre": "Cinematic", "search": "epic dramatic trailer"},
    "mysterious": {"ko": "어둡고 묘한 분위기 (낮은 음, 긴장감)", "mood": "Dark",
                   "genre": "Ambient, Cinematic", "search": "mysterious suspense dark"},
    "calm": {"ko": "잔잔한 로파이 · 피아노", "mood": "Calm",
             "genre": "Ambient, Classical", "search": "calm lofi piano"},
    "playful": {"ko": "통통 튀는 코믹 · 펑키", "mood": "Funky, Happy",
                "genre": "Pop, Children's", "search": "playful quirky funny"},
    "emotional": {"ko": "뭉클한 피아노 · 스트링", "mood": "Sad, Inspirational",
                  "genre": "Cinematic, Classical", "search": "emotional piano strings"},
}

README_NAME = "여기에_음악_넣기.txt"
README = f"""배경음악 폴더

분위기 폴더에 무료 음악 파일(.mp3 .wav .m4a .ogg)을 넣어 두면, 영상 분위기에 맞는 곡을 자동으로 골라 깔아줍니다.
  energetic = 신나는 곡 / dramatic = 웅장·긴장 / mysterious = 미스터리 / calm = 잔잔 / playful = 코믹 / emotional = 감성

음원 받는 곳 (무료 · 저작권 걱정 없음): {LIBRARY_URL}
  - 분위기/장르 필터로 고른 뒤 다운로드 → 이 폴더의 알맞은 분위기 폴더에 넣기
  - '저작자 표시 필요' 곡은 곡과 같은 이름의 .txt 파일에 표시 문구를 적어 두면 설명란 출처에 자동으로 붙습니다.
    예) song.mp3 + song.txt

⚠ 유명 가요/팝송은 넣지 마세요. 저작권 신고(Content ID)로 수익이 원곡자에게 가거나 영상이 막힐 수 있습니다.
"""


def ensure_bgm_dirs(root: Path) -> Path:
    """bgm/ 과 분위기별 폴더 + 안내 파일을 만든다 (이미 있으면 그대로)."""
    root = Path(root)
    for mood in MOOD_BGM:
        (root / mood).mkdir(parents=True, exist_ok=True)
    readme = root / README_NAME
    if not readme.exists():
        readme.write_text(README, encoding="utf-8")
    return root


def _audio_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS)


def pick_bgm(root: Path, mood: str, rng: random.Random | None = None) -> Path | None:
    """분위기 폴더 → bgm/ 바로 아래 → 아무 분위기 폴더 순으로 한 곡."""
    rng = rng or random.Random()
    root = Path(root)
    for files in (_audio_files(root / mood), _audio_files(root),
                  [f for m in MOOD_BGM for f in _audio_files(root / m)]):
        if files:
            return rng.choice(files)
    return None


def bgm_credit(track: Path) -> str:
    """곡 옆 같은 이름의 .txt (저작자 표시 문구)."""
    txt = Path(track).with_suffix(".txt")
    try:
        return txt.read_text(encoding="utf-8-sig").strip() if txt.is_file() else ""
    except OSError:
        return ""


def recommend_text(mood: str) -> str:
    """업로드정보.txt / 로그에 넣을 BGM 추천 (한국어)."""
    r = MOOD_BGM.get(mood) or MOOD_BGM["energetic"]
    return (f"{r['ko']}\n"
            f"   YouTube 오디오 보관함 필터 → 분위기: {r['mood']} / 장르: {r['genre']}\n"
            f"   다른 무료 사이트 검색어: \"{r['search']}\"")
