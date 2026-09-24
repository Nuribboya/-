"""영상 생성 테스트용 가짜 서비스: Pexels API 서버 + edge-tts Communicate.

실제 Pexels/edge-tts 서버에 접속하지 않고 전체 파이프라인을 돌려볼 수 있다.
"""

from __future__ import annotations

import io
import json
import math
import struct
import subprocess
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from yt_monitor.video.tts import TICKS, EdgeTTS


def make_test_clip(ffmpeg: str, path: Path, pattern: str = "testsrc2", size: str = "320x568",
                   seconds: float = 2.0) -> bytes:
    """ffmpeg 테스트 패턴으로 작은 mp4를 만든다."""
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"{pattern}=s={size}:r=15", "-t", str(seconds), "-c:v", "libx264", "-preset",
                    "ultrafast", "-pix_fmt", "yuv420p", str(path)], check=True)
    return Path(path).read_bytes()


def start_fake_pexels(clip_bytes: dict[str, bytes], *, status: int = 200):
    """/videos/search → 검색어마다 영상 2개 (마지막 영상은 가로), /files/<name> → mp4 바이트."""
    names = sorted(clip_bytes)

    class Handler(BaseHTTPRequestHandler):
        log: list = []

        def log_message(self, *args):
            pass

        def do_GET(self):
            type(self).log.append((self.path, self.headers.get("Authorization")))
            if self.path.startswith("/videos/search"):
                if status != 200:
                    self.send_response(status)
                    self.end_headers()
                    self.wfile.write(b'{"error":"denied"}')
                    return
                from urllib.parse import parse_qs, urlparse

                q = parse_qs(urlparse(self.path).query)["query"][0]
                base = abs(hash(q)) % 10_000 * 10
                host = f"http://127.0.0.1:{self.server.server_address[1]}"
                videos = []
                for k, name in enumerate(names[:2]):
                    videos.append({
                        "id": base + k, "duration": 2, "url": f"https://www.pexels.com/video/{base + k}/",
                        "user": {"name": f"작가{k}"},
                        "video_files": [
                            {"id": 1, "quality": "sd", "file_type": "video/mp4", "width": 360, "height": 640,
                             "link": f"{host}/files/{name}"},
                            {"id": 2, "quality": "uhd", "file_type": "video/mp4", "width": 2160,
                             "height": 3840, "link": f"{host}/files/{name}"},
                        ]})
                body = json.dumps({"videos": videos}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path.startswith("/files/"):
                body = clip_bytes[self.path.rsplit("/", 1)[1]]
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
            else:
                body = b"{}"
                self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/videos/search", Handler


def tone_wav(seconds: float, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(seconds * rate)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / rate)))
                               for i in range(n)))
    return buf.getvalue()


class FakeCommunicate:
    """edge_tts.Communicate 흉내: 단어마다 0.3초 + 오디오(wav 바이트, 두 조각)."""

    def __init__(self, text: str):
        self.text = text

    def stream_sync(self):
        t = 0.1
        words = []
        for tok in self.text.split():
            word = tok.strip(".,!?")
            words.append({"type": "WordBoundary", "offset": int(t * TICKS), "duration": int(0.25 * TICKS),
                          "text": word})
            t += 0.3
        data = tone_wav(t + 0.2)
        yield {"type": "audio", "data": data[: len(data) // 2]}
        yield from words
        yield {"type": "audio", "data": data[len(data) // 2:]}


class FakeEdgeTTS(EdgeTTS):
    """네트워크 없이 EdgeTTS의 파싱/저장 코드를 그대로 타게 한다. fail_times 만큼 먼저 실패."""

    def __init__(self, *args, fail_times: int = 0, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_times = fail_times
        self.calls: list[str] = []

    def _communicate(self, text: str):
        self.calls.append(text)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("가짜 네트워크 오류")
        return FakeCommunicate(text)
