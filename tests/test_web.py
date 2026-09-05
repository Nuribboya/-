from fastapi.testclient import TestClient

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
