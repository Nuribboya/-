"""leadradar(원청 후보 관리)와 inventory(재고 관리)를 하나의 사이트에서 쓸 수
있게 묶은 대시보드.

실행:
    uvicorn dashboard.main:app --reload

브라우저에서 http://localhost:8000 접속. 필요한 환경변수(ANTHROPIC_API_KEY,
DART_API_KEY 등)는 각 앱이 요구하는 것과 동일하게 미리 설정해야 한다 - 자세한
목록은 README의 leadradar/inventory 섹션을 참고.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from inventory.webapp.main import app as inventory_app
from leadradar.webapp.main import app as leadradar_app

app = FastAPI()
app.mount("/leads", leadradar_app)
app.mount("/inventory", inventory_app)

_HOME_HTML = """\
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <title>관리 시스템</title>
  <style>
    body { font-family: sans-serif; max-width: 700px; margin: 80px auto; padding: 0 16px; }
    a.card {
      display: block; padding: 24px; margin-top: 16px; border: 1px solid #ccc;
      border-radius: 8px; text-decoration: none; color: inherit;
    }
    a.card:hover { background: #f5f5f5; }
    a.card h2 { margin: 0 0 8px; }
    a.card p { margin: 0; color: #555; }
  </style>
</head>
<body>
  <h1>관리 시스템</h1>
  <a class="card" href="/leads/">
    <h2>원청 레이더</h2>
    <p>신규 원청 후보 발굴 + 적합도 채점 + 컨택 상태 관리</p>
  </a>
  <a class="card" href="/inventory/">
    <h2>재고 관리</h2>
    <p>입출고 기록 + 현재 재고 조회</p>
  </a>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return _HOME_HTML
