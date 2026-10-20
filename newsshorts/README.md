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
| `poll_seconds` | 피드 확인 간격(초) |
| `model` | 대본을 쓸 Claude 모델 |
| `voice`, `voice_rate` | TTS 음성. `ko-KR-InJoonNeural`(남), `ko-KR-SunHiNeural`(여) |
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

# 4) 확인 후 업로드 / 버리기
python -m newsshorts.cli --config newsshorts/config.json approve drafts/20261020_..._제목
python -m newsshorts.cli --config newsshorts/config.json reject  drafts/20261020_..._제목
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

## 대본 원칙 (Claude 프롬프트에 들어 있음)

- 기사에 없는 사실·수치·발언은 만들지 않음
- 발언에는 출처를 붙이고, 확정 사실과 보도·전망을 구분
- 제목은 원문에서 가장 센 사실·발언을 앞세우되, 내용 없는 낚시 표현과 진영 비하 표현은 쓰지 않음

## 구조

- `config.py` — 설정 로드
- `sources.py` — RSS 수집, 키워드 필터, 중복 방지(`seen.json`)
- `script.py` — Claude 대본 생성(JSON 스키마 강제) + 오프라인 템플릿
- `tts.py` — edge-tts 음성 + 문장별 자막 타이밍
- `render.py` — Pillow로 화면 구성, ffmpeg로 영상 합성
- `pipeline.py` — 기사 1건 → 초안 폴더
- `upload.py` — 유튜브 업로드, `notify.py` — 텔레그램 알림
- `cli.py` — 명령어 진입점
