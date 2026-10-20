# 뉴스 → 유튜브 쇼츠 반자동 생성기

뉴스 피드(RSS)를 감시하다가 새 기사가 뜨면 **쇼츠 초안 영상을 자동으로 만들고**,
사람이 확인한 뒤 **승인 명령 한 번으로 유튜브에 올리는** 도구입니다.

```
새 기사 감지 → Claude가 대본·제목 작성 → AI 음성(TTS) → 1080x1920 영상 합성 → 초안 저장 (+텔레그램 알림)
                                                                              ↓ 사람이 확인
                                                                       approve → 유튜브 업로드
```

완전 자동 업로드를 일부러 넣지 않았습니다. 잘못된 내용이 그대로 나가는 일을 막고,
유튜브의 "대량 생산·반복 콘텐츠" 수익화 제외 판정을 피하려면 사람이 확인하는 단계가 필요합니다.

## 관리 앱 (가장 쉬운 사용법)

명령어 없이 버튼으로 쓰는 관리 화면입니다. 브라우저에서 열립니다.

- **Windows:** `newsshorts\start_app.bat` 더블클릭
- **macOS/Linux:** 터미널에서 `./newsshorts/start_app.sh`

처음 실행할 때 필요한 프로그램을 자동으로 설치하고, 브라우저에 `http://127.0.0.1:8765`가 열립니다.
(ffmpeg는 미리 설치해야 합니다. 아래 '설치' 참고)

| 화면 | 할 수 있는 것 |
| --- | --- |
| 상단 | 감시 시작/중지, 지금 바로 새 기사 확인, 자동 공개 상태와 오늘 공개 수 |
| 초안 | 영상 미리보기, 제목·설명 고치기, 승인하고 업로드, 거절, 유튜브에서 내리기, 사실 확인 결과와 자동 공개 안 된 이유 |
| 기록 | 감시 중 무슨 일이 있었는지 실시간 기록 |
| 설정 | 시청자층, 목소리, 자동 공개 조건, Claude API 키, 텔레그램, 뉴스 소스 추가·삭제 |

**휴대폰으로 보기:** `start_app.bat --lan` 또는 `./newsshorts/start_app.sh --lan`으로 켜면
같은 와이파이의 휴대폰에서 `http://PC의IP주소:8765`로 접속할 수 있습니다. PC 화면에 나오는 6자리 PIN을 입력하면 됩니다.

설정은 `newsshorts/config.json`에 저장됩니다(처음 실행 때 예시 설정을 복사해서 만듦). Claude API 키도 여기에 저장되니
이 파일은 다른 사람과 공유하지 마세요. 깃에는 올라가지 않게 해 두었습니다.

## 설치

```bash
pip install -r newsshorts/requirements.txt
# ffmpeg 필요: macOS `brew install ffmpeg`, Ubuntu `sudo apt install ffmpeg`, Windows는 ffmpeg.org에서 설치
```

한글 폰트(나눔고딕, OFL 라이선스)는 처음 실행할 때 자동으로 내려받습니다.

## 설정

`newsshorts/config.example.json`을 `newsshorts/config.json`으로 복사해서 고칩니다.

| 항목 | 설명 |
| --- | --- |
| `sources` | 감시할 RSS 주소 목록. 소스별로 `keywords`를 달면 그 단어가 들어간 기사만 처리 |
| `keywords` | 전체 공통 키워드 필터 (비우면 전부) |
| `audience` | 시청자층. `senior`(50~70대, 기본값) 또는 `general` |
| `skip_rumors` | 찌라시·루머가 주제인 기사 건너뛰기 (기본 true) |
| `min_topic_score` | 시청자층 관심 주제 점수가 이보다 낮은 기사는 건너뜀 (0이면 전부 처리) |
| `poll_seconds` | 피드 확인 간격(초) |
| `model` | 대본을 쓸 Claude 모델 |
| `voice`, `voice_rate` | TTS 음성. `ko-KR-InJoonNeural`(남), `ko-KR-SunHiNeural`(여). `voice_rate`를 비우면 시청자층 기본 속도 사용 |
| `telegram_bot_token`, `telegram_chat_id` | 넣으면 초안 영상을 텔레그램으로 바로 보내줌 |
| `youtube_*` | 업로드 설정 |

Claude API 키는 환경변수로 넣습니다: `export ANTHROPIC_API_KEY=...`
키가 없으면 기사 제목·요약을 그대로 쓰는 템플릿 대본으로 대신 만듭니다.

## 사용법

```bash
# 1) 샘플 기사로 먼저 확인
python -m newsshorts.cli --config newsshorts/config.json demo

# 2) 감시 시작 (첫 실행 때는 이미 피드에 있는 기사는 건너뜀)
python -m newsshorts.cli --config newsshorts/config.json watch

# 3) 초안 목록 보기
python -m newsshorts.cli --config newsshorts/config.json list --status pending

# 4) 확인 후 업로드 / 버리기 / 이미 올린 영상 내리기
python -m newsshorts.cli --config newsshorts/config.json approve drafts/20261020_..._제목
python -m newsshorts.cli --config newsshorts/config.json reject  drafts/20261020_..._제목
python -m newsshorts.cli --config newsshorts/config.json takedown drafts/20261020_..._제목
```

초안 폴더(`drafts/날짜_제목/`)에는 `video.mp4`, `narration.mp3`, `draft.json`이 생깁니다.
업로드 전에 `draft.json`의 `script.title`, `script.description`을 고치면 그대로 반영됩니다.

옵션: `--no-claude`(템플릿 대본), `--no-tts`(무음 트랙으로 레이아웃만 확인),
`watch --once`(한 번만 확인), `watch --include-backlog`(첫 실행 때 기존 기사도 처리).

## 유튜브 업로드 준비

1. Google Cloud Console에서 프로젝트를 만들고 **YouTube Data API v3**를 켭니다.
2. OAuth 클라이언트(데스크톱 앱)를 만들고 JSON을 `client_secret.json`으로 저장합니다.
3. 처음 `approve`할 때 브라우저가 열리면 채널 계정으로 로그인합니다. 이후엔 `youtube_token.json`으로 자동 로그인됩니다.

주의할 점:
- **구글 심사를 받지 않은 API 프로젝트로 올린 영상은 자동으로 비공개**가 됩니다. 심사(audit)를 받기 전까지는 업로드 후 유튜브 스튜디오에서 직접 공개로 바꿔야 합니다.
- 기본 할당량으로는 하루 약 6개까지 업로드할 수 있습니다.
- AI 음성을 쓰므로 업로드할 때 "합성 콘텐츠 포함" 표시를 자동으로 켭니다.

## 중장년·노년층 맞춤 (`audience: "senior"`)

`newsshorts/audience.py`의 `SENIOR` 설정이 아래를 한꺼번에 바꿉니다.

| 항목 | 내용 |
| --- | --- |
| 기사 우선순위 | 연금·건강보험·세금·물가·부동산·의료·요양·보이스피싱·대통령/국회 같은 주제에 점수를 매겨, 한 번에 새 기사가 여러 개 뜨면 점수 높은 것부터 영상으로 만듦. 제목에 들어간 단어는 2배로 계산 |
| 대본 말투 | 앵커식 '~습니다' 체, 유행어·영어 약어 금지, 어려운 제도 이름은 바로 풀어서 설명 |
| 내용 | 첫 문장에서 시청자와 왜 관련 있는지 말하고, 기사에 근거가 있을 때만 "누가, 언제부터, 얼마나" 영향을 받는지 한 문장 추가 |
| 제목 | "65세 이상 기초연금, 내년부터 이렇게 바뀝니다"처럼 대상과 변화가 바로 보이는 형태, 유명 인물·기관 이름을 앞에, 짧은 질문형 허용. 불안을 부풀리는 표현은 금지 |
| 화면 | 글씨 15% 크게, 자막은 한 번에 최대 2줄, 검은 박스 위 노란 글씨로 대비 강화 |
| 음성 | 일반 설정(+10%)보다 느린 기본 속도(+0%) |

주제 점수표(`topic_weights`)는 채널 반응을 보면서 직접 고치면 됩니다. 관련 기사만 만들고 싶으면 `min_topic_score`를 3 정도로 올리세요.

## 미국 정치 소스

`sources`에 `"region": "us"`를 붙인 소스는 미국 소식으로 처리합니다. Claude가 한국어로 옮기고,
기사에 근거가 있을 때만 환율·관세·수출·주한미군 같은 한국 쪽 영향을 한 문장 덧붙입니다.
예시 설정에는 폴리티코, NPR, 구글뉴스(미국: Trump/tariff/Korea 검색)가 들어 있습니다.
Claude 없이(`--no-claude`) 돌리면 번역이 안 되므로 영어 원문 그대로 나옵니다.

```bash
python -m newsshorts.cli demo --us   # 미국 샘플 기사로 확인
```

## 말머리와 찌라시 안전장치 (`labels.py`)

"[단독]", "검토 중" 같은 보도는 찌라시만큼 클릭이 잘 나오면서, 언론사가 책임을 지고 낸 기사라
출처만 밝히면 안전합니다. 이 방식을 자동으로 지키도록 했습니다.

| 기능 | 내용 |
| --- | --- |
| 말머리 자동 부착 | 원문 제목에 속보/Breaking이 있으면 `[속보]`, 단독/Exclusive가 있으면 `[단독]`, 미국 소스면 `[미국]`, 검토·가능성·관측·전망·considering 같은 표현이 있으면 `[전망]` |
| 지어낸 말머리 제거 | Claude가 제목에 넣은 말머리는 지우고 원문 기준 말머리만 붙임. 원문에 없는 "단독"이라는 단어도 제거 |
| 확신 수준 유지 | 기사 유형(보도/전망)을 Claude에 알려 주고, 전망 기사는 "~할 가능성", "~검토 중"처럼 원문 수준으로만 쓰고 누가 그렇게 보는지 밝히게 함. 화면에도 "출처 · 전망"으로 표시 |
| 출처 명시 | 대본에 언론사 이름이 한 번도 없으면 "OO 보도입니다." 문장을 자동으로 맨 앞에 추가 |
| 소문 기사 건너뛰기 | 찌라시·루머·괴소문·미확인 소문이 주제인 기사는 처리하지 않음 (`skip_rumors: false`로 끌 수 있음) |
| 금지 내용 | 출처 없는 소문, 정치인의 사생활·건강·가족 의혹은 기사에 있어도 대본에 옮기지 않도록 지시 |

## 큰 소식 자동 공개 (`breaking.py`, 기본은 꺼짐)

설정에서 `"auto_publish": true`로 켜면, 아래 조건을 **모두** 통과한 영상은 사람 승인 없이 바로 유튜브에 올라갑니다.
하나라도 걸리면 지금처럼 승인 대기 초안으로 남고, 걸린 이유가 `draft.json`의 `auto_blockers`에 기록됩니다.

| 조건 | 기본값 |
| --- | --- |
| 자동 공개 대상 지역 (`auto_regions`) | 미국 소스만 |
| 큰 소식: 원문이 속보(Breaking)이거나, 90분(`auto_window_minutes`) 안에 서로 다른 매체 2곳(`auto_min_sources`) 이상이 같은 소식을 보도 | |
| 전망·관측 기사가 아닌 확인된 보도 | |
| 소문 관련 기사가 아님 | |
| 시청자 관심 주제 점수 (`auto_min_topic_score`) | 4 이상 |
| Claude가 쓴 대본 (템플릿 대본은 제외) | |
| 사실 확인 통과: Claude가 한 번 더 원문과 대본을 대조해 없는 사실, 과장된 확신, 숫자·이름·번역 오류, 비하 표현을 검사 | |
| 하루 자동 공개 한도 (`auto_daily_limit`) | 5개 |

- 첫 매체 보도로 초안을 만들어 두고, 다른 매체가 같은 소식을 보도하면 **새 영상을 또 만들지 않고** 기존 초안을 공개합니다.
- 자동 공개되면 텔레그램으로 링크와 내리는 명령을 보내 줍니다.
- 문제가 있으면 바로 내릴 수 있습니다:
  ```bash
  python -m newsshorts.cli --config newsshorts/config.json takedown drafts/폴더명
  ```
- 구글 심사를 받지 않은 API 프로젝트는 업로드가 비공개로 잠기므로, 자동 공개를 제대로 쓰려면 YouTube API 심사(audit)를 먼저 받아야 합니다.

## 대본 원칙 (Claude 프롬프트에 들어 있음)

- 기사에 없는 사실·수치·발언은 만들지 않음
- 발언에는 출처를 붙이고, 확정 사실과 보도·전망을 구분
- 제목은 원문에서 가장 센 사실·발언을 앞세우되, 내용 없는 낚시 표현과 진영 비하 표현은 쓰지 않음

## 구조

- `config.py` — 설정 로드
- `audience.py` — 시청자층 프리셋(대본 지시, 주제 점수, 화면·음성 설정)
- `labels.py` — 원문 기준 말머리, 보도/전망 구분, 소문 기사 거르기, 출처 문장 보장
- `breaking.py` — 여러 매체 동시 보도 감지, 자동 공개 조건 판정
- `sources.py` — RSS 수집, 키워드 필터, 중복 방지(`seen.json`)
- `script.py` — Claude 대본 생성(JSON 스키마 강제) + 오프라인 템플릿
- `tts.py` — edge-tts 음성 + 문장별 자막 타이밍
- `render.py` — Pillow로 화면 구성, ffmpeg로 영상 합성
- `pipeline.py` — 기사 1건 → 초안 폴더
- `upload.py` — 유튜브 업로드, `notify.py` — 텔레그램 알림
- `cli.py` — 명령어 진입점
- `app.py`, `static/index.html` — 관리 앱(웹 화면 + API), `start_app.bat`/`start_app.sh` — 실행 파일
- `runner.py` — 감시 루프(명령어와 앱이 같이 사용), `log.py` — 실행 기록
