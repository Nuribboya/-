"""터미널 명령어 없이 브라우저에서 쓰는 leadradar 웹 UI.

키워드만 입력하고 버튼을 누르면 DART 후보 발굴 -> LLM 채점까지 한번에 돌아간다.

실행:
    uvicorn leadradar.webapp.main:app --reload

브라우저에서 http://localhost:8000 접속. ANTHROPIC_API_KEY, DART_API_KEY
환경변수가 설정되어 있어야 하고, KAKAO_API_KEY는 선택(없으면 거리 계산만 생략).
leadradar/config.yaml(LEADRADAR_CONFIG 환경변수로 경로 변경 가능)이 있어야 한다.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..config import LeadRadarConfig
from ..discovery import discover_candidates
from ..pipeline import run as run_scoring

app = FastAPI()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

DEFAULT_KEYWORDS = "반도체 디스플레이 자동화 제어 전장 전기 계측 검사 테스트 정밀 파워 ESS PCS 로봇 엔지니어링 배전반"
DEFAULT_ORIGIN = "경기도 안성시"


def _config_path() -> str:
    return os.environ.get("LEADRADAR_CONFIG", "leadradar/config.yaml")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "results": None,
            "error": None,
            "keywords": DEFAULT_KEYWORDS,
            "origin": DEFAULT_ORIGIN,
        },
    )


@app.post("/run", response_class=HTMLResponse)
def run(
    request: Request,
    keywords: str = Form(DEFAULT_KEYWORDS),
    origin: str = Form(DEFAULT_ORIGIN),
) -> HTMLResponse:
    keyword_list = keywords.split()
    error = None
    results = None
    try:
        config = LeadRadarConfig.from_yaml(_config_path())
        dart_api_key = os.environ["DART_API_KEY"]
        kakao_api_key = os.environ.get("KAKAO_API_KEY")
        candidates = discover_candidates(
            dart_api_key,
            keyword_list,
            kakao_api_key=kakao_api_key,
            origin_address=origin,
        )
        results = run_scoring(config, candidates)
    except Exception as exc:  # 화면에 에러 메시지를 그대로 보여주기 위함
        error = str(exc)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "results": results,
            "error": error,
            "keywords": keywords,
            "origin": origin,
        },
    )
