"""수학 채점 시스템 + 전공 적성검사 웹 UI + API."""
from dataclasses import asdict
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from aptitude import LIKERT_LABELS, QUESTIONS, TRAIT_ORDER, TRAITS, score_answers
from mathgrader.grader import grade

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="수학 채점 & 해설 시스템")


class GradeRequest(BaseModel):
    problem: str
    student_answer: str


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"result": None, "error": None, "problem": "", "student_answer": ""}
    )


@app.post("/grade", response_class=HTMLResponse)
async def grade_form(request: Request, problem: str = Form(...), student_answer: str = Form(...)):
    result = None
    error = None
    try:
        result = grade(problem, student_answer)
    except ValueError as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "result": result,
            "error": error,
            "problem": problem,
            "student_answer": student_answer,
        },
    )


@app.post("/api/grade")
async def api_grade(payload: GradeRequest):
    try:
        result = grade(payload.problem, payload.student_answer)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return asdict(result)


def _aptitude_context(*, result=None, error=None, answers=None) -> dict:
    return {
        "questions": QUESTIONS,
        "likert_labels": LIKERT_LABELS,
        "trait_order": TRAIT_ORDER,
        "traits": TRAITS,
        "result": result,
        "error": error,
        "answers": answers or {},
    }


@app.get("/aptitude", response_class=HTMLResponse)
async def aptitude_index(request: Request):
    return templates.TemplateResponse(request, "aptitude.html", _aptitude_context())


@app.post("/aptitude", response_class=HTMLResponse)
async def aptitude_submit(request: Request):
    form = await request.form()
    answers: Dict[str, int] = {}
    result = None
    error = None
    try:
        for q in QUESTIONS:
            raw = form.get(q.id)
            if raw is None:
                raise ValueError(f"{q.text!r} 문항에 응답해 주세요.")
            try:
                answers[q.id] = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"'{q.id}' 응답 값이 올바르지 않습니다.") from exc
        result = score_answers(answers)
    except ValueError as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request, "aptitude.html", _aptitude_context(result=result, error=error, answers=answers)
    )


class AptitudeRequest(BaseModel):
    answers: Dict[str, int]


@app.post("/api/aptitude")
async def api_aptitude(payload: AptitudeRequest):
    try:
        result = score_answers(payload.answers)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return {
        "trait_scores": result.trait_scores,
        "holland_code": result.holland_code,
        "top_recommendations": [asdict(rec) for rec in result.top_recommendations],
    }
