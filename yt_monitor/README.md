# YouTube 채널 성과 모니터 & 성장 둔화 알림

여러 YouTube 채널의 영상별 조회수 추이를 SQLite에 쌓고, 최근 영상의 성과가 이전 영상보다
눈에 띄게 떨어지면 텔레그램으로 알려주는 시스템입니다. 무료 API만 사용합니다
(YouTube Data API v3 무료 쿼터 + Telegram Bot API). 유료 LLM API는 호출하지 않습니다.
알림과 리포트에 들어 있는 **"Claude에 붙여넣기용" 블록**을 복사해 Claude 채팅에 붙여넣으면
다음 주제와 대본을 바로 물어볼 수 있습니다.

```
yt_monitor/
├── main.py         # CLI 진입점
├── config.yaml     # 채널 목록 · 임계값 · 스케줄 (비밀값 없음)
├── .env.example    # API 키 · 봇 토큰 템플릿 → .env 로 복사
├── db.py           # 1) SQLite 스키마 / 저장소
├── collector.py    # 2) YouTube Data API 수집
├── analyzer.py     # 3) 성장세 분석 / 둔화 판단
├── notifier.py     #    텔레그램 알림
├── report.py       # 4) 마크다운 리포트 + Claude 프롬프트
├── pipeline.py     #    수집→분석→리포트→알림 1회 실행
└── scheduler.py    #    APScheduler cron 실행
```

## 0. 준비물 발급 (처음 한 번)

### YouTube Data API 키
1. <https://console.cloud.google.com/> 접속 → 상단에서 **새 프로젝트** 생성
2. **API 및 서비스 → 라이브러리**에서 `YouTube Data API v3` 검색 → **사용**
3. **API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → API 키**
4. (권장) 생성된 키의 **API 제한사항**에서 `YouTube Data API v3`만 허용
5. 무료 쿼터는 하루 10,000 unit입니다. 이 시스템은 채널 1개를 1회 수집할 때 약 3 unit을 씁니다.
   6시간마다 10개 채널을 수집해도 하루 약 120 unit입니다.

### 채널 ID / 핸들
- 가장 쉬운 방법은 채널 주소의 `@핸들`을 그대로 쓰는 것입니다 (예: `@mychannel`).
- 채널 ID(`UC`로 시작)를 쓰려면 YouTube Studio → **설정 → 채널 → 고급 설정**에서 확인합니다.

### 텔레그램 봇 토큰 & 챗 ID
1. 텔레그램에서 **@BotFather** 검색 → `/newbot` 입력
2. 봇 이름과 username(`..._bot`으로 끝나야 함)을 정하면 **토큰**(`123456:ABC-...`)을 발급해 줍니다.
3. 만든 봇과 대화를 열고 아무 메시지(예: `/start`)나 보냅니다.
4. `.env`에 토큰을 넣은 뒤 `python main.py --get-chat-id`를 실행하면 `chat_id`가 출력됩니다.
   그룹 채팅으로 받으려면 봇을 그룹에 초대하고, 그룹에서 메시지를 보낸 뒤 실행하세요.
5. `python main.py --test-telegram`으로 연결을 확인합니다.

## 1. 설치

```bash
cd yt_monitor
python -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -r requirements.txt
cp .env.example .env          # 값 채우기 (.env는 git에 올라가지 않습니다)
```

`.env`
```
YOUTUBE_API_KEY=AIza...
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=987654321
```

`config.yaml`의 `channels`에 내 채널을 등록합니다.
```yaml
channels:
  - handle: "@mychannel"
    name: "메인 채널"
  - id: UCxxxxxxxxxxxxxxxxxxxxxx
    name: "서브 채널"
    analysis:
      drop_threshold_pct: 40     # 채널별 임계값 덮어쓰기
```

## 2. 실행

```bash
python main.py --check-now            # 지금 수집 + 분석 + 리포트 저장 + (둔화 시) 알림
python main.py --check-now --dry-run  # 텔레그램 전송 없이 실행
python main.py --analyze-only         # API 호출 없이 DB 데이터로만 재분석
python main.py --schedule             # APScheduler: config.yaml의 cron 주기로 계속 실행
```

### 백그라운드로 계속 돌리기
- 간단한 방법: `nohup python main.py --schedule > monitor.log 2>&1 &`
- APScheduler 대신 시스템 cron을 써도 됩니다: `0 */6 * * * cd /path/to/yt_monitor && .venv/bin/python main.py --check-now`

## 3. 분석 방식

**영상별 지표 ("게시 후 경과일 대비 조회수")**: `analysis.metric`으로 고릅니다.

| metric | 계산 | 특징 |
| --- | --- | --- |
| `views_per_day` (기본) | 누적 조회수 ÷ 게시 후 경과일 | 첫 수집부터 바로 사용 가능 |
| `views_at_age` | 게시 후 `age_days`일 시점의 조회수 (시계열 선형 보간) | 영상 나이가 달라서 생기는 편향이 없음. 수집을 시작한 뒤 올라온 영상부터 쓸 수 있으며, 데이터가 부족하면 `views_per_day`로 자동 대체 |

**성장 둔화 판단**
1. 게시 후 `min_age_days`가 지난 영상을 최신순으로 정렬합니다.
2. 최근 `recent_n`개의 평균(`aggregate: median`이면 중앙값)과 그 이전 `baseline_m`개의 평균을 비교합니다.
3. `(이전 − 최근) / 이전 × 100 ≥ drop_threshold_pct`이면 **성장 둔화**로 판단합니다.
4. (선택) `weekly_drop_threshold_pct`를 설정하면, 채널 전체 조회수의 최근 7일 증가량이 전주보다 그만큼 줄었을 때도 알림을 보냅니다 (14일 이상 수집 필요).

**알림 중복 방지**: 같은 채널은 `alert_cooldown_hours`(기본 24시간) 동안 다시 알리지 않습니다.
단, 하락폭이 직전 알림보다 10%p 이상 커지면 바로 다시 알립니다.

**상위 성과 패턴**: 지표 기준 상위 `top_k`개 영상의 반복 키워드, 카테고리, 숏폼 비율과 평균 길이
(채널 전체와 비교), 게시 요일과 시간, 좋아요율을 정리합니다.

## 4. 결과물

**텔레그램 알림** (둔화 감지 시)
1. 요약: 채널명, 하락 지표, 최근 영상 성과, 상위 영상 Top K와 패턴
2. Claude 프롬프트: `<pre>` 블록이라 텔레그램에서 탭하면 바로 복사됩니다.
3. 마크다운 리포트 파일 첨부 (`send_report_file: true`)

수집이 실패하면(쿼터 초과 등) 오류 메시지도 보냅니다.

**리포트 아카이브**
- `reports/YYYY-MM-DD/HHMM_<채널>.md`: 수집할 때마다 저장
- `reports/latest/<채널>.md`: 항상 최신본

리포트 맨 아래 블록 예시:
```text
채널: 메인 채널
분석 시각: 2026-09-24 18:00 KST
상태: 성장 둔화 감지
구독자: 12,300명
최근 5개 영상 평균 조회수 증가율: -42.0% (이전 10개 대비, 지표: 일평균 조회수 ...)
최근 7일 채널 조회수 증가량: 8,400회 (전주 대비 -18.3%)

[최근 영상 성과 (최신순 5개)]
- ...
[상위 성과 영상 Top 3]
1. ...
[상위 영상 패턴]
- 반복 키워드: ...

요청:
1. 위 데이터로 볼 때 조회수 흐름이 바뀐 원인을 가설 3가지로 정리해줘.
2. 상위 성과 영상의 패턴을 살려서 다음 영상 주제 5개를 추천해줘. ...
3. 그중 가장 유망한 주제 1개로 대본 초안을 써줘. ...
```
`요청:` 부분은 `config.yaml`의 `report.claude_request`에서 원하는 문구로 바꿀 수 있습니다.

## DB 스키마 (SQLite, `data/yt_monitor.db`)

| 테이블 | 내용 |
| --- | --- |
| `channels` | 채널 ID, 이름, 핸들, 업로드 재생목록 ID |
| `channel_stats` | 수집 시각별 구독자 · 총 조회수 · 영상 수 |
| `videos` | 영상 ID, 제목, 게시일, 카테고리, 길이(초), 태그 |
| `video_stats` | 수집 시각별 조회수 · 좋아요 · 댓글 (영상별 시계열) |
| `categories` | 카테고리 ID → 이름 캐시 |
| `alerts` | 보낸 알림 이력 (쿨다운 판단용) |

## 테스트

저장소 루트에서 실행합니다. YouTube와 텔레그램은 가짜 객체로 대체하므로 키가 없어도 됩니다.
```bash
pytest tests/test_yt_monitor.py
```
