"""주기적으로 자동 갱신되는 그레이엄 스크리너 웹 대시보드.

실행:
    uvicorn stockscreener.web.main:app --reload

환경변수:
    STOCKSCREENER_TICKERS          쉼표로 구분한 티커 목록 (예: "AAPL,MSFT,005930.KS")
    STOCKSCREENER_SP500=1          번들된 S&P 500 전체(약 500종목)를 대상으로 실행
    STOCKSCREENER_REFRESH_SECONDS  갱신 주기(초). 기본 1800(30분).
                                   --sp500 사용 시 한 주기가 오래 걸릴 수 있으니
                                   더 길게(예: 7200) 잡는 것을 권장한다.
    STOCKSCREENER_NO_TICKER_NEWS   "0"이 아니면 종목별 뉴스 조회를 건너뛴다 (기본: 건너뜀).

브라우저는 이 서버가 미리 계산해 둔 최신 결과를 /api/data 에서 주기적으로
가져와 화면을 갱신한다 — 실제 야후 파이낸스/뉴스 조회는 서버(이 프로세스)가
백그라운드에서 수행하고, 브라우저는 외부 사이트에 직접 접속하지 않는다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from stockscreener import config
from stockscreener import report as report_fmt
from stockscreener.screener import Screener
from stockscreener.universe import load_sp500_tickers

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
_INDEX_HTML = (BASE_DIR / "templates" / "dashboard.html").read_text(encoding="utf-8")


def _load_configured_tickers() -> list[str]:
    env_tickers = os.environ.get("STOCKSCREENER_TICKERS")
    if env_tickers:
        return [t.strip() for t in env_tickers.split(",") if t.strip()]
    if os.environ.get("STOCKSCREENER_SP500"):
        return load_sp500_tickers()
    return list(config.DEFAULT_TICKERS)


REFRESH_INTERVAL_SECONDS = int(os.environ.get("STOCKSCREENER_REFRESH_SECONDS", "1800"))
FETCH_TICKER_NEWS = os.environ.get("STOCKSCREENER_NO_TICKER_NEWS", "1") == "0"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(_refresh_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="그레이엄 밸류 스크리너", lifespan=_lifespan)

_state_lock = threading.Lock()
_state: dict = {
    "payload": None,
    "last_refreshed_at": None,
    "is_refreshing": False,
    "last_error": None,
    "ticker_count": 0,
}


def _run_cycle() -> None:
    tickers = _load_configured_tickers()
    with _state_lock:
        _state["is_refreshing"] = True
        _state["ticker_count"] = len(tickers)

    try:
        screener = Screener(fetch_ticker_news=FETCH_TICKER_NEWS)
        market_news = screener.get_market_news()
        reports = screener.analyze(tickers)
        payload = json.loads(report_fmt.to_json(reports, market_news))
        with _state_lock:
            _state["payload"] = payload
            _state["last_refreshed_at"] = payload["generated_at"]
            _state["last_error"] = None
    except Exception as exc:  # 백그라운드 갱신 실패가 서버 자체를 죽이면 안 된다
        logger.exception("스크리닝 갱신 실패")
        with _state_lock:
            _state["last_error"] = str(exc)
    finally:
        with _state_lock:
            _state["is_refreshing"] = False


async def _refresh_loop() -> None:
    while True:
        await asyncio.to_thread(_run_cycle)
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _INDEX_HTML


@app.get("/api/data")
async def api_data() -> JSONResponse:
    with _state_lock:
        payload = _state["payload"]
    if payload is None:
        return JSONResponse({"reports": [], "market_news": [], "generated_at": None})
    return JSONResponse(payload)


@app.get("/api/status")
async def api_status() -> dict:
    with _state_lock:
        return {
            "is_refreshing": _state["is_refreshing"],
            "last_refreshed_at": _state["last_refreshed_at"],
            "last_error": _state["last_error"],
            "ticker_count": _state["ticker_count"],
            "refresh_interval_seconds": REFRESH_INTERVAL_SECONDS,
            "server_time": datetime.now(timezone.utc).isoformat(),
        }


@app.post("/api/refresh")
async def api_refresh() -> dict:
    with _state_lock:
        already_running = _state["is_refreshing"]
    if already_running:
        return {"started": False, "reason": "already_running"}
    asyncio.create_task(asyncio.to_thread(_run_cycle))
    return {"started": True}
