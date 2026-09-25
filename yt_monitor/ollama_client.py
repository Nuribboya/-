"""로컬 Ollama REST API 클라이언트 (requests 사용).

- 서버가 떠 있는지, 모델이 받아져 있는지 확인: GET /api/tags
- 생성: POST /api/chat (stream 지원 → GUI에서 글자가 나오는 대로 보여줌)

단독 테스트 (config 없이도 동작):
    python -m yt_monitor.ollama_client --check
    python -m yt_monitor.ollama_client --prompt "유튜브 채널 이름 3개 추천해줘"
    python -m yt_monitor.ollama_client --model qwen2.5:7b --host http://localhost:11434 --prompt "..."
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Callable

import requests

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"
DOWNLOAD_URL = "https://ollama.com/download"


class OllamaError(RuntimeError):
    pass


class OllamaUnavailable(OllamaError):
    """서버에 연결할 수 없음 (Ollama 미설치/미실행)."""


class ModelMissing(OllamaError):
    """서버는 떠 있지만 모델이 로컬에 없음."""


class GenerationCancelled(OllamaError):
    pass


def pull_command(model: str) -> str:
    return f"ollama pull {model}"


@dataclass
class OllamaStatus:
    server_up: bool
    model_present: bool
    model: str
    host: str
    models: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.server_up and self.model_present

    @property
    def message(self) -> str:
        if not self.server_up:
            return (f"Ollama 서버({self.host})에 연결할 수 없습니다.\n"
                    f"Ollama를 설치·실행했는지 확인하세요. (설치: {DOWNLOAD_URL})")
        if not self.model_present:
            return (f"모델 '{self.model}' 이(가) 아직 받아져 있지 않습니다.\n"
                    f"명령 프롬프트에서 먼저 실행하세요:\n\n    {pull_command(self.model)}")
        return f"Ollama 준비 완료 ({self.model})"


def _model_matches(wanted: str, available: str) -> bool:
    if ":" not in wanted:
        wanted += ":latest"
    if ":" not in available:
        available += ":latest"
    return wanted == available


class OllamaClient:
    def __init__(self, host: str = DEFAULT_HOST, model: str = DEFAULT_MODEL,
                 timeout: float = 900, session: requests.Session | None = None):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.session = session or requests.Session()
        # 로컬 서버이므로 시스템 프록시 설정을 타지 않게 한다
        self.session.trust_env = False

    # ---- 상태 확인 ------------------------------------------------------------

    def list_models(self) -> list[str]:
        try:
            resp = self.session.get(f"{self.host}/api/tags", timeout=5)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise OllamaUnavailable(str(exc)) from exc
        return [m.get("name") or m.get("model", "") for m in resp.json().get("models", [])]

    def status(self) -> OllamaStatus:
        try:
            models = self.list_models()
        except OllamaUnavailable:
            return OllamaStatus(False, False, self.model, self.host)
        present = any(_model_matches(self.model, m) for m in models)
        return OllamaStatus(True, present, self.model, self.host, models)

    def ensure_ready(self) -> None:
        st = self.status()
        if not st.server_up:
            raise OllamaUnavailable(st.message)
        if not st.model_present:
            raise ModelMissing(st.message)

    def unload(self) -> None:
        """모델을 그래픽카드 메모리에서 바로 내린다 (AI 이미지 생성에 메모리를 넘겨주기 위해)."""
        try:
            self.session.post(f"{self.host}/api/generate", json={"model": self.model, "keep_alive": 0},
                              timeout=15)
        except requests.RequestException:
            pass

    # ---- 생성 ---------------------------------------------------------------

    def chat(self, prompt: str, *, system: str | None = None, json_mode: bool = False,
             options: dict | None = None, on_token: Callable[[str], None] | None = None,
             cancel: threading.Event | None = None, images: list[str] | None = None) -> str:
        """프롬프트 1개를 보내고 전체 응답 텍스트를 돌려준다.

        on_token 을 주면 스트리밍으로 받으면서 조각마다 호출한다.
        cancel 이벤트가 set 되면 GenerationCancelled 를 던진다.
        images: base64 이미지 목록 (비전 모델용, 예: qwen2.5vl)
        """
        user = {"role": "user", "content": prompt}
        if images:
            user["images"] = list(images)
        messages = ([{"role": "system", "content": system}] if system else []) + [user]
        payload = {"model": self.model, "messages": messages, "stream": on_token is not None,
                   "options": options or {}}
        if json_mode:
            payload["format"] = "json"
        try:
            resp = self.session.post(f"{self.host}/api/chat", json=payload,
                                     timeout=(5, self.timeout), stream=on_token is not None)
        except requests.ConnectionError as exc:
            raise OllamaUnavailable(OllamaStatus(False, False, self.model, self.host).message) from exc
        except requests.RequestException as exc:
            raise OllamaError(f"Ollama 요청 실패: {exc}") from exc

        with resp:
            if resp.status_code == 404:
                raise ModelMissing(OllamaStatus(True, False, self.model, self.host).message)
            if resp.status_code >= 400:
                raise OllamaError(f"Ollama 오류 {resp.status_code}: {resp.text[:300]}")
            if on_token is None:
                data = resp.json()
                if "error" in data:
                    raise OllamaError(data["error"])
                return data.get("message", {}).get("content", "")

            parts: list[str] = []
            for line in resp.iter_lines():
                if cancel is not None and cancel.is_set():
                    raise GenerationCancelled("사용자가 생성을 취소했습니다.")
                if not line:
                    continue
                data = json.loads(line)
                if "error" in data:
                    raise OllamaError(data["error"])
                piece = data.get("message", {}).get("content", "")
                if piece:
                    parts.append(piece)
                    on_token(piece)
                if data.get("done"):
                    break
            return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Ollama 연결 단독 테스트")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--check", action="store_true", help="서버/모델 상태만 확인")
    parser.add_argument("--prompt", help="보낼 프롬프트 (스트리밍 출력)")
    args = parser.parse_args(argv)

    client = OllamaClient(args.host, args.model)
    st = client.status()
    print(st.message)
    if st.server_up:
        print("설치된 모델: " + (", ".join(st.models) or "(없음)"))
    if not st.ok:
        return 1
    if args.prompt:
        client.chat(args.prompt, on_token=lambda t: (sys.stdout.write(t), sys.stdout.flush()))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
