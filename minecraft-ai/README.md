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

## 일지(저널)
`data/journal.jsonl`에 시간순으로 기록합니다: 죽음(원인·위치·낮/밤·직전 행동), 큰 피해(-3 이상, 추정 원인), 스킬 성공/실패(에러 포함), 새 스킬, 목표 달성. 최근 기록과 죽음 통계는 매 판단 프롬프트에 들어가서, 같은 실수를 피하는 데 쓰입니다.
```bash
npm run report   # 죽음/마일스톤/새 스킬 요약 출력
```

## 반성(self-improvement) 루프
`REFLECT_EVERY`(기본 25) 판단마다 쌓인 실패를 모아 분석합니다.
1. 최근 3번 이상 실패한 스킬은 에러 메시지와 현재 코드를 함께 smart 모델에 주고 **고쳐 쓰게** 합니다 (한 번에 최대 2개).
2. 일지와 기존 교훈을 **최대 8개의 짧은 교훈으로 다시 정리**해서 프롬프트에 반영합니다.
결과는 일지의 `reflect_fix`, `reflect` 항목으로 남습니다.

## 게임 지식 (자동 계획)
프롬프트에 위키를 통째로 넣지 않고, 게임 데이터(minecraft-data)에서 필요할 때 계산합니다.
- 매 판단마다 현재 목표까지의 **단계별 계획**과 **추천 다음 행동**(기본 스킬 호출)을 넣어줍니다. 예: 빈손 → 나무 곡괭이 = 원목 3 → 판자 → 막대기 → 판자 → 작업대 → 설치 → 곡괭이.
- 스킬 코드에서 쓸 수 있는 조회 함수: `api.recipe(item)`, `api.toolFor(block)`, `api.plan(item, count)`.
- 기본 스킬 추가: `mine_block {block, count}`, `smelt_item {item, count, fuel}`.
- 굽기 레시피와 광석 위치처럼 데이터에 없는 것은 `src/knowledge.js`에 직접 적어두었습니다.

## 로그
터미널은 가볍게 유지합니다: 긴 줄은 200자에서 자르고(`LOG_MAX_CHARS`), 같은 메시지는 "N번 반복"으로 묶고, 300줄마다 화면을 지웁니다(`CLEAR_EVERY`, 0이면 안 지움). 잘리지 않은 전체 기록은 `data/bot.log`에 남습니다.

## 헛돌기 방지
스킬 작성이 2번 연속(각 3회 시도) 실패하면 `WRITE_COOLDOWN_STEPS`(6)턴 동안 스킬 작성을 막고 기존 스킬만 쓰게 합니다.

## 성공 판정
스킬이 에러 없이 끝나도 **인벤토리·위치(3칸 이상)·허기·주변 적대몹 수**가 하나도 안 바뀌면 "효과 없음"(실패)으로 칩니다. 새 스킬도 실제 효과가 있어야 저장됩니다.

## 두뇌 저장 / 정리
- **자동 저장**: 마일스톤 달성 때(`ms-<id>`, 영구 보관)와 `SNAPSHOT_EVERY_MIN`(기본 60분)마다(`auto`, 최근 10개만 유지) `brains/`에 `data/` 전체를 복사합니다.
- **정리**: 반성 4번마다(`CONSOLIDATE_EVERY`) 또는 배운 스킬이 `MAX_SKILLS`(25)를 넘으면, 먼저 저장한 뒤 한 번도 성공 못 한 스킬·5연속 실패 스킬·점수 낮은 스킬을 `data/skills/archive/`로 옮깁니다 (삭제 아님). 기본 스킬은 건드리지 않습니다.
```bash
npm run brain                     # 저장된 두뇌 목록
npm run brain -- save 이름         # 지금 상태 수동 저장 (영구 보관)
npm run brain -- restore 두뇌이름   # 봇을 끈 상태에서 복원 (직전 상태는 자동 백업)
```

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
src/actions.js    스킬 코드가 쓰는 api (gather/craft/smelt/attack/placeAt/digAt/equip/block/nearbyMobs/...)
src/sandbox.js    생성 코드 실행(금지어 검사 + 타임아웃)
src/skills.js     스킬 라이브러리 (data/skills)
src/curriculum.js 단계별 목표
src/llm.js        Claude / Ollama 어댑터 + 예산 가드
src/overlay.js    방송 오버레이 서버 (overlay/index.html)
```

## 아직 검증 안 된 부분
단위 테스트(`npm test`)는 통과하지만, 실제 마인크래프트 서버와의 연동(채집·제작·화로·전투)은 이 개발 환경에서 직접 돌려보지 못했습니다. 첫 실행에서 `api.*` 쪽 버그가 나올 수 있고, 에러는 로그와 오버레이에 표시됩니다.
