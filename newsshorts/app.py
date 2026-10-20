"""Local web app: start/stop watching, review drafts, approve or take down, edit settings.

    python -m newsshorts.app            # http://127.0.0.1:8765 (this PC only)
    python -m newsshorts.app --lan      # also reachable from a phone on the same Wi-Fi, PIN-protected
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import secrets
import shutil
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from newsshorts.audience import PRESETS
from newsshorts.config import Config, Source, load_config
from newsshorts.log import log, recent_lines
from newsshorts.pipeline import (auto_published_today, list_drafts, load_draft, publish_draft,
                                 reject_draft, save_draft, takedown_draft)
from newsshorts.runner import Watcher, process, work_lock
from newsshorts.samples import SAMPLE_ITEM, SAMPLE_US_ITEM

HERE = Path(__file__).resolve().parent
EXAMPLE_CONFIG = HERE / "config.example.json"
VOICES = ["ko-KR-InJoonNeural", "ko-KR-SunHiNeural", "ko-KR-HyunsuMultilingualNeural"]


class State:
    def __init__(self, config_path: Path, use_claude: bool, use_tts: bool, pin: str | None):
        self.config_path = config_path
        self.use_claude = use_claude
        self.use_tts = use_tts
        self.pin = pin
        self.lock = threading.Lock()
        self.config = self._load()
        self.watcher: Watcher | None = None

    def _load(self) -> Config:
        if not self.config_path.exists():
            shutil.copy(EXAMPLE_CONFIG, self.config_path)
        config = load_config(self.config_path)
        if config.anthropic_api_key:
            os.environ["ANTHROPIC_API_KEY"] = config.anthropic_api_key
        return config

    def reload(self) -> None:
        old = self.watcher
        was_running = old is not None and old.running
        self.stop()
        self.config = self._load()
        self.watcher = None
        if was_running:
            self.start(after=old._thread)

    def start(self, after: threading.Thread | None = None) -> None:
        with self.lock:
            if self.watcher is None:
                self.watcher = Watcher(self.config, self.use_claude, self.use_tts)
            self.watcher.start(after)

    def stop(self) -> None:
        with self.lock:
            if self.watcher is not None:
                self.watcher.stop()


def create_app(state: State) -> FastAPI:
    app = FastAPI(title="뉴스 쇼츠 관리")

    @app.middleware("http")
    async def require_pin(request: Request, call_next):
        path = request.url.path
        # A custom header can't be sent cross-site without a CORS preflight we never allow,
        # so other web pages can't trigger uploads through this local server.
        if request.method == "POST" and request.headers.get("x-app") != "1":
            return JSONResponse({"detail": "잘못된 요청"}, status_code=403)
        if state.pin and (path.startswith("/api") or path.startswith("/media")):
            given = request.headers.get("x-pin") or request.query_params.get("pin")
            if not secrets.compare_digest(given or "", state.pin):
                return JSONResponse({"detail": "PIN이 필요합니다"}, status_code=401)
        return await call_next(request)

    def draft_dir(name: str) -> Path:
        root = Path(state.config.output_dir).resolve()
        path = (root / name).resolve()
        if path.parent != root or not (path / "draft.json").exists():
            raise HTTPException(404, "초안을 찾을 수 없습니다")
        return path

    def run(action, *args):
        try:
            return action(*args)
        except HTTPException:
            raise
        except Exception as exc:
            log(f"[error] {exc}")
            raise HTTPException(500, str(exc)) from exc

    @app.get("/")
    def index():
        return FileResponse(HERE / "static" / "index.html")

    @app.get("/api/status")
    def status():
        w = state.watcher
        return {
            "watching": bool(w and w.running),
            "last_poll": w.last_poll if w else None,
            "auto_publish": state.config.auto_publish,
            "auto_published_today": auto_published_today(state.config),
            "auto_daily_limit": state.config.auto_daily_limit,
            "pending": len(list_drafts(state.config, "pending")),
            "claude_ready": bool(os.environ.get("ANTHROPIC_API_KEY")) and state.use_claude,
            "youtube_ready": Path(state.config.youtube_client_secret).exists(),
            "log": recent_lines(80),
        }

    @app.post("/api/watch/start")
    def watch_start():
        run(state.start)
        return {"ok": True}

    @app.post("/api/watch/stop")
    def watch_stop():
        state.stop()
        return {"ok": True}

    @app.post("/api/poll")
    def poll_now():
        if state.watcher is None:
            state.watcher = Watcher(state.config, state.use_claude, state.use_tts)
        run(state.watcher.poll_once)
        return {"ok": True}

    @app.post("/api/demo")
    def demo(us: bool = False):
        draft = run(process, SAMPLE_US_ITEM if us else SAMPLE_ITEM, state.config,
                    state.use_claude, state.use_tts)
        return {"name": draft.name}

    @app.get("/api/drafts")
    def drafts(status: str | None = None):
        out = []
        for d in reversed(list_drafts(state.config, status or None)):
            meta = load_draft(d)
            out.append({"name": d.name, **meta})
        return out

    @app.post("/api/drafts/{name}/update")
    async def update(name: str, request: Request):
        body = await request.json()
        with work_lock:
            d = draft_dir(name)
            meta = load_draft(d)
            if meta["status"] != "pending":
                raise HTTPException(400, "대기 중인 초안만 고칠 수 있습니다")
            for key in ("title", "description"):
                if isinstance(body.get(key), str):
                    meta["script"][key] = body[key].strip()
            save_draft(d, meta)
        return {"ok": True}

    @app.post("/api/drafts/{name}/approve")
    def approve(name: str):
        with work_lock:
            video_id = run(publish_draft, state.config, draft_dir(name))
        log(f"[uploaded] https://youtube.com/shorts/{video_id}")
        return {"youtube_id": video_id}

    @app.post("/api/drafts/{name}/reject")
    def reject(name: str):
        with work_lock:
            reject_draft(draft_dir(name))
        return {"ok": True}

    @app.post("/api/drafts/{name}/takedown")
    def takedown(name: str):
        with work_lock:
            video_id = run(takedown_draft, state.config, draft_dir(name))
        log(f"[taken down] {video_id}")
        return {"ok": True}

    @app.get("/media/{name}/video.mp4")
    def video(name: str):
        return FileResponse(draft_dir(name) / "video.mp4", media_type="video/mp4")

    @app.get("/api/config")
    def get_config():
        data = dataclasses.asdict(state.config)
        key = data.get("anthropic_api_key") or ""
        data["anthropic_api_key"] = f"…{key[-4:]}" if key else ""
        return {"config": data, "audiences": sorted(PRESETS), "voices": VOICES}

    @app.post("/api/config")
    async def set_config(request: Request):
        body = await request.json()
        current = json.loads(state.config_path.read_text(encoding="utf-8"))
        allowed = {f.name for f in dataclasses.fields(Config)}
        for key, value in body.items():
            if key not in allowed:
                continue
            # The masked key comes back unchanged when the user didn't type a new one.
            if key == "anthropic_api_key" and (not value or str(value).startswith("…")):
                continue
            current[key] = value
        try:
            Config(sources=[Source(**s) for s in current.get("sources", [])],
                   **{k: v for k, v in current.items() if k != "sources"})
        except TypeError as exc:
            raise HTTPException(400, f"설정 형식 오류: {exc}") from exc
        state.config_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        state.reload()
        log("[info] 설정 저장됨")
        return {"ok": True}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="뉴스 쇼츠 관리 앱")
    parser.add_argument("--config", default="newsshorts/config.json")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--lan", action="store_true", help="같은 와이파이의 휴대폰에서도 접속 (PIN 필요)")
    parser.add_argument("--no-claude", action="store_true")
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    import uvicorn

    pin = f"{secrets.randbelow(10**6):06d}" if args.lan else None
    state = State(Path(args.config), not args.no_claude, not args.no_tts, pin)
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  뉴스 쇼츠 관리 앱: {url}")
    if pin:
        print(f"  휴대폰: 이 PC의 IP 주소:{args.port} 로 접속, PIN {pin}\n")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(state), host=host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
