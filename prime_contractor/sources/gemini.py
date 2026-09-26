"""구글 Gemini 무료 API — 규칙 기반 진단이 놓칠 수 있는 걸 한 번 더 물어본다.

    https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=...

무료 키 한도 안에서 쓰는 용도라 실패해도(키 없음, 한도 초과, 인터넷 안 됨)
예외를 그대로 띄우지 않고 짧은 한국어 메시지로 바꾼다. 이 기능이 없어도
diagnosis.py 의 규칙 기반 진단은 그대로 동작해야 하므로, 여기서 나는 오류가
나머지 화면을 막아서는 안 된다.
"""
from __future__ import annotations

import requests

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
#: 무료 등급에서 넉넉히 쓸 수 있는 가벼운 모델. 이름이 바뀌면 여기만 고치면 된다.
DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiError(RuntimeError):
    pass


class GeminiClient:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 30.0,
                session: requests.Session | None = None) -> None:
        if not api_key:
            raise GeminiError("Gemini API 키가 없습니다.")
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self.session = session or requests.Session()

    def generate(self, prompt: str) -> str:
        url = f"{API_BASE}/{self.model}:generateContent"
        try:
            response = self.session.post(
                url, params={"key": self.api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                headers={"Content-Type": "application/json"},
                timeout=self.timeout)
        except requests.RequestException as exc:
            raise GeminiError(f"인터넷 연결을 확인해 주세요: {exc}") from exc

        if response.status_code == 429:
            raise GeminiError("오늘 무료 사용량을 다 썼습니다. 내일 다시 눌러 주세요.")
        if response.status_code in (400, 401, 403):
            raise GeminiError("API 키가 거절됐습니다. 키를 다시 확인해 주세요.")
        try:
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise GeminiError(f"Gemini 응답을 읽지 못했습니다: {exc}") from exc
        return _extract_text(payload)


def _extract_text(payload: dict) -> str:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError("Gemini 응답 형식을 이해하지 못했습니다.") from exc
    if not text:
        raise GeminiError("Gemini 가 빈 답을 돌려줬습니다.")
    return text
