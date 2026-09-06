"""터미널 명령어 없이 브라우저에서 쓰는 leadradar 웹 UI.

키워드만 입력하고 버튼을 누르면 DART 후보 발굴 -> LLM 채점까지 한번에 돌아가고,
결과는 SQLite에 계속 쌓인다. 각 후보의 컨택 상태(미접촉/컨택함/성사/거절 등)를
사이트에서 직접 관리할 수 있다 - 이 기록이 나중에 진짜 학습 데이터가 된다.

실행:
    uvicorn leadradar.webapp.main:app --reload

브라우저에서 http://localhost:8000 접속. ANTHROPIC_API_KEY, DART_API_KEY
환경변수가 설정되어 있어야 하고, KAKAO_API_KEY는 선택(없으면 거리 계산만 생략).
leadradar/config.yaml(LEADRADAR_CONFIG 환경변수로 경로 변경 가능)이 있어야 한다.
LEADRADAR_RESULTS_DB 환경변수로 결과 DB 파일 경로를 바꿀 수 있다.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import LeadRadarConfig
from ..discovery import discover_candidates
from ..pipeline import run as run_scoring
from ..results_db import (
    CONTACT_STATUSES,
    DEFAULT_DB_PATH,
    init_db,
    list_candidates,
    update_contact_status,
    upsert_scored_candidates,
)

app = FastAPI()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

DEFAULT_KEYWORDS = "반도체 디스플레이 자동화 제어 전장 전기 계측 검사 테스트 정밀 파워 ESS PCS 로봇 엔지니어링 배전반"
DEFAULT_ORIGIN = "경기도 안성시"
DEFAULT_MIN_SCORE = 30


def _config_path() -> str:
    return os.environ.get("LEADRADAR_CONFIG", "leadradar/config.yaml")


def _results_db_path() -> str:
    path = os.environ.get("LEADRADAR_RESULTS_DB", DEFAULT_DB_PATH)
    init_db(path)
    return path


@app.get("/", response_class=HTMLResponse)
def index(request: Request, error: Optional[str] = None, min_score: int = DEFAULT_MIN_SCORE) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "candidates": list_candidates(_results_db_path(), min_score=min_score),
            "error": error,
            "keywords": DEFAULT_KEYWORDS,
            "origin": DEFAULT_ORIGIN,
            "statuses": CONTACT_STATUSES,
            "min_score": min_score,
        },
    )


@app.post("/run")
def run(
    keywords: str = Form(DEFAULT_KEYWORDS),
    origin: str = Form(DEFAULT_ORIGIN),
) -> RedirectResponse:
    try:
        config = LeadRadarConfig.from_yaml(_config_path())
        dart_api_key = os.environ["DART_API_KEY"]
        kakao_api_key = os.environ.get("KAKAO_API_KEY")
        candidates = discover_candidates(
            dart_api_key,
            keywords.split(),
            kakao_api_key=kakao_api_key,
            origin_address=origin,
        )
        results = run_scoring(config, candidates)
        upsert_scored_candidates(_results_db_path(), results)
    except Exception as exc:  # 화면에 에러 메시지를 그대로 보여주기 위함
        return RedirectResponse(url=f".?error={quote(str(exc))}", status_code=303)

    return RedirectResponse(url=".", status_code=303)


@app.post("/candidates/status")
def set_status(name: str = Form(...), status: str = Form(...)) -> RedirectResponse:
    update_contact_status(_results_db_path(), name, status)
    # "/candidates/status"는 경로가 두 단계 깊어서 루트로 가려면 ".."가 필요하다
    # (단독 실행이든 다른 앱에 mount되어 있든 상관없이 항상 앱의 "/"로 돌아간다).
    return RedirectResponse(url="..", status_code=303)
