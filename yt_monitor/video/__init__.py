"""대본 → 쇼츠 영상 자동 생성 (무료 도구만 사용).

    scenes.py     대본을 씬으로 나누고 Ollama로 씬별 검색 키워드 추출
    pexels.py     Pexels API로 스톡 영상 검색/다운로드 (무료 API 키)
    tts.py        edge-tts로 음성 + 단어별 타임스탬프 생성 (키 불필요)
    subtitles.py  타임스탬프 → SRT(업로드용) / ASS(번인용, 쇼츠 스타일)
    ffmpeg.py     ffmpeg 찾기/실행/로그
    compose.py    클립 이어붙이기 → 음성 삽입 → 자막 번인 (9:16)
    pipeline.py   전체 흐름 (GUI와 CLI에서 사용)
"""
