"""Click-friendly title tags ([속보], [단독], [미국], [전망]) that stay truthful.

Tags come from the original article text, never from the model, so a video can
only say [단독] when the outlet itself said so. Speculative reports keep a
[전망] tag so "could happen" never reads as "happened".
"""
from __future__ import annotations

import re

from newsshorts.script import Script
from newsshorts.sources import NewsItem

SPECULATIVE_MARKERS = [
    "검토", "추진", "가능성", "관측", "전망", "유력", "거론", "할 듯", "될 듯", "예상",
    "considering", "weighing", "could ", "might ", "expected to", "likely",
    "reportedly", "sources say", "plans to", "poised to",
]
# Articles whose subject is itself an unverified rumor. Repeating the rumor's
# content is defamation risk even with hedged wording, so these are skipped.
RUMOR_MARKERS = ["찌라시", "루머", "괴소문", "미확인 소문", "rumor", "unverified claim"]

_TAG_PREFIX = re.compile(r"^\s*(\[[^\]]{1,8}\]\s*)+")
# Outlets' own English tags, which duplicate ours once the title is tagged.
_EN_TAG_PREFIX = re.compile(r"^\s*(exclusive|breaking(\s+news)?)\s*:\s*", re.IGNORECASE)


def _text(item: NewsItem) -> str:
    return f"{item.title} {item.summary}".lower()


def is_speculative(item: NewsItem) -> bool:
    text = _text(item)
    return any(m in text for m in SPECULATIVE_MARKERS)


def is_rumor(item: NewsItem) -> bool:
    text = _text(item)
    return any(m in text for m in RUMOR_MARKERS)


def report_type(item: NewsItem) -> str:
    return "전망" if is_speculative(item) else "보도"


def title_labels(item: NewsItem) -> list[str]:
    title = item.title.lower()
    labels = []
    if "속보" in title or "breaking" in title:
        labels.append("속보")
    if "단독" in title or "exclusive" in title:
        labels.append("단독")
    if item.region == "us":
        labels.append("미국")
    if is_speculative(item):
        labels.append("전망")
    return labels


def apply_labels(title: str, labels: list[str]) -> str:
    """Replace any tags the model wrote with the ones the source justifies."""
    bare = _EN_TAG_PREFIX.sub("", _TAG_PREFIX.sub("", title)).strip()
    if "단독" not in labels:
        bare = bare.replace("단독", "").strip()
    prefix = "".join(f"[{l}]" for l in labels)
    return f"{prefix} {bare}".strip()[:100]


def ensure_attribution(script: Script, item: NewsItem) -> Script:
    """Make sure the narration names the outlet at least once."""
    if not any(item.source in s for s in script.sentences):
        script.sentences.insert(0, f"{item.source} 보도입니다.")
    return script


def finalize(script: Script, item: NewsItem) -> Script:
    script.title = apply_labels(script.title, title_labels(item))
    return ensure_attribution(script, item)
