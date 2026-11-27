# minecraft-ai

마인크래프트(자바 에디션)를 **스스로 플레이하고 배우는 AI 봇**. 두뇌는 Claude API 또는 로컬 Ollama 모델로 바꿔 끼울 수 있고, 방송용 오버레이(생각 자막·스탯·새 스킬 알림)가 들어 있습니다.

## 동작 방식
1. **관찰**: 위치/체력/인벤토리/주변 블록·몹을 텍스트로 요약
2. **판단 (fast 모델)**: JSON으로 다음 행동 선택 — 기존 스킬 실행 / 새 스킬 작성 / 대기
3. **스킬 작성 (smart 모델)**: 필요한 스킬을 JS 코드로 작성 → 실행 → 에러를 피드백받아 최대 3회 수정 → 성공하면 `data/skills/`에 저장
4. **자동 커리큘럼**: 나무 → 작업대 → 나무곡괭이 → 돌곡괭이 → 화로 → 철 → 철곡괭이 → 다이아
5. **교훈 기록**: 죽음/스킬 작성 실패 원인을 `data/memory.json`에 저장해 프롬프트에 포함

모델 가중치는 바뀌지 않습니다. 학습 = 스킬 라이브러리 + 교훈 메모.

## 실행
```bash
cd minecraft-ai
npm install
cp .env.example .env   # 값 채우기 (ANTHROPIC_API_KEY 등)
set -a; source .env; set +a
npm start
```
- 마인크래프트에서 싱글플레이 월드를 열고 "LAN으로 열기" 후 표시되는 포트를 `MC_PORT`에 넣거나, 전용 서버(`online-mode=false` 권장)를 사용하세요.
- 방송: OBS → 소스 추가 → 브라우저 → `http://localhost:3000` (배경 투명).

## 무료 모드 (API 키 없이)
```bash
# 1) Ollama 설치: https://ollama.com  → 터미널에서 모델 받기
ollama pull qwen2.5:7b
ollama pull qwen2.5-coder:7b
# 2) 실행 (Node 20.6+)
cd minecraft-ai && npm install && npm run start:free
```
설정은 `.env.free`에 있습니다. GPU(VRAM 8GB 이상)가 있으면 쾌적하고, CPU만 쓰면 판단이 느립니다. 모델이 작아서 코드 작성 실패가 잦을 수 있으니, 성능이 부족하면 `SMART_MODEL=qwen2.5-coder:14b`처럼 키우거나 smart만 Claude로 바꾸세요.

## 비용 / 유료 모드
- 기본: fast=`claude-haiku-5-5`, smart=`claude-sonnet-5-5`. 24시간 기준 대략 하루 $12~15 (추정).
- `DAILY_BUDGET_USD`를 넘으면 봇이 자동으로 쉽니다 (다음 날 초기화).
- 무료: `FAST_PROVIDER=ollama` (+ 필요하면 `SMART_PROVIDER=ollama`). 로컬 모델은 코드 작성 품질이 낮을 수 있어, fast만 로컬로 두는 하이브리드를 추천합니다.

## 안전 주의
모델이 쓴 코드를 `node:vm`에서 실행합니다. 위험 식별자(`process`, `require`, `eval` 등)를 막고 `api` 객체만 노출하지만, **vm은 완전한 보안 경계가 아닙니다.** 전용 PC/컨테이너에서 돌리고, 봇 전용 서버를 쓰세요.

## 구조
```
src/index.js      접속·재접속
src/agent.js      관찰→판단→실행 루프, 스킬 작성/수정
src/actions.js    스킬 코드가 쓰는 api (gather/craft/smelt/attack/...)
src/sandbox.js    생성 코드 실행(금지어 검사 + 타임아웃)
src/skills.js     스킬 라이브러리 (data/skills)
src/curriculum.js 단계별 목표
src/llm.js        Claude / Ollama 어댑터 + 예산 가드
src/overlay.js    방송 오버레이 서버 (overlay/index.html)
```

## 아직 검증 안 된 부분
단위 테스트(`npm test`)는 통과하지만, 실제 마인크래프트 서버와의 연동(채집·제작·화로·전투)은 이 개발 환경에서 직접 돌려보지 못했습니다. 첫 실행에서 `api.*` 쪽 버그가 나올 수 있고, 에러는 로그와 오버레이에 표시됩니다.
