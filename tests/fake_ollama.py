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

KEYWORDS_JSON = {
    "mood": "dramatic",
    "scenes": [
        {"scene": 1, "keywords": ["convenience store", "snack shelf", "night street"],
         "image_prompt": "neon convenience store at night, low angle, rain reflections"},
        {"scene": 2, "keywords": ["instant noodles", "rice ball"]},
        {"scene": 3, "keywords": ["yogurt granola", "breakfast bowl"]},
        {"scene": 4, "keywords": ["subscribe button", "smartphone"]},
    ]
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

THUMB_JSON = {"text_on_screen": "DON'T EAT THIS", "subject": "chocolate croissant", "face": "one face close-up",
              "emotion": "shocked", "shot": "extreme close-up", "colors": "brown, yellow", "hook": "huge bite reaction"}

UPLOAD_JSON = {
    "titles": [{"title": "편의점 조합 TOP5", "why": "숫자가 있어 궁금함"},
               {"title": "Gas Station Snack Hacks That Work", "why": "구체적인 약속"},
               {"title": "You've Been Eating Chips Wrong", "why": "도발적인 문장"}],
    "best": 3, "category_id": "26", "category_reason": "노하우 쇼츠가 요즘 잘 뜸",
    "description": "Which one would you try first?\nComment below!",
    "hashtags": ["#Shorts", "snackhack", "#food"], "tags": ["snack hacks", "gas station food"],
}

# 영어 프롬프트에는 qwen이 실제로 돌려준 모양대로 (머리말 · 한국어 제목 섞임)
SCRIPT_TEXT_EN = """Sure, here is the voice-over script for your YouTube Shorts video:
편의점 꿀조합
You won't believe these gas station snack hacks.
Number one is chips dipped in chocolate.
The last one is wild. Which one would you try?
"""


SCRIPT_TEXT_EN_LONG = SCRIPT_TEXT_EN.split("\n", 2)[2] + "\n".join(
    f"Snack fact number {i} will honestly change how you shop at gas stations forever." for i in range(1, 12)) + "\n"
SCRIPT_TEXT_KO_MORE = "\n".join(f"{i}번째 조합은 정말 의외로 맛있어서 다들 깜짝 놀라요." for i in range(3, 30)) + "\n"


class _Handler(BaseHTTPRequestHandler):
    extend_ok = True                       # False면 늘려 달라고 해도 똑같이 짧게 답한다
    models = ["qwen2.5:7b", "llama3:latest"]
    requests_log: list = []
    unloads: list = []

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
        if self.path == "/api/generate":            # keep_alive=0 → 모델 내리기
            type(self).unloads.append(payload.get("model"))
            return self._json(200, {"done": True})
        if payload["model"] not in self.models:
            return self._json(404, {"error": f"model '{payload['model']}' not found"})
        prompt = payload["messages"][-1]["content"]
        if payload.get("format") == "json":
            if payload["messages"][-1].get("images"):          # 비전 모델 (썸네일 분석)
                data = THUMB_JSON
            elif "category_id" in prompt:
                data = UPLOAD_JSON
            else:
                data = KEYWORDS_JSON if "Pexels" in prompt else TOPICS_JSON
            content = json.dumps(data, ensure_ascii=False)
        elif "too short" in prompt:
            content = SCRIPT_TEXT_EN_LONG if type(self).extend_ok else SCRIPT_TEXT_EN
        elif "너무 짧습니다" in prompt:
            content = SCRIPT_TEXT if not type(self).extend_ok else SCRIPT_TEXT + SCRIPT_TEXT_KO_MORE
        elif "American English" in prompt:
            content = SCRIPT_TEXT_EN
        else:
            content = SCRIPT_TEXT
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
    handler = type("Handler", (_Handler,), {"models": models or _Handler.models, "requests_log": [],
                                            "unloads": []})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}", handler


if __name__ == "__main__":
    import sys

    srv, url, _ = start_fake_ollama(int(sys.argv[1]) if len(sys.argv) > 1 else 11434)
    print(f"가짜 Ollama 서버: {url}")
    threading.Event().wait()
