"""테스트용 가짜 Ollama 서버 (/api/tags, /api/chat 스트리밍/비스트리밍).

    python tests/fake_ollama.py 11434    # 수동 테스트용으로 띄우기
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOPICS_JSON = {
    "topics": [
        {"topic": "자취생 3천원 도시락 5종", "reason": "상위 영상의 '저예산 자취' 패턴을 잇는다",
         "titles": ["3천원으로 일주일 도시락 끝", "자취생 도시락 5종, 전부 3천원 이하", "편의점보다 싼 도시락"]},
        {"topic": "편의점 신상 조합 레시피", "reason": "편의점 키워드 반응이 좋음",
         "titles": ["편의점 신상 조합 TOP5", "이 조합 미쳤다"]},
        {"topic": "전자레인지 5분 요리", "reason": "숏폼 반응이 좋은 간편 요리",
         "titles": ["전자레인지로 5분 만에", "불 없이 만드는 요리"]},
    ],
    "best": 2,
}

SCRIPT_TEXT = """# 대본
제목: 편의점 신상 조합 TOP5

**내레이션:** 여러분, 편의점에서 이 조합 먹어보셨나요? [효과음]
오늘은 진짜 맛있는 조합만 모았습니다. 끝까지 보시면 마지막에 반전이 있어요!
- 첫 번째 조합은 컵라면과 삼각김밥입니다 😋
(화면 전환)
두 번째는 요거트와 그래놀라예요.
구독과 좋아요 부탁드려요.
"""


class _Handler(BaseHTTPRequestHandler):
    models = ["qwen2.5:7b", "llama3:latest"]
    requests_log: list = []

    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._json(200, {"models": [{"name": m, "model": m} for m in self.models]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests_log.append(payload)
        if payload["model"] not in self.models:
            return self._json(404, {"error": f"model '{payload['model']}' not found"})
        content = (json.dumps(TOPICS_JSON, ensure_ascii=False) if payload.get("format") == "json"
                   else SCRIPT_TEXT)
        if not payload.get("stream"):
            return self._json(200, {"message": {"role": "assistant", "content": content}, "done": True})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for i in range(0, len(content), 20):
            chunk = {"message": {"role": "assistant", "content": content[i : i + 20]}, "done": False}
            self.wfile.write((json.dumps(chunk, ensure_ascii=False) + "\n").encode())
        self.wfile.write(b'{"message":{"role":"assistant","content":""},"done":true}\n')


def start_fake_ollama(port: int = 0, models: list[str] | None = None):
    handler = type("Handler", (_Handler,), {"models": models or _Handler.models, "requests_log": []})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}", handler


if __name__ == "__main__":
    import sys

    srv, url, _ = start_fake_ollama(int(sys.argv[1]) if len(sys.argv) > 1 else 11434)
    print(f"가짜 Ollama 서버: {url}")
    threading.Event().wait()
