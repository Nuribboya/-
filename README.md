# 수학 채점 & 해설 시스템

수학 문제(산술식, 일차방정식, 이차방정식, 식 정리)를 입력하면 정답을 계산하고,
학생 답안을 채점하며, 단계별 풀이 과정(해설)을 생성해 주는 시스템입니다.
`sympy` 기반 기호 계산으로 동작하며, 외부 API 없이 자체적으로 채점/해설을 만듭니다.

## 지원하는 문제 유형

| 유형 | 예시 |
| --- | --- |
| 산술식 | `3 + 4 * 2 - 1` |
| 일차방정식 | `2x + 3 = 11`, `3x + 2 = x + 10` |
| 이차방정식 | `x^2 - 5x + 6 = 0` |
| 식 정리(전개/동류항) | `3x + 5 - x + 2`, `2*(x + 3)` |

학생 답안은 `4`, `x = 4`, `x=2 또는 x=3`, `2, 3` 처럼 자유롭게 입력할 수 있습니다.

## 설치

```bash
pip install -r requirements.txt
```

## 사용법

### 웹으로 실행

```bash
uvicorn web.main:app --reload
```

브라우저에서 `http://localhost:8000` 접속 후 문제와 학생 답안을 입력하면
정답 여부와 단계별 풀이가 표시됩니다.

### API로 사용

```bash
curl -X POST http://localhost:8000/api/grade \
  -H "Content-Type: application/json" \
  -d '{"problem": "2x + 3 = 11", "student_answer": "x = 4"}'
```

### CLI로 사용

```bash
python -m mathgrader.cli "2x + 3 = 11" "x = 4"
```

## 테스트

```bash
pytest
```

## 구조

- `mathgrader/classify.py` — 문제 유형 분류 (산술식 / 일차방정식 / 이차방정식 / 식 정리 / 기타)
- `mathgrader/solvers/` — 유형별 단계별 풀이 로직
- `mathgrader/parsing.py` — 수식/답안 문자열 파싱, 답안 비교
- `mathgrader/grader.py` — 분류 → 풀이 → 채점을 총괄하는 진입점
- `web/` — FastAPI 기반 웹 UI 및 API
