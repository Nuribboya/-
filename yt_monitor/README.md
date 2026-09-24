# YouTube 채널 성과 모니터 + 로컬 LLM 주제/대본 생성

조회수 체크 → 성장 둔화 감지 → 다음 영상 주제/제목/대본 자동 생성 → 텔레그램 알림까지
자동으로 처리하는 프로그램입니다. 무료 도구만 사용합니다:
YouTube Data API 무료 쿼터, Telegram Bot API, **로컬 Ollama**(qwen2.5:7b). 유료 LLM API는 쓰지 않습니다.
Windows용 **단일 exe**(`YouTubeMonitor.exe`)로 배포할 수 있습니다.

```
YouTube Data API ─▶ SQLite 시계열 ─▶ 하락 감지 ─┬─▶ 리포트 (reports/날짜/)
                                              └─▶ Ollama 주제·제목·대본 생성 (outputs/날짜/채널명.md)
                                                    └─▶ 텔레그램 알림 (요약 + 파일 첨부)
```

## 0. 먼저 확인할 것

| 항목 | 확인 방법 |
| --- | --- |
| Ollama 설치 | <https://ollama.com/download>에서 설치 → 명령 프롬프트에서 `ollama --version` |
| qwen2.5:7b 모델 | `ollama pull qwen2.5:7b` (약 4.7GB) → `ollama list`에 보이면 완료 |
| YouTube API 키 | 아래 발급 방법 참고 |
| 채널 | `@핸들`, `UC…` 채널 ID, 채널 주소 중 아무거나 |
| 텔레그램 봇 토큰 / chat_id | 아래 발급 방법 참고 (선택. 없으면 알림 없이 동작) |

- Ollama는 설치하면 백그라운드에서 서버(`localhost:11434`)가 자동으로 뜹니다.
  프로그램은 서버가 켜져 있는지와 모델이 있는지만 확인하며, **모델이 없으면
  `ollama pull qwen2.5:7b` 먼저 실행하라는 안내를 띄우고 종료**합니다.
- 권장 사양: 메모리 8GB 이상. GPU가 없으면 대본 1편 생성에 수 분이 걸릴 수 있습니다.

### YouTube Data API 키 발급
1. <https://console.cloud.google.com/>에서 새 프로젝트를 만듭니다.
2. **API 및 서비스 → 라이브러리**에서 `YouTube Data API v3`를 검색해 **사용**을 누릅니다.
3. **사용자 인증 정보 → 사용자 인증 정보 만들기 → API 키**를 누르면 키가 만들어집니다.
   (권장) 키 제한을 `YouTube Data API v3`로 설정하세요.
4. 무료 쿼터는 하루 10,000 unit입니다. 채널 1개를 1회 체크하는 데 약 3 unit이 듭니다.

### 텔레그램 봇 만들기
1. 텔레그램에서 **@BotFather**에게 `/newbot`을 보내고, 이름과 `…_bot` 형식의 username을 정하면 **토큰**이 발급됩니다.
2. 만든 봇과 대화를 열고 `/start`를 보냅니다.
3. 프로그램 설정 창에서 토큰을 넣고 **[chat_id 찾기]**를 누르면 chat_id가 자동으로 채워집니다.

## 1. exe로 사용하기 (Windows)

### exe 받기
- **GitHub Actions**: 이 저장소의 **Actions → yt-monitor-exe → 최근 실행 → Artifacts**에서
  `YouTubeMonitor-windows`를 받아 압축을 풉니다. `yt_monitor/`가 바뀔 때마다 자동으로 빌드됩니다.
- **직접 빌드**: 아래 "exe 직접 빌드"를 참고하세요.

### 실행
1. `YouTubeMonitor.exe`를 원하는 폴더(예: `C:\YouTubeMonitor\`)에 두고 더블클릭합니다.
2. 첫 실행이면 **설정 창**이 뜹니다. API 키, 채널, (선택) 텔레그램 정보, 체크 주기, Ollama 모델, 하락 임계값을
   입력하고 저장하면 exe 옆에 `config.yaml`이 만들어집니다.
3. 이후 exe 옆에 다음 파일과 폴더가 생깁니다.

```
YouTubeMonitor.exe
config.yaml        ← 설정 (API 키/토큰 포함 — 공유 금지)
prompts/           ← 프롬프트 템플릿. 메모장으로 고치면 다음 생성부터 반영
data/              ← SQLite DB (조회수 시계열)
reports/           ← 체크할 때마다 저장되는 채널 리포트
outputs/           ← 생성한 주제/대본 (날짜/채널명.md, 채널명_대본.txt)
logs/              ← 실행 로그
```

> Windows SmartScreen이 "알 수 없는 게시자" 경고를 띄우면 **추가 정보 → 실행**을 누르세요.
> 서명되지 않은 exe라서 나오는 경고입니다.

### 화면 구성
| 영역 | 기능 |
| --- | --- |
| **▶ 지금 체크하기** | YouTube에서 통계 수집 → 분석 → 리포트 저장. 둔화가 감지되면 주제/대본 자동 생성 + 텔레그램 알림 |
| **✨ 새 주제/대본 생성** | 선택한 채널의 주제/대본을 바로 생성 (Ollama 호출, 글자가 생성되는 대로 표시, 취소 가능) |
| **자동 체크** 토글 | 켜면 설정한 주기마다 백그라운드로 체크 (APScheduler). 상태는 다음 실행에도 유지 |
| 채널 목록 | 상태, 구독자, 최근/이전 변화율, 최근 7일 조회수, 14일 추이(스파크라인), 마지막 체크 |
| **조회수 추이** 탭 | 선택한 채널의 지표, 최근 영상 성과, 상위 성과 영상, 패턴 |
| **주제/대본** 탭 | 생성 결과(수정 가능). **주제 선택 → [선택한 주제로 대본 생성]**, **[💾 파일로 저장]** |
| ⚙ 설정 / 📂 출력 폴더 | 설정 변경, outputs 폴더 열기 |

창을 닫으면 자동 체크도 멈춥니다. 창 없이 계속 돌리고 싶다면 Windows **작업 스케줄러**에
`YouTubeMonitor.exe --check-now`를 등록하세요 (결과는 `logs/yt_monitor.log`에 남습니다).

## 2. 생성 결과 형식

`outputs/2026-09-24/채널명.md` (같은 날 다시 만들면 `채널명_HHMMSS.md`)
- **주제 후보** 3~5개: 추천 이유, 주제마다 제목 후보 2~3개, ⭐ 모델 추천 주제 표시
- **대본**: 추천 주제(또는 GUI에서 고른 주제)로 작성하며, **한 줄에 한 문장**입니다.
  마크다운, 번호, 괄호 지시문(`[효과음]`, `(화면 전환)`), 이모지, `내레이션:` 같은 표시는 자동으로 지워서
  TTS에 바로 넣을 수 있습니다. 같은 내용이 `채널명_대본.txt`로도 저장됩니다.
- 생성에 사용한 채널 데이터 요약

**텔레그램 알림** (둔화 감지 시): 하락 지표와 최근 영상 요약 → 생성된 주제·제목 목록과 대본 앞부분 →
`.md`/`_대본.txt`/리포트 파일 첨부. Ollama가 꺼져 있어 생성에 실패하면, 실패 사유와 함께
Claude 채팅에 붙여넣을 수 있는 요약 블록을 대신 보냅니다.

### 프롬프트 수정
`prompts/topics.txt`(주제·제목 생성)와 `prompts/script_gen.txt`(대본 생성)를 메모장으로 고치면 됩니다.
`{channel_data}`, `{topic}`, `{title}`, `{reason}`, `{num_topics}`, `{num_titles}`, `{script_minutes}`,
`{script_chars}`, `{channel_name}` 자리표시자만 값으로 바뀌고, 나머지 중괄호(JSON 예시 등)는 그대로 남습니다.
원래대로 되돌리려면 파일을 지우세요. 다음 실행 때 기본본이 다시 복사됩니다.

## 3. 성장 둔화 판단

| 설정 (`analysis`) | 기본값 | 의미 |
| --- | --- | --- |
| `metric` | `views_per_day` | 영상 지표. 누적 조회수 ÷ 게시 후 경과일. `views_at_age`는 게시 후 `age_days`일 시점 조회수 (시계열이 쌓인 뒤 사용) |
| `recent_n` / `baseline_m` | 5 / 10 | 최근 N개 영상과 그 이전 M개 영상을 비교 |
| `drop_threshold_pct` | 30 | 최근 N개 평균이 이전 M개보다 X% 이상 낮으면 **둔화** (설정 창의 '하락 임계값') |
| `aggregate` | `mean` | `median`으로 바꾸면 대박 영상 하나에 덜 흔들림 |
| `weekly_drop_threshold_pct` | 없음 | 채널 주간 조회수 증가량이 전주보다 X% 이상 줄어도 둔화로 판단 |
| `alert_cooldown_hours` | 24 | 같은 채널은 이 시간 동안 다시 생성·알림하지 않음 (하락폭이 10%p 이상 커지면 예외) |

채널별로 다른 임계값을 쓰려면 `config.yaml`의 채널 항목에 `analysis:`를 추가하세요.

## 4. 소스로 실행 / 단계별 단독 테스트

```bash
cd yt_monitor
pip install -r requirements.txt          # Python 3.11+
cp .env.example .env                     # 또는 config.yaml / 설정 창에 직접 입력

python main.py                           # GUI
python main.py --check-now               # 1회 체크 (둔화 시 생성 + 알림)
python main.py --check-now --dry-run --no-generate
python main.py --generate @내채널         # 수동 생성
python main.py --schedule                # 창 없이 주기 실행
```

각 단계는 따로 실행해 볼 수 있습니다 (저장소 루트에서 실행):

| 단계 | 명령 | 확인 내용 |
| --- | --- | --- |
| 1) 설정 | `python -m yt_monitor.config [config.yaml]` | 채널, 키(가려서 표시), Ollama, 주기 |
| 1) DB | `python -m yt_monitor.db [db경로]` | 스키마 생성 + 테이블별 행 수 |
| 2) 수집 | `python -m yt_monitor.collector @핸들 [--save]` | 채널 통계 + 최근 영상 10개 |
| 3) Ollama | `python -m yt_monitor.ollama_client --check` / `--prompt "안녕"` | 서버/모델 확인, 스트리밍 응답 |
| 3) 생성 | `python -m yt_monitor.generator --demo` | 예시 채널 데이터로 주제·대본 생성 → outputs/ |
| 4) 감지+알림 | `python main.py --check-now` / `--test-telegram` | 둔화 판단, 자동 생성, 텔레그램 |
| 5) GUI | `python -m yt_monitor.gui` | 창 실행 |
| 6) 빌드 | `python yt_monitor/build_exe.py` | `yt_monitor/dist/YouTubeMonitor(.exe)` |

### exe 직접 빌드 (Windows)
```bat
pip install -r yt_monitor\requirements.txt pyinstaller
python yt_monitor\build_exe.py            REM 창 모드 exe
python yt_monitor\build_exe.py --console  REM 문제를 확인할 때: 콘솔 창이 같이 뜨는 exe
```
PyInstaller는 크로스 컴파일을 하지 않으므로 **Windows exe는 Windows에서** 빌드해야 합니다.
macOS나 Linux에서 같은 명령을 실행하면 해당 OS용 실행 파일이 만들어집니다.

### 테스트
YouTube, Ollama, 텔레그램은 가짜 서버와 객체로 대체되므로 키나 모델이 없어도 됩니다.
```bash
pytest tests/test_yt_monitor.py tests/test_yt_monitor_generation.py tests/test_yt_monitor_gui.py
# 리눅스 서버처럼 화면이 없으면: xvfb-run -a pytest ...
```

## DB 스키마 (SQLite)

| 테이블 | 내용 |
| --- | --- |
| `channels` / `channel_stats` | 채널 정보 / 수집 시각별 구독자·총 조회수·영상 수 |
| `videos` / `video_stats` | 영상 정보(제목, 게시일, 카테고리, 길이, 태그) / 수집 시각별 조회수·좋아요·댓글 |
| `categories` | 카테고리 이름 캐시 |
| `alerts` | 알림 및 자동 생성 이력 (쿨다운 판단) |
| `generations` | 생성한 주제 후보(JSON), 선택 주제, 제목, 대본, 저장 경로, 모델, 자동/수동 여부 |
