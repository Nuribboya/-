"""수학 채점 시스템 웹 UI + API."""
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

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
