from fastapi.testclient import TestClient

from aptitude import QUESTIONS
from web.main import app

client = TestClient(app)


def test_index_page():
    r = client.get("/")
    assert r.status_code == 200
    assert "수학 채점" in r.text


def test_grade_form_correct():
    r = client.post("/grade", data={"problem": "2x + 3 = 11", "student_answer": "x = 4"})
    assert r.status_code == 200
    assert "정답입니다" in r.text


def test_api_grade():
    r = client.post("/api/grade", json={"problem": "2x + 3 = 11", "student_answer": "x = 4"})
    assert r.status_code == 200
    body = r.json()
    assert body["is_correct"] is True


def test_api_grade_bad_problem():
    r = client.post("/api/grade", json={"problem": "", "student_answer": "4"})
    assert r.status_code == 400


def test_aptitude_index_page():
    r = client.get("/aptitude")
    assert r.status_code == 200
    assert "전공 적성검사" in r.text
    assert QUESTIONS[0].text in r.text


def test_aptitude_submit_form():
    data = {q.id: "3" for q in QUESTIONS}
    r = client.post("/aptitude", data=data)
    assert r.status_code == 200
    assert "홀랜드 코드" in r.text
    assert "추천 전공" in r.text


def test_aptitude_submit_form_missing_answer_shows_error():
    data = {q.id: "3" for q in QUESTIONS[1:]}
    r = client.post("/aptitude", data=data)
    assert r.status_code == 200
    assert "응답해 주세요" in r.text


def test_api_aptitude():
    answers = {q.id: 3 for q in QUESTIONS}
    r = client.post("/api/aptitude", json={"answers": answers})
    assert r.status_code == 200
    body = r.json()
    assert len(body["holland_code"]) == 2
    assert len(body["top_recommendations"]) == 5
    assert all(score == 50.0 for score in body["trait_scores"].values())


def test_api_aptitude_missing_answers():
    r = client.post("/api/aptitude", json={"answers": {}})
    assert r.status_code == 400
