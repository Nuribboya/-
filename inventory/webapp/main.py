"""브라우저에서 재고 입출고를 기록하고 현재 재고를 조회하는 로컬 웹 앱.

실행:
    uvicorn inventory.webapp.main:app --reload --port 8001

브라우저에서 http://localhost:8001 접속 (leadradar 웹앱과 포트가 겹치지
않게 8001을 권장). INVENTORY_DB 환경변수로 DB 파일 경로를 바꿀 수 있다
(기본: inventory.db, 이 파일 하나에 모든 기록이 쌓인다).
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..db import DEFAULT_DB_PATH, current_stock, init_db, recent_movements, record_movement

app = FastAPI()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _db_path() -> str:
    """요청마다 현재 INVENTORY_DB 값을 다시 읽고, 필요하면 스키마를 만든다.

    init_db는 CREATE TABLE IF NOT EXISTS라 여러 번 호출해도 안전하다. 앱
    시작 시점에 한 번만 초기화하면 테스트에서 요청마다 DB 경로를 바꿔가며
    검증하기 어려워, 매 요청마다 확인하는 쪽을 택했다.
    """
    path = os.environ.get("INVENTORY_DB", DEFAULT_DB_PATH)
    init_db(path)
    return path


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "stock": current_stock(_db_path()),
            "movements": recent_movements(_db_path()),
        },
    )


@app.post("/movements")
def create_movement(
    item_name: str = Form(...),
    direction: str = Form(...),
    quantity: int = Form(...),
    unit: str = Form("개"),
    memo: str = Form(""),
) -> RedirectResponse:
    signed_qty = quantity if direction == "in" else -quantity
    record_movement(_db_path(), item_name.strip(), signed_qty, memo=memo.strip(), unit=unit.strip() or "개")
    return RedirectResponse(url=".", status_code=303)
