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
#: 위 모델이 내려가 404 가 나면 이 이름으로 한 번 더 시도한다. '늘 최신 Flash'를
#: 가리키는 별칭이라 모델 세대가 바뀌어도 살아 있다.
FALLBACK_MODEL = "gemini-flash-latest"


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
            # 키는 주소(?key=)가 아니라 머리글로 보낸다. 주소에 넣으면 연결 오류 문구에
            # 키가 그대로 찍혀 화면에 나온다.
            response = self.session.post(
                url, json={"contents": [{"parts": [{"text": prompt}]}]},
                headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
                timeout=self.timeout)
        except requests.RequestException as exc:
            raise GeminiError(f"인터넷 연결을 확인해 주세요: {exc}") from exc

        if response.status_code == 404 and self.model != FALLBACK_MODEL:
            # 구글이 옛 모델을 내리면 404 가 난다. 키 문제가 아니니 최신 별칭으로 바꿔 본다.
            self.model = FALLBACK_MODEL
            return self.generate(prompt)
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
