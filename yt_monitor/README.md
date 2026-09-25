# YouTube 채널 성과 모니터 + 로컬 LLM 주제/대본 생성

조회수 체크 → 성장 둔화 감지 → 다음 영상 주제/제목/대본 자동 생성 → 텔레그램 알림까지
자동으로 처리하고, 대본을 **쇼츠 영상(mp4)**으로까지 만들어 주는 프로그램입니다. 무료 도구만 사용합니다:
YouTube Data API 무료 쿼터, Telegram Bot API, **로컬 Ollama**(qwen2.5:7b), **Pexels**(무료 스톡 영상),
**edge-tts**(무료 TTS), **ffmpeg**. 유료 API는 쓰지 않습니다.
Windows용 **단일 exe**(`YouTubeMonitor.exe`)로 배포할 수 있습니다.

```
YouTube Data API ─▶ SQLite 시계열 ─▶ 하락 감지 ─┬─▶ 리포트 (reports/날짜/)
                                              └─▶ Ollama 주제·제목·대본 생성 (outputs/날짜/채널명.md)
                                                    ├─▶ 텔레그램 알림 (요약 + 파일 첨부)
                                                    └─▶ 🎬 영상 생성: 씬 분리 → 키워드(Ollama) → Pexels 영상
                                                          → edge-tts 음성 → 자막(SRT) → ffmpeg 합성 → outputs/날짜/제목.mp4
```

## 0. 먼저 확인할 것

| 항목 | 확인 방법 |
| --- | --- |
| Ollama 설치 | <https://ollama.com/download>에서 설치 → 명령 프롬프트에서 `ollama --version` |
| qwen2.5:7b 모델 | `ollama pull qwen2.5:7b` (약 4.7GB) → `ollama list`에 보이면 완료 |
| YouTube API 키 | 아래 발급 방법 참고 |
| 채널 | `@핸들`, `UC…` 채널 ID, 채널 주소 중 아무거나 |
| 텔레그램 봇 토큰 / chat_id | 아래 발급 방법 참고 (선택. 없으면 알림 없이 동작) |
| ffmpeg (영상 생성) | 명령 프롬프트에서 `ffmpeg -version`. 없으면 [영상 생성](#3-대본--쇼츠-영상-자동-생성) 참고 |
| Pexels API 키 (영상 생성) | <https://www.pexels.com/api/> 가입 즉시 무료 발급 (없으면 단색 배경으로 만듦) |

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
outputs/           ← 생성한 주제/대본 (날짜/채널명.md, 채널명_대본.txt), 영상 (날짜/제목.mp4)
logs/              ← 실행 로그
```

> Windows SmartScreen이 "알 수 없는 게시자" 경고를 띄우면 **추가 정보 → 실행**을 누르세요.
> 서명되지 않은 exe라서 나오는 경고입니다.

### 화면 구성
| 영역 | 기능 |
| --- | --- |
| **▶ 지금 체크하기** | YouTube에서 통계 수집 → 분석 → 리포트 저장. 둔화가 감지되면 주제/대본 자동 생성 + 텔레그램 알림 |
| **🔥 유행 쇼츠 분석** | 최근 며칠 한국에서 조회수가 빠르게 오른 쇼츠를 분석 → 지금 뜨는 주제 추천 → 50초 쇼츠 대본 (→ 체크하면 영상까지) |
| **✨ 새 주제/대본 생성** | 선택한 채널의 주제/대본을 바로 생성 (Ollama 호출, 글자가 생성되는 대로 표시, 취소 가능) |
| **자동 체크** 토글 | 켜면 설정한 주기마다 백그라운드로 체크 (APScheduler). 상태는 다음 실행에도 유지 |
| 채널 목록 | 상태, 구독자, 최근/이전 변화율, 최근 7일 조회수, 14일 추이(스파크라인), 마지막 체크 |
| **🔥 트렌드** 탭 | 유행 쇼츠 순위 (시간당 조회수, 조회수, 구독자, 🔥 떡상 표시), "대본 생성 후 영상까지 자동으로 만들기" 체크박스 |
| **조회수 추이** 탭 | 선택한 채널의 지표, 최근 영상 성과, 상위 성과 영상, 패턴 |
| **주제/대본** 탭 | 생성 결과(수정 가능). **주제 선택 → [선택한 주제로 대본 생성]**, **[💾 파일로 저장]**, **[🎬 이 대본으로 영상 만들기]** |
| **영상 생성** 탭 | 대본 입력(생성 결과가 자동으로 들어옴) → **[🎬 영상 만들기]** → 6단계 진행 표시 → 결과 경로 + **[📂 폴더 열기]** |
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

## 🌎 콘텐츠 언어/시장 (기본: 영어권 · 미국 중심)

**⚙ 설정 → 콘텐츠 언어/시장**에서 고르면 아래 값이 한꺼번에 바뀝니다. (`config.yaml`의 `language: en | ko`)

| 항목 | 영어권 (미국) — 기본 | 한국 |
| --- | --- | --- |
| 유행 분석 지역 / 제목 언어 | US / 영어 제목만 (한글·일본어·키릴 등 제외) | KR / 한글 제목만 |
| 유행 최소 조회수 | 5만 | 1만 |
| 주제·대본 프롬프트 | `*_en.txt` (영어 제목·대본, **추천 이유는 한국어**) | 한국어 프롬프트 |
| TTS 음성 | `en-US-GuyNeural` (영상 생성 탭에서 Jenny, Aria 등 선택 가능) | `ko-KR-SunHiNeural` |
| 자막 | Arial Black, 18자 | 맑은 고딕, 14자 |

- 언어 설정이 없던 이전 버전의 `config.yaml`은 처음 실행할 때 **영어/미국 설정으로 자동 전환**됩니다.
  그 뒤에 `config.yaml`에서 직접 바꾼 값(예: 음성)은 그대로 유지됩니다.
- 영어 대본 길이는 분당 150단어 기준입니다 (쇼츠 50초 ≈ 125단어).

## ⚡ 눈에 띄는 영상 만들기 (효과 · 훅 문구 · Pixabay · AI 이미지)

스톡 영상은 "어디에나 쓸 수 있게" 무난하게 찍혀 있어서 그대로 붙이면 밋밋합니다. 그래서 편집으로 강하게 만듭니다.

| 기능 | 기본값 | 설정 (`config.yaml`) |
| --- | --- | --- |
| **첫 화면 큰 훅 문구** — 처음 2.5초 동안 위쪽에 큰 노란 글씨(검은 테두리, 팝 효과). 영어는 대문자 | 제목을 사용 (영상 생성 탭에서 수정, `-` 입력 시 끔) | `video.hook_seconds`, `hook_font_size` |
| **줌인/줌아웃** — 컷마다 번갈아 천천히 8% | 켜짐 | `video.zoom` (0이면 끔) |
| **색감 강화** — 대비·채도 | 1.08 / 1.2 | `video.contrast`, `saturation` |
| **빠른 컷** — 2.5초마다 화면 전환 | 2.5초 | `video.clip_max_seconds` |
| **강렬한 검색어** — dramatic, close up, slow motion, neon … | 켜짐 | `prompts/video_scenes.txt` |
| **Pixabay** — Pexels와 번갈아 검색해서 영상 선택 폭 넓히기 | 키를 넣으면 켜짐 | ⚙ 설정 → Pixabay API 키 |
| **AI 이미지 (ComfyUI)** — 첫 씬(훅)과 스톡 영상을 못 찾은 씬에 AI 이미지 | 꺼짐 | ⚙ 설정 → AI 이미지 생성, `ai_images.mode` (`mix`/`all`) |

### 🎙 분위기에 맞는 음성 자동 선택
영상 생성 탭의 음성이 **"자동 (분위기에 맞게)"**(기본값)이면, 키워드를 뽑을 때 Ollama가 대본 분위기도 함께 판단해서
음성 · 말하기 속도 · 음높이를 고릅니다. (Ollama가 꺼져 있으면 대본 단어로 추정)

| 분위기 | 이런 대본 | 영어 음성 (속도 / 음높이) | 한국어 음성 |
| --- | --- | --- | --- |
| energetic | 랭킹, 꿀팁, 챌린지 | Guy (+12% / +2Hz) | 인준 |
| dramatic | 반전, 충격 사실 | Christopher (+2% / -3Hz) | 현수 |
| mysterious | 괴담, 미스터리 | Christopher (-6% / -6Hz) | 인준 (낮게) |
| calm | 과학, 역사 설명 | Eric (기본) | 선희 |
| playful | 웃긴 이야기, 동물 | Aria (+10% / +3Hz) | 선희 (밝게) |
| emotional | 감동, 공감 | Jenny (-4%) | 선희 (천천히) |

- 음성을 직접 고르면 그 음성을 씁니다. 항상 같은 음성을 쓰려면 `video.voice_mode: fixed`.
- 분위기별 음성은 `video.mood_voices`로 바꿀 수 있습니다. 예: `{en: {calm: {voice: en-US-JennyNeural}}}`
- 무료 edge-tts는 외침·속삭임 같은 감정 스타일은 지원하지 않아서, 음성 · 속도 · 음높이 조합으로 분위기를 맞춥니다.

### 🎵 배경음악(BGM) — 분위기에 맞게 자동으로

음악은 저작권 때문에 인터넷에서 자동으로 받지 않고, **직접 넣어 둔 무료 곡**을 분위기에 맞게 골라 씁니다.

1. 영상 생성 탭의 **[🎵 BGM 폴더]** → exe 옆에 `bgm/` 과 분위기 폴더 6개가 생깁니다.
   `energetic`(신나는) · `dramatic`(웅장/긴장) · `mysterious`(미스터리) · `calm`(잔잔) · `playful`(코믹) · `emotional`(감성)
2. **YouTube 스튜디오 → 오디오 보관함**에서 분위기/장르 필터로 곡을 받아 알맞은 폴더에 넣습니다 (YouTube 업로드용 무료 · 저작권 걱정 없음).
3. 영상을 만들 때 대본 분위기에 맞는 폴더에서 한 곡을 무작위로 골라, **목소리가 나올 때는 음악을 자동으로 줄여서**(더킹) 깝니다.
   - 폴더가 비어 있으면 목소리만 넣고, `_업로드정보.txt`에 분위기별 **추천 곡 스타일 · 오디오 보관함 필터**를 적어 줍니다.
   - '저작자 표시 필요' 곡은 곡과 같은 이름의 `.txt`(예: `song.mp3` + `song.txt`)에 표시 문구를 적으면 출처에 자동으로 붙습니다.
4. 크기 조절: `config.yaml` → `video.bgm_volume` (기본 0.15, 0.1 작게 ~ 0.3 크게), 끄려면 `bgm_enabled: false`.

> ⚠ 유명 가요/팝송은 넣지 마세요. Content ID 저작권 신고로 수익이 원곡자에게 가거나 영상이 막힐 수 있습니다.

### AI 이미지 생성 켜기 (그래픽카드 필요, RTX 5060 8GB면 충분)
1. **ComfyUI portable**(Windows · NVIDIA)을 받아 압축을 풉니다. RTX 50 시리즈는 **cu130** 버전을 받으세요.
   Desktop 버전은 사용자 폴더 이름이 한글이면 안 켜지는 경우가 있어 portable을 권장합니다.
2. **`sdxl_lightning_4step.safetensors`** (<https://huggingface.co/ByteDance/SDXL-Lightning>)를 받아
   `ComfyUI_windows_portable\ComfyUI\models\checkpoints` 폴더에 넣습니다.
3. 프로그램 **⚙ 설정 → AI 이미지 생성** 체크 → **[연결 확인]**.
   - ComfyUI를 **직접 켤 필요가 없습니다.** 폴더를 자동으로 찾고(다운로드 · 바탕화면 · C:\ 등, 못 찾으면 **ComfyUI 폴더**에 지정),
     영상을 만들 때 **창 없이 자동으로 켰다가 이미지를 다 만들면 자동으로 끕니다.** 직접 켜 둔 ComfyUI는 끄지 않습니다.
   - [연결 확인]은 ComfyUI가 꺼져 있어도 폴더 · 모델 파일이 제대로 있는지 알려 줍니다.
   - `sdxl_lightning_*step` 모델은 **4스텝 · cfg 1 · euler · sgm_uniform**을 자동으로 맞춥니다 (config 수정 필요 없음).
4. 영상을 만들면 Ollama가 씬마다 영화 같은 장면 묘사를 쓰고, ComfyUI가 768x1344 세로 이미지를 만듭니다.
   이미지는 줌 효과로 움직이는 영상처럼 들어갑니다. 이미지를 만들기 전에 Ollama 모델을 그래픽카드에서 내려서
   8GB로도 돌아가게 했습니다. 처음 켤 때는 모델을 읽느라 1~2분 걸립니다.
- **실제 사람 같은 얼굴(추천)**: 실사 전용 모델 **RealVisXL V5.0 Lightning**
  (<https://huggingface.co/SG161222/RealVisXL_V5.0_Lightning> → `RealVisXL_V5.0_Lightning_fp16.safetensors`)을
  같은 `checkpoints` 폴더에 넣으세요. 모델이 여러 개면 **실사 모델(RealVis > Juggernaut)을 자동으로 먼저** 쓰고,
  권장값(5스텝 · cfg 1.5 · dpmpp_sde · karras)도 자동으로 맞춥니다.
  기본 스타일도 "RAW photo, candid, 35mm, 자연광, 피부 질감"으로 바뀌었고, AI 이미지에는 채도 보정을 하지 않습니다.
- 사람이 나오는 AI 이미지는 업로드할 때 **'변경되거나 합성된 콘텐츠'를 '예'로** 표시하세요 (YouTube 정책).
- 다른 모델: Juggernaut XL Lightning 등은 `ai_images.steps/cfg/sampler/scheduler` 값(기본 6 · 2.0 · dpmpp_sde · karras)을 쓰고,
  일반 SDXL 모델은 `steps: 25`, `cfg: 6`으로 바꾸세요. 모델마다 이용 조건이 다르니 상업적 이용이 가능한지 확인하세요.
- ComfyUI를 켜지 못하면 경고만 남기고 스톡 영상으로 계속 만듭니다. 원인은 `<영상>_work/logs/comfyui.log`에 있습니다.
- 단독 테스트: `python -m yt_monitor.video.comfyui --check` / `python -m yt_monitor.video.comfyui "a giant shark under a neon city"`

## 🚀 원클릭 쇼츠 만들기 (제일 쉬운 방법)

맨 위 **[🚀 원클릭 쇼츠 만들기]** 버튼 하나로 끝까지 자동으로 진행합니다.

- **주제 칸을 비우면**: 요즘 유행 쇼츠 분석 → 주제 선택 → 쇼츠 대본 → 업로드 정보(제목 후보 · 카테고리 · 해시태그) → 영상
- **주제를 적으면** (예: `Why cats knock things off tables`): 유행 분석을 건너뛰고 그 주제로 바로 대본 → 영상
- 끝나면 영상이 있는 폴더가 열리고(파일 선택됨), 추천 제목 · 카테고리 · 해시태그를 한 창에 요약해서 보여줍니다.
  중간에 묻는 창 없이 진행되며, 경고가 있으면 마지막 요약에 함께 적습니다.
- ComfyUI(AI 이미지)도 필요할 때 자동으로 켜고 끕니다.

## 🔥 유행 쇼츠 분석 → 영상

유튜브는 알고리즘 점수를 공개하지 않으므로, **최근 올라왔는데 조회수가 빠르게 오르는 쇼츠**를 찾아 유행을 추정합니다.

1. **수집** (YouTube Data API, 1회 약 210 unit / 무료 하루 10,000)
   - 미국(또는 한국) 인기 급상승 목록 + 최근 3일 조회수 상위 짧은 영상 검색 (검색어 없음, `#shorts`)
   - 3분 이하 · 최근 3일 · 조회수 5만 이상(한국 1만) · 영어 제목(한국 모드는 한글 제목)만 남김
2. **분석**: 시간당 조회수로 순위, 조회수가 구독자의 3배 이상이면 🔥 떡상(작은 채널이 알고리즘을 탄 경우), 반복 키워드
3. **주제 추천** (Ollama, `prompts/trend_topics.txt`): 유행의 공통점을 탄 주제 5개. 스톡 영상 + 내레이션으로 만들 수 있는 주제 위주
4. **쇼츠 대본** (`prompts/shorts_script.txt`): 약 50초, 첫 문장 훅 → `outputs/날짜/트렌드.md`, `트렌드_대본.txt`
5. **업로드 정보** (`prompts/upload_meta.txt`): 클릭 잘 되는 제목 5개(훅 방식을 서로 다르게 · 이유는 한국어) + ⭐ 추천,
   카테고리(유행 쇼츠의 **카테고리별 영상 수 · 시간당 조회수**를 근거로), 설명 2줄, 해시태그(`#Shorts` 포함), 태그.
   추천 제목이 영상 제목/첫 화면 훅 문구로 들어갑니다.
6. **영상**: 영상 생성 탭에 대본이 자동으로 들어갑니다. "대본 생성 후 영상까지 자동으로 만들기"를 켜면 바로 mp4까지.
   영상 옆에 `<이름>_업로드정보.txt`(제목 후보 · 카테고리 · 설명 + 해시태그 + 출처)가 생기고,
   영상 탭의 **[📋 업로드 정보]** 버튼으로 열어서 그대로 복사해 붙여넣으면 됩니다.

> 카테고리는 조회수를 크게 좌우하지 않습니다(비슷한 영상과 묶이는 정도). 조회수에는 제목 · 첫 2초 · 끝까지 보는 비율이 훨씬 중요합니다.
> 끄려면 `config.yaml`의 `ollama.upload_meta` / `video.upload_meta` 를 `false`로.

매일 자동으로 돌리려면 Windows **작업 스케줄러**에 `YouTubeMonitor.exe --trends --with-video`를 등록하세요.
설정은 `config.yaml`의 `trends:` (`lookback_days`, `search_queries`, `min_views`, `script_seconds` 등).

## 3. 대본 → 쇼츠 영상 자동 생성

대본을 넣으면 스톡 영상 · 음성 · 자막을 붙여 **9:16 세로 mp4**를 만듭니다. 전부 무료입니다.

| 단계 | 하는 일 | 사용 도구 |
| --- | --- | --- |
| 1/6 키워드 추출 | 대본을 문장/씬으로 나누고, 씬마다 영어 검색어 2~3개 추출 | 로컬 Ollama (`prompts/video_scenes.txt`). 꺼져 있으면 대본의 명사로 대신 검색 |
| 2/6 영상 다운로드 | 검색어로 세로 영상 검색 → 출력 해상도에 맞는 파일 다운로드 (캐시) | Pexels API (시간당 200회 무료) |
| 3/6 TTS 음성 | 씬마다 음성 합성 + 단어별 타임스탬프 | edge-tts (키 불필요, 한국어/영어) |
| 4/6 자막 | 타임스탬프로 14자 안팎의 짧은 자막 덩어리 생성 → SRT | 직접 생성 |
| 5/6 합성 | 씬 길이에 맞춰 클립 이어붙이기 (짧으면 반복, 길면 자름, 가운데 크롭) + 음성 삽입 (배경음악 없음) | ffmpeg |
| 6/6 저장 | 자막 번인 (하단 중앙, 굵은 글씨, 반투명 검은 박스) → `outputs/날짜/제목.mp4` | ffmpeg (libass) |

결과 파일 (`outputs/2026-09-24/`):
```
제목.mp4          ← 완성 영상 (1080x1920, 30fps, H.264 + AAC)
제목.srt          ← 자막 파일 (유튜브에 따로 올리거나 편집용)
제목_출처.txt      ← 사용한 Pexels 영상 작가/주소 (설명란에 넣으면 좋음)
제목_work/        ← 중간 파일: 클립(shots/), 씬별 음성(tts/), scenes.json, ffmpeg 로그(logs/)
```

### 먼저 준비할 것

**1) ffmpeg 설치** — 프로그램이 시작 시 확인하고, 없으면 아래 안내를 띄웁니다.
- Windows: 명령 프롬프트에서 `winget install Gyan.FFmpeg` → 프로그램 재실행
- 또는 <https://www.gyan.dev/ffmpeg/builds/>에서 `ffmpeg-release-essentials.zip`을 받아 `bin\ffmpeg.exe`를
  `YouTubeMonitor.exe` 옆에 복사 (또는 `config.yaml`의 `video.ffmpeg_path`에 경로 입력)
- 확인: `YouTubeMonitor.exe --check-ffmpeg` (또는 `python main.py --check-ffmpeg`)

**2) Pexels API 키 (무료)**
1. <https://www.pexels.com/api/>에 가입/로그인합니다.
2. **Your API Key** 페이지에서 사용 목적(예: 개인 유튜브 영상 제작)을 적으면 키가 바로 발급됩니다.
3. 프로그램 **⚙ 설정 → Pexels API 키**에 붙여넣습니다. (`.env`의 `PEXELS_API_KEY`도 가능)

키가 없으면 스톡 영상 대신 단색 배경으로 만들지 물어봅니다.

### 사용법 (GUI)
1. **✨ 새 주제/대본 생성**으로 대본을 만들면 **영상 생성** 탭에 대본과 제목이 자동으로 들어갑니다.
   (다른 대본은 직접 붙여넣거나 **[📥 주제/대본 탭에서 가져오기]**)
2. 음성을 고르고 **[🎬 영상 만들기]**. `1/6 씬 분리 · 키워드 추출 중…` 처럼 진행 상황과 로그가 표시됩니다.
3. 끝나면 결과 경로와 **[📂 폴더 열기] [▶ 영상 열기]** 버튼이 활성화됩니다.

명령줄: `python main.py --make-video 대본.txt --title "편의점 꿀조합" [--voice ko-KR-InJoonNeural]`

### 영상 설정 (`config.yaml` → `video:`)
| 설정 | 기본값 | 의미 |
| --- | --- | --- |
| `clip_max_seconds` | 4 | **씬당 클립 길이.** 씬이 이보다 길면 클립 여러 개로 나눠 화면을 바꿈 |
| `min_scene_seconds` / `max_scene_chars` | 2.5 / 90 | 짧은 문장은 다음 문장과 합쳐 한 씬으로 / 씬 최대 글자 수 |
| `tts_voice` / `tts_rate` | `ko-KR-SunHiNeural` / `+10%` | 음성(남성: `ko-KR-InJoonNeural`) / 말하기 속도 |
| `subtitle_font` / `subtitle_font_size` | `Malgun Gothic` / 72 | 자막 폰트(굵게) / 크기(1080x1920 기준 px) |
| `subtitle_max_chars` | 14 | 자막 한 덩어리 최대 글자 수 |
| `subtitle_margin_bottom` | 320 | 화면 아래에서 자막까지 거리(px). 쇼츠 버튼/설명에 가리지 않게 |
| `subtitle_box_opacity` | 0.6 | 자막 뒤 검은 박스 불투명도 (0~1) |
| `width` / `height` / `fps` | 1080 / 1920 / 30 | 출력 해상도 (9:16) |
| `crf` / `preset` | 21 / `veryfast` | 화질 / 인코딩 속도 |
| `keep_work_files` | true | 중간 파일과 ffmpeg 로그를 `제목_work/`에 남김 |

`pexels:`에서 `orientation`(기본 `portrait`), `per_page`를 바꿀 수 있습니다.

### 단계별 단독 테스트 (각 단계의 결과 파일을 직접 확인)
저장소 루트에서 실행하며, 결과는 `samples/` 아래에 생깁니다.

| 단계 | 명령 | 결과물 |
| --- | --- | --- |
| 0) ffmpeg 확인 | `python -m yt_monitor.video.ffmpeg` | 버전, libass/libx264 지원 여부 |
| 1) Pexels | `python -m yt_monitor.video.pexels "ocean waves" "coffee" --out samples/pexels` | 키워드별 mp4 다운로드 |
| 2) TTS | `python -m yt_monitor.video.tts "안녕하세요. 테스트입니다." --out samples/tts` | `tts.mp3`, `tts_words.json`(단어 타이밍), `tts.srt` |
| 3) 자막 | `python -m yt_monitor.video.subtitles 대본.txt --words samples/tts/tts_words.json` | `samples/subs/subtitles.srt`, `.ass` |
| 4) 합성 | `python -m yt_monitor.video.compose --out samples/compose` | 인터넷 없이 테스트 패턴으로 `01_clips.mp4` → `02_with_audio.mp4` → `03_final.mp4` (`--step concat`/`audio`로 중간까지만) |
| 4) 합성 (내 파일) | `python -m yt_monitor.video.compose --clips a.mp4 b.mp4 --audio samples/tts/tts.mp3` | 받은 클립 + TTS 음성으로 합성 |
| 씬/키워드 | `python -m yt_monitor.video.scenes 대본.txt` | 씬 분리 결과와 Ollama 키워드 |
| 5) 전체 | `python -m yt_monitor.video.pipeline 대본.txt --title 제목` | `outputs/날짜/제목.mp4` |
| 5) 전체 (오프라인) | `python -m yt_monitor.video.pipeline 대본.txt --offline` | 무음 + 단색 배경으로 합성만 확인 |

### 문제 해결
- **ffmpeg 오류**: 오류 창에 ffmpeg 출력 마지막 15줄과 로그 파일 경로가 나옵니다.
  `제목_work/logs/NN_단계.log`에 실행한 **명령줄 전체**, 작업 폴더, 종료 코드, ffmpeg 출력이 모두 남으니
  명령줄을 복사해 명령 프롬프트에서 그대로 다시 실행해 볼 수 있습니다. 프로그램 로그는 `logs/yt_monitor.log`.
- **자막이 네모(□)로 나옴**: `subtitle_font`에 PC에 설치된 한글 폰트 이름을 넣으세요 (예: `NanumGothic`).
- **TTS 실패**: edge-tts는 인터넷(`speech.platform.bing.com`)이 필요합니다. 회사망이면 `video.tts_proxy`에 프록시 주소를 넣으세요.
- **영상이 주제와 안 맞음**: `prompts/video_scenes.txt`를 고치거나, 대본에 구체적인 장면(사물·장소·행동)을 넣으면 좋아집니다.
  Pexels는 영어 검색 결과가 훨씬 많으니 Ollama를 켜 두는 것을 권장합니다.

## 4. 성장 둔화 판단

| 설정 (`analysis`) | 기본값 | 의미 |
| --- | --- | --- |
| `metric` | `views_per_day` | 영상 지표. 누적 조회수 ÷ 게시 후 경과일. `views_at_age`는 게시 후 `age_days`일 시점 조회수 (시계열이 쌓인 뒤 사용) |
| `recent_n` / `baseline_m` | 5 / 10 | 최근 N개 영상과 그 이전 M개 영상을 비교 |
| `drop_threshold_pct` | 30 | 최근 N개 평균이 이전 M개보다 X% 이상 낮으면 **둔화** (설정 창의 '하락 임계값') |
| `aggregate` | `mean` | `median`으로 바꾸면 대박 영상 하나에 덜 흔들림 |
| `weekly_drop_threshold_pct` | 없음 | 채널 주간 조회수 증가량이 전주보다 X% 이상 줄어도 둔화로 판단 |
| `alert_cooldown_hours` | 24 | 같은 채널은 이 시간 동안 다시 생성·알림하지 않음 (하락폭이 10%p 이상 커지면 예외) |

채널별로 다른 임계값을 쓰려면 `config.yaml`의 채널 항목에 `analysis:`를 추가하세요.

## 5. 소스로 실행 / 단계별 단독 테스트

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
| 트렌드 | `python -m yt_monitor.trends` / `--generate` | 유행 쇼츠 목록 / + 주제·대본 |
| 5) GUI | `python -m yt_monitor.gui` | 창 실행 |
| 영상 | `python -m yt_monitor.video.pipeline 대본.txt` | 대본 → mp4 ([3. 영상 생성](#3-대본--쇼츠-영상-자동-생성)의 단계별 테스트 참고) |
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
YouTube, Ollama, 텔레그램, Pexels, edge-tts는 가짜 서버와 객체로 대체되므로 키나 모델이 없어도 됩니다.
영상 합성 테스트는 ffmpeg가 설치되어 있을 때만 실행됩니다.
```bash
pytest tests/test_yt_monitor.py tests/test_yt_monitor_generation.py tests/test_yt_monitor_gui.py tests/test_yt_monitor_video.py tests/test_yt_monitor_trends.py tests/test_yt_monitor_visuals.py
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
