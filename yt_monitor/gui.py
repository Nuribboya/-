"""tkinter GUI: 채널 현황 · 지금 체크 · 주제/대본 생성 · 영상 생성 · 자동 체크 토글 · 설정.

스레드 규칙: 오래 걸리는 작업(YouTube 수집, Ollama 생성, 스케줄러 작업)은 백그라운드 스레드에서
돌리고, 화면 갱신은 self.ui(...)로 큐에 넣어 Tk 메인 스레드에서만 실행한다.

단독 실행: python -m yt_monitor.gui [config.yaml 경로]
"""

from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from datetime import timedelta
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, ttk
from typing import Callable
from zoneinfo import ZoneInfo

from .analyzer import ChannelAnalysis, analyze_channel, value_at
from .config import LANGUAGE_LABELS, Config, ConfigError, apply_language, load_config, read_raw, save_config
from .db import Database, from_iso, utcnow
from .report import fduration, fnum, fpct, pattern_lines, status_text

log = logging.getLogger(__name__)

APP_TITLE = "YouTube 채널 성과 모니터"
AUTO_VOICE = "자동 (분위기에 맞게)"
SPARK = "▁▂▃▄▅▆▇█"


# ---- 화면에 쓰는 순수 함수 (테스트 가능) -------------------------------------------------

def sparkline(values: list[float]) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return SPARK[3] * len(vals)
    return "".join(SPARK[int((v - lo) / (hi - lo) * (len(SPARK) - 1))] for v in vals)


def daily_view_gains(db: Database, channel_id: str, days: int = 14) -> list[float]:
    """채널 총 조회수 시계열 → 최근 days일의 하루 증가량 (데이터가 있는 날만)."""
    rows = db.get_channel_stats(channel_id)
    points = [(from_iso(r["collected_at"]), r["view_count"]) for r in rows]
    if len(points) < 2:
        return []
    end = points[-1][0]
    gains = []
    for d in range(days, 0, -1):
        a = value_at(points, end - timedelta(days=d))
        b = value_at(points, end - timedelta(days=d - 1))
        if a is not None and b is not None:
            gains.append(b - a)
    return gains


def summary_text(a: ChannelAnalysis, tz: ZoneInfo, gains: list[float]) -> str:
    """'조회수 추이' 탭에 보여줄 요약."""
    s = a.settings
    lines = [
        f"{a.channel_title}   {status_text(a)}",
        f"분석 기준: {a.analyzed_at.astimezone(tz):%Y-%m-%d %H:%M}",
        "",
        f"구독자             {fnum(a.subscribers)}",
        f"최근 {len(a.recent)}개 영상 평균   {fnum(a.recent_avg, 1)}  (일평균 조회수 기준)",
        f"이전 {len(a.baseline)}개 영상 평균   {fnum(a.baseline_avg, 1)}",
        f"변화율             {fpct(a.change_pct)}   (둔화 기준 -{s['drop_threshold_pct']:g}%)",
        f"최근 7일 조회수     {fnum(a.weekly_views)}"
        + (f"  (전주 대비 {fpct(a.weekly_change_pct)})" if a.weekly_change_pct is not None else ""),
    ]
    if gains:
        lines.append(f"일별 조회수 증가    {sparkline(gains)}  (최근 {len(gains)}일, "
                     f"최소 {fnum(min(gains))} / 최대 {fnum(max(gains))})")
    if a.insufficient:
        lines += ["", f"⏳ {a.insufficient}"]
    for r in a.reasons:
        lines.append(f"🚨 {r}")

    lines += ["", "── 최근 영상 ──"]
    recent_ids = {p.video.video_id for p in a.recent}
    for p in a.videos[:10]:
        mark = "●" if p.video.video_id in recent_ids else " "
        lines.append(f"{mark} {p.video.published_at.astimezone(tz):%m-%d}  조회 {fnum(p.views):>9}  "
                     f"일평균 {fnum(p.views_per_day):>7}  7일 +{fnum(p.gain_7d):>7}  {p.video.title}")
    lines += ["", f"── 상위 성과 Top {len(a.top)} ──"]
    for i, p in enumerate(a.top, 1):
        lines.append(f"{i}. {p.video.title}  [{p.video.category_name or '미분류'} · "
                     f"{fduration(p.video.duration_seconds)}]  {fnum(p.metric)}")
    lines += ["", "── 상위 영상 패턴 ──"] + [f"· {x}" for x in pattern_lines(a)]
    return "\n".join(lines)


_URL_HANDLE = re.compile(r"youtube\.com/(@[\w.\-가-힣]+)")
_URL_ID = re.compile(r"youtube\.com/channel/(UC[\w-]{22})")


def parse_channel_lines(text: str) -> list[dict]:
    """설정 창의 채널 입력 → config 채널 목록.

    한 줄에 하나: '@핸들', 'UC채널ID', 채널 주소 모두 가능. '| 표시이름' 을 붙이면 이름 지정.
    """
    out = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        key, _, name = (x.strip() for x in raw.partition("|"))
        if m := _URL_ID.search(key):
            key = m.group(1)
        elif m := _URL_HANDLE.search(key):
            key = m.group(1)
        item = {"id": key} if re.fullmatch(r"UC[\w-]{22}", key) else {
            "handle": key if key.startswith("@") else "@" + key}
        if name:
            item["name"] = name
        out.append(item)
    return out


def channel_lines(channels: list) -> str:
    lines = []
    for c in channels or []:
        if isinstance(c, str):
            lines.append(c)
            continue
        key = c.get("id") or c.get("handle") or ""
        lines.append(f"{key} | {c['name']}" if c.get("name") else key)
    return "\n".join(lines)


def open_path(path: Path, select: bool = False) -> None:
    """파일/폴더를 OS 기본 프로그램으로 연다. select=True면 탐색기에서 그 파일을 선택한 채로 연다."""
    path = Path(path)
    if sys.platform == "win32":
        if select and path.is_file():
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(path if not select else path.parent)  # type: ignore[attr-defined]
    else:
        target = path.parent if select and path.is_file() else path
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])


# ---- 업로드 정보 창 ------------------------------------------------------------------

STUDIO_URL = "https://studio.youtube.com"


class UploadDialog(tk.Toplevel):
    """영상이 완성되면 업로드에 필요한 것(제목 · 설명+해시태그 · 태그 · 카테고리)을 한 창에, 항목마다 [복사]."""

    def __init__(self, master, res, warnings: list[str] | None = None):
        from .upload_meta import CATEGORY_NOTE, category_label

        super().__init__(master)
        meta = res.upload
        self.res = res
        self.title("📋 업로드 정보 — 복사해서 붙여넣기")
        self.transient(master)
        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        self.copied_var = tk.StringVar(value="")

        head = f"✅ 완성: {res.video_path.name} ({res.duration:.0f}초)"
        ttk.Label(frm, text=head, font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        row = 1
        for w in (warnings or [])[:4]:
            ttk.Label(frm, text="⚠ " + w, foreground="#b35c00", wraplength=620, justify="left").grid(
                row=row, column=0, columnspan=3, sticky="w")
            row += 1

        # 제목 (후보 중 고르거나 직접 수정)
        titles = [t["title"] for t in meta.titles]
        self.title_var = tk.StringVar(value=meta.title)
        ttk.Label(frm, text="제목").grid(row=row, column=0, sticky="w", pady=(10, 2))
        self.title_combo = ttk.Combobox(frm, textvariable=self.title_var, values=titles, width=60)
        self.title_combo.grid(row=row, column=1, sticky="ew", pady=(10, 2))
        ttk.Button(frm, text="복사", command=lambda: self.copy(self.title_var.get(), "제목")).grid(
            row=row, column=2, padx=(6, 0), pady=(10, 2))
        row += 1
        self.why_var = tk.StringVar()
        ttk.Label(frm, textvariable=self.why_var, foreground="#777", wraplength=560, justify="left").grid(
            row=row, column=1, sticky="w")
        row += 1

        def show_why(*_):
            cur = self.title_var.get()
            t = next((t for t in meta.titles if t["title"] == cur), None)
            star = "⭐ 추천 · " if titles and cur == meta.title else ""
            self.why_var.set(f"{star}{len(cur)}자" + (f" · {t['why']}" if t and t.get("why") else "") +
                             (" · 40자가 넘으면 휴대폰에서 잘려요" if len(cur) > 40 else ""))
        self.title_var.trace_add("write", show_why)
        show_why()

        # 설명 (설명 + 해시태그 + 출처)
        ttk.Label(frm, text="설명").grid(row=row, column=0, sticky="nw", pady=(8, 2))
        self.desc = tk.Text(frm, width=64, height=9, wrap="word")
        self.desc.insert("1.0", res.description)
        self.desc.grid(row=row, column=1, sticky="nsew", pady=(8, 2))
        frm.rowconfigure(row, weight=1)
        ttk.Button(frm, text="복사", command=lambda: self.copy(self.desc.get("1.0", "end-1c"), "설명")).grid(
            row=row, column=2, sticky="n", padx=(6, 0), pady=(8, 2))
        row += 1
        ttk.Label(frm, text="해시태그 · 출처까지 들어 있어요. 통째로 설명란에 붙여넣으면 됩니다.",
                  foreground="#777").grid(row=row, column=1, sticky="w")
        row += 1

        # 태그
        self.tags_var = tk.StringVar(value=", ".join(meta.tags))
        ttk.Label(frm, text="태그").grid(row=row, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(frm, textvariable=self.tags_var).grid(row=row, column=1, sticky="ew", pady=(8, 2))
        ttk.Button(frm, text="복사", command=lambda: self.copy(self.tags_var.get(), "태그")).grid(
            row=row, column=2, padx=(6, 0), pady=(8, 2))
        row += 1
        ttk.Label(frm, text="PC 스튜디오 → 세부정보 → 더보기 → 태그 칸 (휴대폰 앱엔 없음)", foreground="#777").grid(
            row=row, column=1, sticky="w")
        row += 1

        # 카테고리 · 음악 · 체크리스트
        info = [f"카테고리: {category_label(meta.category_id)}" +
                (f" — {meta.category_reason}" if meta.category_reason else ""),
                "   " + CATEGORY_NOTE]
        if getattr(res, "bgm_note", ""):
            info.append("배경음악: " + res.bgm_note.replace("\n", " "))
        if meta.upload_times:
            info.append("추천 업로드 시간: " + " · ".join(meta.upload_times) + " (요즘 잘 뜬 영상들이 올라온 시간)")
        info += ["체크: AI 이미지가 들어갔다면 '변경되거나 합성된 콘텐츠'를 '예'로 표시하세요."]
        if meta.source == "fallback":
            info.append("(Ollama 응답이 없어 기본값으로 채웠어요. 제목/설명을 다듬어 주세요.)")
        ttk.Label(frm, text="\n".join(info), wraplength=640, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(10, 4))
        row += 1

        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(btns, textvariable=self.copied_var, foreground="#1a7f37").pack(side="left")
        ttk.Button(btns, text="닫기", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="🌐 YouTube 스튜디오", command=self.open_studio).pack(side="right", padx=4)
        ttk.Button(btns, text="📂 영상 폴더", command=self.open_folder).pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())

    def copy(self, text: str, what: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.copied_var.set(f"✅ {what} 복사됨 — 붙여넣기(Ctrl+V) 하세요")

    def open_folder(self):
        try:
            open_path(self.res.video_path, select=True)
        except OSError:
            pass

    @staticmethod
    def open_studio():
        import webbrowser

        webbrowser.open(STUDIO_URL)


# ---- 설정 창 ----------------------------------------------------------------------

class SetupDialog(tk.Toplevel):
    """첫 실행/설정 변경 창. 저장하면 config.yaml을 쓰고 self.saved=True."""

    def __init__(self, master, config_path: Path, first_run: bool = False):
        super().__init__(master)
        self.config_path = Path(config_path)
        self.raw = read_raw(self.config_path)
        self.saved = False
        self.title("처음 설정" if first_run else "설정")
        self.resizable(False, False)
        self.transient(master)

        frm = ttk.Frame(self, padding=14)
        frm.grid(sticky="nsew")
        if first_run:
            ttk.Label(frm, text="처음 실행입니다. 아래 정보를 입력하면 config.yaml이 만들어집니다.",
                      foreground="#555").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        r = self.raw
        self.vars: dict[str, tk.Variable] = {
            "api_key": tk.StringVar(value=r["youtube"].get("api_key") or ""),
            "bot_token": tk.StringVar(value=r["telegram"].get("bot_token") or ""),
            "pexels_key": tk.StringVar(value=r["pexels"].get("api_key") or ""),
            "pixabay_key": tk.StringVar(value=r["pixabay"].get("api_key") or ""),
            "ai_enabled": tk.BooleanVar(value=bool(r["ai_images"].get("enabled"))),
            "ai_host": tk.StringVar(value=r["ai_images"].get("host") or "http://127.0.0.1:8188"),
            "comfy_dir": tk.StringVar(value=r["ai_images"].get("comfy_dir") or ""),
            "chat_id": tk.StringVar(value=str(r["telegram"].get("chat_id") or "")),
            "interval": tk.StringVar(value=str(r["schedule"].get("interval_hours") or 6)),
            "model": tk.StringVar(value=r["ollama"]["model"]),
            "host": tk.StringVar(value=r["ollama"]["host"]),
            "threshold": tk.StringVar(value=f"{r['analysis']['drop_threshold_pct']:g}"),
            "auto_gen": tk.BooleanVar(value=bool(r["ollama"].get("auto_generate_on_slowdown", True))),
            "language": tk.StringVar(value=LANGUAGE_LABELS.get(r.get("language", "en"), LANGUAGE_LABELS["en"])),
        }
        row = 1

        def field(label, key, width=48, hint=None, show=None):
            nonlocal row
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=3)
            e = ttk.Entry(frm, textvariable=self.vars[key], width=width, show=show or "")
            e.grid(row=row, column=1, sticky="w", pady=3)
            if hint:
                ttk.Label(frm, text=hint, foreground="#777").grid(row=row, column=2, sticky="w", padx=6)
            row += 1
            return e

        ttk.Label(frm, text="콘텐츠 언어/시장").grid(row=row, column=0, sticky="w", pady=3)
        ttk.Combobox(frm, textvariable=self.vars["language"], values=list(LANGUAGE_LABELS.values()),
                     state="readonly", width=20).grid(row=row, column=1, sticky="w", pady=3)
        ttk.Label(frm, text="유행 분석 지역 · 대본 언어 · 음성 · 자막 폰트가 함께 바뀜",
                  foreground="#777").grid(row=row, column=2, sticky="w", padx=6)
        row += 1
        field("YouTube API 키 *", "api_key")
        ttk.Label(frm, text="채널 목록 *").grid(row=row, column=0, sticky="nw", pady=3)
        self.channels_text = tk.Text(frm, width=48, height=5)
        self.channels_text.insert("1.0", channel_lines(r.get("channels")))
        self.channels_text.grid(row=row, column=1, sticky="w", pady=3)
        ttk.Label(frm, text="한 줄에 하나\n@핸들 · UC채널ID · 채널 주소\n'| 이름'으로 표시 이름 지정",
                  foreground="#777", justify="left").grid(row=row, column=2, sticky="nw", padx=6)
        row += 1
        field("텔레그램 봇 토큰", "bot_token", hint="@BotFather 에서 발급")
        field("텔레그램 chat_id", "chat_id", width=20)
        ttk.Button(frm, text="chat_id 찾기", command=self._find_chat_id).grid(
            row=row - 1, column=1, sticky="e")
        field("Pexels API 키", "pexels_key", hint="영상 생성용 (무료)")
        ttk.Button(frm, text="발급 페이지 열기", command=self._open_pexels).grid(
            row=row - 1, column=2, sticky="e", padx=6)
        field("Pixabay API 키", "pixabay_key", hint="선택 · 영상 소스 추가 (무료)")
        ttk.Button(frm, text="발급 페이지 열기", command=self._open_pixabay).grid(
            row=row - 1, column=2, sticky="e", padx=6)
        ttk.Checkbutton(frm, text="AI 이미지 생성 (ComfyUI · 그래픽카드 필요)", variable=self.vars["ai_enabled"]).grid(
            row=row, column=1, sticky="w", pady=3)
        ttk.Button(frm, text="연결 확인", command=self._check_comfy).grid(row=row, column=2, sticky="e", padx=6)
        row += 1
        field("ComfyUI 주소", "ai_host", width=32, hint="첫 씬(훅)과 영상 없는 씬에 AI 이미지")
        field("ComfyUI 폴더", "comfy_dir", hint="비우면 자동으로 찾음 · 영상 만들 때 자동 실행/종료")
        ttk.Button(frm, text="폴더 선택", command=self._pick_comfy_dir).grid(
            row=row - 1, column=2, sticky="e", padx=6)
        field("체크 주기(시간)", "interval", width=8)
        field("하락 임계값(%)", "threshold", width=8, hint="최근 영상이 이전보다 이만큼 떨어지면 알림")
        field("Ollama 모델", "model", width=24, hint="기본 qwen2.5:7b")
        field("Ollama 주소", "host", width=32)
        ttk.Checkbutton(frm, text="하락 감지 시 주제/대본 자동 생성", variable=self.vars["auto_gen"]).grid(
            row=row, column=1, sticky="w", pady=(6, 0))
        row += 1

        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="저장", command=self._save).pack(side="right", padx=4)
        ttk.Button(btns, text="취소", command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()

    def _open_pexels(self):
        import webbrowser

        from .video.pexels import KEY_HELP, SIGNUP_URL

        webbrowser.open(SIGNUP_URL)
        messagebox.showinfo("Pexels API 키", KEY_HELP, parent=self)

    def _open_pixabay(self):
        import webbrowser

        from .video.pixabay import KEY_HELP, SIGNUP_URL

        webbrowser.open(SIGNUP_URL)
        messagebox.showinfo("Pixabay API 키", KEY_HELP, parent=self)

    def _pick_comfy_dir(self):
        path = filedialog.askdirectory(parent=self, title="ComfyUI 폴더 (run_nvidia_gpu.bat 이 있는 폴더)")
        if path:
            self.vars["comfy_dir"].set(path)

    def _check_comfy(self):
        from .video.comfy_launcher import diagnose, find_comfy_dir

        ai = dict(self.raw["ai_images"], host=self.vars["ai_host"].get().strip() or "http://127.0.0.1:8188",
                  comfy_dir=self.vars["comfy_dir"].get().strip())
        ok, text = diagnose(ai)
        if not ai["comfy_dir"]:                     # 자동으로 찾은 폴더를 칸에 채워 준다
            found = find_comfy_dir(None)
            if found:
                self.vars["comfy_dir"].set(str(found))
        (messagebox.showinfo if ok else messagebox.showwarning)("ComfyUI", text, parent=self)

    def _find_chat_id(self):
        token = self.vars["bot_token"].get().strip()
        if not token:
            messagebox.showinfo("chat_id 찾기", "봇 토큰을 먼저 입력하세요.", parent=self)
            return
        try:
            from .notifier import fetch_chat_ids

            chats = fetch_chat_ids(token)
        except Exception as exc:
            messagebox.showerror("chat_id 찾기", f"조회 실패: {exc}", parent=self)
            return
        if not chats:
            messagebox.showinfo("chat_id 찾기", "텔레그램에서 봇에게 아무 메시지(예: /start)를 "
                                "보낸 뒤 다시 눌러주세요.", parent=self)
            return
        self.vars["chat_id"].set(chats[-1][0])
        if len(chats) > 1:
            messagebox.showinfo("chat_id 찾기", "여러 대화가 있습니다:\n" +
                                "\n".join(f"{cid}  {name}" for cid, name in chats), parent=self)

    def _save(self):
        v = {k: var.get() for k, var in self.vars.items()}
        channels = parse_channel_lines(self.channels_text.get("1.0", "end"))
        errors = []
        if not str(v["api_key"]).strip():
            errors.append("YouTube API 키를 입력하세요.")
        if not channels:
            errors.append("채널을 최소 1개 입력하세요.")
        try:
            interval = float(v["interval"])
            threshold = float(v["threshold"])
            if interval <= 0 or not 0 < threshold < 100:
                raise ValueError
        except ValueError:
            errors.append("체크 주기는 0보다 큰 숫자, 임계값은 0~100 사이 숫자여야 합니다.")
        if errors:
            messagebox.showerror("입력 확인", "\n".join(errors), parent=self)
            return

        # 기존 채널별 세부 설정(analysis 덮어쓰기 등)은 유지
        old = {(c.get("id") or c.get("handle")): c for c in self.raw.get("channels") or []
               if isinstance(c, dict)}
        merged = []
        for c in channels:
            prev = dict(old.get(c.get("id") or c.get("handle"), {}))
            prev.pop("name", None)
            prev.update(c)
            merged.append(prev)

        r = self.raw
        lang = next((k for k, label in LANGUAGE_LABELS.items() if label == v["language"]), "en")
        if lang != r.get("language"):
            apply_language(r, lang)
        r["channels"] = merged
        r["youtube"]["api_key"] = v["api_key"].strip()
        r["telegram"]["bot_token"] = v["bot_token"].strip()
        r["telegram"]["chat_id"] = v["chat_id"].strip()
        r["pexels"]["api_key"] = v["pexels_key"].strip()
        r["pixabay"]["api_key"] = v["pixabay_key"].strip()
        r["ai_images"]["enabled"] = bool(v["ai_enabled"])
        r["ai_images"]["host"] = v["ai_host"].strip() or "http://127.0.0.1:8188"
        r["ai_images"]["comfy_dir"] = v["comfy_dir"].strip()
        r["schedule"]["interval_hours"] = interval
        r["analysis"]["drop_threshold_pct"] = threshold
        r["ollama"]["model"] = v["model"].strip() or "qwen2.5:7b"
        r["ollama"]["host"] = v["host"].strip() or "http://localhost:11434"
        r["ollama"]["auto_generate_on_slowdown"] = bool(v["auto_gen"])
        save_config(r, self.config_path)
        self.saved = True
        self.destroy()


# ---- 메인 창 ------------------------------------------------------------------------

class App:
    COLUMNS = (("status", "상태", 110), ("subs", "구독자", 80), ("change", "최근/이전", 80),
               ("weekly", "최근 7일 조회수", 110), ("trend", "14일 추이", 130), ("checked", "마지막 체크", 110))

    def __init__(self, root: tk.Tk, config_path: Path, *, check_ollama_on_start: bool = True):
        self.root = root
        self.config_path = Path(config_path)
        self.cfg: Config | None = None
        self.tz = ZoneInfo("Asia/Seoul")
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.busy = False
        self.check_lock = threading.Lock()
        self.cancel_event: threading.Event | None = None
        self.scheduler = None
        self.generation = None
        self.analyses: dict[str, ChannelAnalysis] = {}
        self.channel_ids: dict[str, str | None] = {}   # 트리 아이템 → channel_id
        self.ollama_ok = False
        self.closed = False
        self._gains: dict[str, list[float]] = {}
        self.video_result = None
        # 테스트에서 가짜 Pexels/TTS를 넣을 수 있게 파이프라인 생성을 함수로 둔다
        self.video_pipeline_factory: Callable = self._default_video_pipeline

        root.title(APP_TITLE)
        root.geometry("1000x700")
        root.minsize(820, 560)
        self._fonts()
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self._drain)
        if self._load_or_setup():
            self.refresh_channels()
            self._apply_auto_check(self.cfg.raw.get("gui", {}).get("auto_check", False))
            self._apply_language_ui()
            if check_ollama_on_start:
                self.check_ollama(startup=True)

    # ---- 기본 틀 ---------------------------------------------------------------

    def _fonts(self):
        families = set(tkfont.families(self.root))
        family = next((f for f in ("Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo",
                                   "Noto Sans CJK KR", "NanumGothic") if f in families), None)
        if family:
            for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
                tkfont.nametofont(name).configure(family=family, size=10)
        self.text_font = (family or "TkFixedFont", 10)

    def _build(self):
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        self.btn_check = ttk.Button(top, text="▶ 지금 체크하기", command=self.check_now)
        self.btn_check.pack(side="left")
        self.btn_generate = ttk.Button(top, text="✨ 새 주제/대본 생성", command=self.generate_now)
        self.btn_generate.pack(side="left", padx=6)
        self.btn_trend = ttk.Button(top, text="🔥 유행 쇼츠 분석", command=self.trend_now)
        self.btn_trend.pack(side="left")
        self.auto_var = tk.BooleanVar(value=False)
        self.auto_chk = ttk.Checkbutton(top, text="자동 체크", variable=self.auto_var,
                                        command=lambda: self._apply_auto_check(self.auto_var.get(), save=True))
        self.auto_chk.pack(side="left", padx=(12, 0))
        ttk.Button(top, text="⚙ 설정", command=self.open_settings).pack(side="right")
        ttk.Button(top, text="📂 출력 폴더", command=self.open_outputs).pack(side="right", padx=6)
        self.ollama_label = ttk.Label(top, text="Ollama 확인 중…", foreground="#777")
        self.ollama_label.pack(side="right", padx=10)

        one = ttk.Frame(self.root, padding=(10, 0, 10, 6))
        one.pack(fill="x")
        self.btn_oneclick = ttk.Button(one, text="🚀 원클릭 쇼츠 만들기", command=self.one_click)
        self.btn_oneclick.pack(side="left", ipady=4)
        ttk.Label(one, text="주제(선택):").pack(side="left", padx=(12, 4))
        self.oneclick_topic = tk.StringVar()
        topic_entry = ttk.Entry(one, textvariable=self.oneclick_topic, width=36)
        topic_entry.pack(side="left")
        topic_entry.bind("<Return>", lambda _e: self.one_click())
        ttk.Label(one, text="비우면 요즘 유행에서 주제를 골라 → 대본 · 제목 · 영상까지 한 번에",
                  foreground="#777").pack(side="left", padx=8)

        # 상태 표시줄은 먼저 아래에 붙여야 창이 작아져도 가려지지 않는다
        status = ttk.Frame(self.root, padding=(10, 4))
        status.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=140)
        self.progress.pack(side="left")
        self.status_var = tk.StringVar(value="준비")
        ttk.Label(status, textvariable=self.status_var).pack(side="left", padx=8)

        paned = ttk.PanedWindow(self.root, orient="vertical")
        paned.pack(fill="both", expand=True, padx=10)

        tree_frame = ttk.Frame(paned)
        self.tree = ttk.Treeview(tree_frame, columns=[c[0] for c in self.COLUMNS], height=5)
        self.tree.heading("#0", text="채널")
        self.tree.column("#0", width=180)
        for key, label, width in self.COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected_summary())
        paned.add(tree_frame, weight=1)

        self.nb = ttk.Notebook(paned)
        self.summary_box = scrolledtext.ScrolledText(self.nb, wrap="none", font=self.text_font)
        self.summary_box.configure(state="disabled")
        self.nb.add(self.summary_box, text="조회수 추이")
        self._build_trend_tab()

        gen_tab = ttk.Frame(self.nb)
        self.gen_tab = gen_tab
        bar = ttk.Frame(gen_tab, padding=(0, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="주제:").pack(side="left")
        self.topic_combo = ttk.Combobox(bar, state="readonly", width=48)
        self.topic_combo.pack(side="left", padx=4)
        self.btn_rescript = ttk.Button(bar, text="선택한 주제로 대본 생성", command=self.rewrite_script)
        self.btn_rescript.pack(side="left")
        self.btn_cancel = ttk.Button(bar, text="취소", command=self.cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=4)
        ttk.Button(bar, text="💾 파일로 저장", command=self.save_result).pack(side="right")
        ttk.Button(bar, text="🎬 이 대본으로 영상 만들기", command=self.send_to_video).pack(side="right", padx=4)
        self.result_box = scrolledtext.ScrolledText(gen_tab, wrap="word", font=self.text_font, undo=True)
        self.result_box.pack(fill="both", expand=True)
        self.nb.add(gen_tab, text="주제/대본")
        self._build_video_tab()
        paned.add(self.nb, weight=3)

    def _build_trend_tab(self):
        tab = ttk.Frame(self.nb)
        self.trend_tab = tab
        bar = ttk.Frame(tab, padding=(0, 6))
        bar.pack(fill="x")
        self.btn_trend2 = ttk.Button(bar, text="🔥 유행 쇼츠 분석 시작", command=self.trend_now)
        self.btn_trend2.pack(side="left")
        self.trend_auto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="대본 생성 후 영상까지 자동으로 만들기", variable=self.trend_auto_var,
                        command=self._save_trend_auto).pack(side="left", padx=10)
        self.trend_hint = ttk.Label(bar, foreground="#777")
        self.trend_hint.pack(side="left")
        self.trend_box = scrolledtext.ScrolledText(tab, wrap="none", font=self.text_font)
        self.trend_box.insert("1.0", "[🔥 유행 쇼츠 분석]을 누르면 최근 유행하는 쇼츠 목록이 여기에 표시됩니다.\n"
                              "(YouTube API 쿼터를 1회 약 200 사용합니다. 무료 쿼터는 하루 10,000)")
        self.trend_box.configure(state="disabled")
        self.trend_box.pack(fill="both", expand=True)
        self.nb.add(tab, text="🔥 트렌드")

    def _build_video_tab(self):
        from .video.tts import VOICES

        tab = ttk.Frame(self.nb)
        self.video_tab = tab
        row1 = ttk.Frame(tab, padding=(0, 6))
        row1.pack(fill="x")
        ttk.Label(row1, text="제목(파일명):").pack(side="left")
        self.video_title_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.video_title_var, width=34).pack(side="left", padx=4)
        ttk.Label(row1, text="음성:").pack(side="left", padx=(8, 0))
        self.voice_var = tk.StringVar(value=AUTO_VOICE)
        self.voice_combo = ttk.Combobox(row1, textvariable=self.voice_var, width=30, state="readonly",
                                        values=[AUTO_VOICE] + [f"{k}  {v}" for k, v in VOICES.items()])
        self.voice_combo.pack(side="left", padx=4)
        ttk.Button(row1, text="📥 주제/대본 탭에서 가져오기", command=self.import_script).pack(side="right")
        row_hook = ttk.Frame(tab)
        row_hook.pack(fill="x")
        ttk.Label(row_hook, text="첫 화면 훅 문구:").pack(side="left")
        self.hook_var = tk.StringVar()
        ttk.Entry(row_hook, textvariable=self.hook_var, width=50).pack(side="left", padx=4)
        ttk.Label(row_hook, text="처음 2.5초 동안 크게 표시 (비우면 제목, '-' 이면 표시 안 함)",
                  foreground="#777").pack(side="left")

        # 아래쪽 줄(진행/로그/결과)을 먼저 붙여야 창이 작아도 결과 경로와 버튼이 가려지지 않는다
        row3 = ttk.Frame(tab, padding=(0, 6))
        row3.pack(side="bottom", fill="x")
        ttk.Label(row3, text="결과:").pack(side="left")
        self.video_path_var = tk.StringVar(value="-")
        ttk.Entry(row3, textvariable=self.video_path_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=4)
        self.btn_upload_info = ttk.Button(row3, text="📋 업로드 정보", state="disabled",
                                          command=self._open_upload_info)
        self.btn_upload_info.pack(side="right")
        ttk.Button(row3, text="🎵 BGM 폴더", command=self._open_bgm_dir).pack(side="right")
        self.btn_open_video = ttk.Button(row3, text="▶ 영상 열기", state="disabled",
                                         command=lambda: self._open_result(select=False))
        self.btn_open_video.pack(side="right")
        self.btn_open_folder = ttk.Button(row3, text="📂 폴더 열기", state="disabled",
                                          command=lambda: self._open_result(select=True))
        self.btn_open_folder.pack(side="right", padx=4)

        self.video_log = scrolledtext.ScrolledText(tab, wrap="word", font=self.text_font, height=5)
        self.video_log.configure(state="disabled")
        self.video_log.pack(side="bottom", fill="x")

        row2 = ttk.Frame(tab, padding=(0, 6))
        row2.pack(side="bottom", fill="x")
        self.btn_video = ttk.Button(row2, text="🎬 영상 만들기", command=self.make_video)
        self.btn_video.pack(side="left")
        self.btn_video_cancel = ttk.Button(row2, text="취소", command=self.cancel, state="disabled")
        self.btn_video_cancel.pack(side="left", padx=4)
        self.video_progress = ttk.Progressbar(row2, mode="determinate", maximum=6, length=160)
        self.video_progress.pack(side="left", padx=8)
        self.video_step_var = tk.StringVar(value="대본을 넣고 [영상 만들기]를 누르세요.")
        ttk.Label(row2, textvariable=self.video_step_var).pack(side="left")

        ttk.Label(tab, text="대본 (한 줄에 한 문장 권장. [효과음], (화면 전환), 이모지, 마크다운은 자동으로 빠집니다)",
                  foreground="#777").pack(anchor="w")
        self.video_script = scrolledtext.ScrolledText(tab, wrap="word", font=self.text_font, height=6, undo=True)
        self.video_script.pack(fill="both", expand=True)

        self.nb.add(tab, text="영상 생성")


    # ---- 스레드 도우미 ------------------------------------------------------------

    def ui(self, fn: Callable[[], None]) -> None:
        """다른 스레드에서 화면을 바꿀 때 사용 (Tk는 메인 스레드에서만 조작 가능)."""
        self._queue.put(fn)

    def _drain(self):
        try:
            while True:
                self._queue.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            log.exception("화면 갱신 실패")
        if not self.closed:
            self.root.after(100, self._drain)

    def set_status(self, text: str) -> None:
        self.ui(lambda: self.status_var.set(text))

    def _set_busy(self, busy: bool, text: str | None = None, cancellable: bool = False):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_check, self.btn_generate, self.btn_rescript, self.btn_video, self.btn_trend,
                  self.btn_trend2, self.btn_oneclick):
            b.configure(state=state)
        for b in (self.btn_cancel, self.btn_video_cancel):
            b.configure(state="normal" if busy and cancellable else "disabled")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()
        if text:
            self.status_var.set(text)

    def run_bg(self, text: str, work: Callable[[], object], done: Callable[[object], None] | None = None,
               cancellable: bool = False) -> threading.Thread | None:
        if self.busy:
            messagebox.showinfo(APP_TITLE, "다른 작업이 진행 중입니다. 끝난 뒤 다시 시도하세요.")
            return None
        self._set_busy(True, text, cancellable)

        def target():
            try:
                result = work()
            except Exception as exc:
                from .ollama_client import GenerationCancelled
                from .video.ffmpeg import Cancelled

                if isinstance(exc, (GenerationCancelled, Cancelled)):
                    self.ui(lambda: self._set_busy(False, "작업을 취소했습니다."))
                    return
                from .pipeline import redact_secrets

                log.exception("작업 실패: %s", text)
                msg = f"{type(exc).__name__}: {exc}" if not str(exc).startswith(("Ollama", "모델")) else str(exc)
                msg = redact_secrets(msg)
                self.ui(lambda: (self._set_busy(False, "오류"), messagebox.showerror(APP_TITLE, msg)))
                return
            self.ui(lambda: (self._set_busy(False), done(result) if done else None))

        t = threading.Thread(target=target, daemon=True)
        t.start()
        return t

    # ---- 설정 ----------------------------------------------------------------

    def _load_or_setup(self) -> bool:
        first = not self.config_path.exists()
        while True:
            if not first:
                try:
                    self.cfg = load_config(self.config_path)
                    self.tz = ZoneInfo(self.cfg.schedule["timezone"])
                    self._ensure_prompts()
                    return True
                except ConfigError as exc:
                    messagebox.showwarning(APP_TITLE, f"설정을 확인해주세요.\n\n{exc}")
            dlg = SetupDialog(self.root, self.config_path, first_run=first)
            self.root.wait_window(dlg)
            if not dlg.saved:
                if self.cfg is None:
                    self.closed = True
                    self.root.after(0, self.root.destroy)
                    return False
                return True
            first = False

    def _ensure_prompts(self):
        from .generator import ensure_prompts

        ensure_prompts(self.cfg)

    def open_settings(self):
        dlg = SetupDialog(self.root, self.config_path)
        self.root.wait_window(dlg)
        if dlg.saved:
            self.cfg = load_config(self.config_path)
            self._apply_language_ui()
            self._apply_auto_check(self.auto_var.get())   # 주기가 바뀌었을 수 있음
            self.refresh_channels()
            self.check_ollama()

    # ---- Ollama -----------------------------------------------------------------

    def check_ollama(self, startup: bool = False):
        from .ollama_client import OllamaClient

        o = self.cfg.ollama

        def work():
            st = OllamaClient(o["host"], o["model"]).status()
            self.ui(lambda: self._on_ollama_status(st, startup))

        threading.Thread(target=work, daemon=True).start()

    def _on_ollama_status(self, st, startup: bool):
        self.ollama_ok = st.ok
        if st.ok:
            self.ollama_label.configure(text=f"● Ollama 준비됨 ({st.model})", foreground="#1a7f37")
        elif st.server_up:
            self.ollama_label.configure(text=f"● 모델 없음 ({st.model})", foreground="#c62828")
            messagebox.showerror(APP_TITLE, st.message + "\n\n모델을 받은 뒤 프로그램을 다시 실행하세요.")
            if startup:
                self.close()
        else:
            self.ollama_label.configure(text="● Ollama 꺼짐", foreground="#c62828")
            if startup:
                messagebox.showwarning(APP_TITLE, st.message + "\n\n조회수 체크는 계속 사용할 수 있고, "
                                       "주제/대본 생성은 Ollama를 켠 뒤 사용할 수 있습니다.")

    # ---- 채널 현황 ------------------------------------------------------------------

    def refresh_channels(self):
        """DB 데이터로 채널 현황을 다시 계산 (API 호출 없음)."""
        cfg = self.cfg

        def work():
            rows = []
            now = utcnow()
            with Database(cfg.db_path) as db:
                for ch in cfg.channels:
                    cid = ch.id
                    if not cid:
                        row = db.find_channel_by_handle(ch.handle)
                        cid = row["channel_id"] if row else None
                    a = gains = checked = None
                    if cid and db.get_channel(cid):
                        a = analyze_channel(db, cid, ch.analysis, now, self.tz,
                                            max_videos=cfg.youtube["max_videos_per_channel"])
                        gains = daily_view_gains(db, cid)
                        stats = db.get_channel_stats(cid)
                        checked = from_iso(stats[-1]["collected_at"]) if stats else None
                    rows.append((ch, cid, a, gains, checked))
            self.ui(lambda: self._fill_tree(rows))

        threading.Thread(target=work, daemon=True).start()

    def _fill_tree(self, rows):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.analyses.clear()
        self.channel_ids.clear()
        self._gains = {}
        for i, (ch, cid, a, gains, checked) in enumerate(rows):
            iid = f"ch{i}"
            self.channel_ids[iid] = cid
            if a is None:
                values = ("아직 수집 전", "-", "-", "-", "", "-")
                label = ch.label
            else:
                self.analyses[iid] = a
                self._gains[iid] = gains or []
                values = (status_text(a), fnum(a.subscribers), fpct(a.change_pct),
                          fnum(a.weekly_views), sparkline(gains or []),
                          checked.astimezone(self.tz).strftime("%m-%d %H:%M") if checked else "-")
                label = ch.name or a.channel_title
            self.tree.insert("", "end", iid=iid, text=label, values=values)
        keep = [s for s in selected if self.tree.exists(s)]
        if keep or self.tree.get_children():
            self.tree.selection_set(keep or self.tree.get_children()[0])
        self._show_selected_summary()

    def _selected_iid(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _show_selected_summary(self):
        iid = self._selected_iid()
        a = self.analyses.get(iid) if iid else None
        if a is not None:
            text = summary_text(a, self.tz, self._gains.get(iid, []))
        else:
            text = "아직 수집된 데이터가 없습니다. [▶ 지금 체크하기]를 눌러 수집하세요."
        self.summary_box.configure(state="normal")
        self.summary_box.delete("1.0", "end")
        self.summary_box.insert("1.0", text)
        self.summary_box.configure(state="disabled")

    # ---- 지금 체크 / 자동 체크 ----------------------------------------------------------

    def _do_check(self, trigger: str):
        from .pipeline import run_check

        with self.check_lock:
            self.set_status("YouTube에서 조회수를 가져오는 중…" if trigger == "manual"
                            else "자동 체크 실행 중…")
            return run_check(self.cfg)

    def check_now(self):
        self.run_bg("YouTube에서 조회수를 가져오는 중…", lambda: self._do_check("manual"),
                    self._on_check_done)

    def _on_check_done(self, results):
        errors = [r for r in results if r.error]
        slow = [r for r in results if r.analysis and r.analysis.slowdown]
        gens = [r.generation for r in results if r.generation]
        msg = f"체크 완료 {utcnow().astimezone(self.tz):%H:%M} — {len(results)}개 채널"
        if slow:
            msg += f", 🚨 둔화 {len(slow)}개"
        if gens:
            msg += f", ✨ 주제/대본 {len(gens)}건 자동 생성"
        self.status_var.set(msg)
        self.refresh_channels()
        if gens:
            self.show_generation(gens[-1])
        if errors:
            messagebox.showwarning(APP_TITLE, "일부 채널 처리 실패:\n\n" +
                                   "\n".join(f"· {r.channel.label}: {r.error}" for r in errors))

    def _scheduled_job(self):
        if not self.check_lock.acquire(blocking=False):
            self.set_status("다른 작업이 진행 중이라 이번 자동 체크는 건너뜁니다.")
            return
        self.check_lock.release()
        try:
            results = self._do_check("auto")
            self.ui(lambda: self._on_check_done(results))
        except Exception as exc:
            log.exception("자동 체크 실패")
            self.set_status(f"자동 체크 실패: {exc}")

    def _apply_auto_check(self, on: bool, save: bool = False):
        from .scheduler import build_scheduler, describe_schedule

        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None
        self.auto_var.set(on)
        desc = describe_schedule(self.cfg)
        self.auto_chk.configure(text=f"자동 체크 ({desc})")
        if on:
            self.scheduler, _ = build_scheduler(self.cfg, blocking=False, job=self._scheduled_job)
            self.scheduler.start()
            job = self.scheduler.get_jobs()[0]
            nxt = job.next_run_time.astimezone(self.tz).strftime("%m-%d %H:%M") if job.next_run_time else "-"
            self.status_var.set(f"자동 체크 켜짐 — 다음 체크 {nxt}")
        elif save:
            self.status_var.set("자동 체크 꺼짐")
        if save:
            raw = read_raw(self.config_path)
            raw.setdefault("gui", {})["auto_check"] = bool(on)
            save_config(raw, self.config_path)

    # ---- 주제/대본 생성 ---------------------------------------------------------------

    def _stream_start(self, header: str):
        self.nb.select(self.gen_tab)
        self.result_box.delete("1.0", "end")
        self.result_box.insert("end", header + "\n\n")

    def _on_token(self, token: str):
        def append():
            self.result_box.insert("end", token)
            self.result_box.see("end")
        self.ui(append)

    def _on_gen_status(self, msg: str):
        self.set_status(msg)
        self.ui(lambda: (self.result_box.insert("end", f"\n\n▶ {msg}\n"), self.result_box.see("end")))

    def generate_now(self):
        iid = self._selected_iid()
        cid = self.channel_ids.get(iid) if iid else None
        if not cid:
            messagebox.showinfo(APP_TITLE, "채널을 선택하세요. 수집 전인 채널은 먼저 [지금 체크하기]를 실행하세요.")
            return
        from .generator import generate_for_channel
        from .ollama_client import OllamaClient

        o = self.cfg.ollama
        client = OllamaClient(o["host"], o["model"], timeout=o["timeout_sec"])
        st = client.status()
        if not st.ok:
            self._on_ollama_status(st, startup=False)
            if not st.server_up:
                messagebox.showerror(APP_TITLE, st.message)
            return
        self.cancel_event = threading.Event()
        cancel = self.cancel_event
        self._stream_start(f"[{self.tree.item(iid, 'text')}] {o['model']}로 생성 중… "
                           "(PC 사양에 따라 수 분 걸릴 수 있습니다)")
        self.run_bg("주제/대본 생성 중…",
                    lambda: generate_for_channel(self.cfg, cid, trigger="manual",
                                                 on_status=self._on_gen_status,
                                                 on_token=self._on_token, cancel=cancel),
                    self.show_generation, cancellable=True)

    def rewrite_script(self):
        g = self.generation
        idx = self.topic_combo.current()
        if g is None or idx < 0:
            messagebox.showinfo(APP_TITLE, "먼저 [새 주제/대본 생성]으로 주제 후보를 만드세요.")
            return
        from .db import Database
        from .generator import ScriptGenerator, record_generation, save_generation

        gen = ScriptGenerator.from_config(self.cfg)
        self.cancel_event = threading.Event()
        cancel = self.cancel_event
        self._stream_start(f"주제 {idx + 1}로 대본 다시 쓰는 중…")

        def work():
            gen.write_script(g, idx, on_status=self._on_gen_status, on_token=self._on_token, cancel=cancel)
            g.output_path = g.script_path = None
            save_generation(g, self.cfg.outputs_dir, self.tz)
            with Database(self.cfg.db_path) as db:
                record_generation(db, g)
            return g

        self.run_bg("대본 생성 중…", work, self.show_generation, cancellable=True)

    def cancel(self):
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.status_var.set("취소 요청됨 — 현재 응답을 정리하는 중…")

    def show_generation(self, g):
        from .generator import render_generation_md

        self.generation = g
        self.nb.select(self.gen_tab)
        self.result_box.delete("1.0", "end")
        self.result_box.insert("1.0", render_generation_md(g, self.tz))
        self.result_box.see("1.0")
        self.topic_combo.configure(values=[f"{i + 1}. {t['topic']}" for i, t in enumerate(g.topics)])
        if g.selected is not None:
            self.topic_combo.current(g.selected)
        if g.script_lines and not self.video_script.get("1.0", "end").strip():
            self._fill_video_script(g.script, g.title or "")
        self.status_var.set(f"생성 완료 — 저장됨: {g.output_path}")

    def save_result(self):
        text = self.result_box.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showinfo(APP_TITLE, "저장할 내용이 없습니다.")
            return
        g = self.generation
        initial = g.output_path if g and g.output_path else None
        path = filedialog.asksaveasfilename(
            parent=self.root, title="파일로 저장", defaultextension=".md",
            initialdir=str(initial.parent if initial else self.cfg.outputs_dir),
            initialfile=initial.name if initial else "기획안.md",
            filetypes=[("Markdown", "*.md"), ("텍스트", "*.txt"), ("모든 파일", "*.*")])
        if path:
            Path(path).write_text(text, encoding="utf-8")
            self.status_var.set(f"저장됨: {path}")

    def open_outputs(self):
        out = self.cfg.outputs_dir
        out.mkdir(parents=True, exist_ok=True)
        try:
            open_path(out)
        except OSError:
            messagebox.showinfo(APP_TITLE, f"출력 폴더: {out}")

    # ---- 유행 쇼츠 분석 ------------------------------------------------------------------

    def _apply_language_ui(self):
        t = self.cfg.raw.get("trends", {})
        self.trend_auto_var.set(bool(t.get("auto_video", False)))
        from .trends import region_name

        self.trend_hint.configure(text=f"최근 {t.get('lookback_days', 3)}일 {region_name(t.get('region', 'US'))}에서 조회수가 "
                                       f"빠르게 오른 쇼츠 → 주제 추천 → 쇼츠 대본 "
                                       f"({LANGUAGE_LABELS.get(self.cfg.language, '')})")

    def _save_trend_auto(self):
        raw = read_raw(self.config_path)
        raw.setdefault("trends", {})["auto_video"] = bool(self.trend_auto_var.get())
        save_config(raw, self.config_path)
        self.cfg.raw.setdefault("trends", {})["auto_video"] = bool(self.trend_auto_var.get())

    def _set_trend_text(self, text: str):
        self.trend_box.configure(state="normal")
        self.trend_box.delete("1.0", "end")
        self.trend_box.insert("1.0", text)
        self.trend_box.configure(state="disabled")

    def one_click(self):
        """🚀 원클릭: (주제가 없으면 유행 분석 →) 주제 → 대본 · 업로드 정보 → 영상 → 결과 열기."""
        if self.busy:
            messagebox.showinfo(APP_TITLE, "다른 작업이 진행 중입니다. 끝난 뒤 다시 시도하세요.")
            return
        from .video.ffmpeg import check_ffmpeg
        from .video.pexels import KEY_HELP

        ff = check_ffmpeg(self.cfg.video.get("ffmpeg_path"))
        if not ff.ok:
            messagebox.showerror(APP_TITLE, ff.message)
            return
        stock = self.cfg.secret("pexels", "api_key", required=False) or \
            self.cfg.secret("pixabay", "api_key", required=False)
        if not stock and not messagebox.askyesno(APP_TITLE, KEY_HELP + "\n\n지금은 키 없이 단색 배경으로 만들까요?"):
            return
        self.trend_now(one_click=True, topic=self.oneclick_topic.get().strip())

    def trend_now(self, one_click: bool = False, topic: str = ""):
        """최근 유행 쇼츠 수집 → 주제 추천 → 쇼츠 대본 (→ 체크돼 있거나 원클릭이면 영상까지)."""
        from .ollama_client import OllamaClient
        from .trends import run_topic, run_trends

        self._one_click = one_click
        o = self.cfg.ollama
        st = OllamaClient(o["host"], o["model"]).status()
        if not st.ok:
            self._on_ollama_status(st, startup=False)
            if not st.server_up:
                messagebox.showerror(APP_TITLE, st.message)
            return
        self.cancel_event = threading.Event()
        cancel = self.cancel_event
        status = self._on_gen_status
        prefix = "🚀 원클릭 1/2 — " if one_click else ""
        if topic:
            self._set_trend_text(f"직접 입력한 주제: {topic}")
            self._stream_start(f"{prefix}'{topic}' 주제로 {o['model']}가 쇼츠 대본 · 업로드 정보 작성 중…")
            work = lambda: run_topic(self.cfg, topic, on_status=status, on_token=self._on_token,  # noqa: E731
                                     cancel=cancel)
        else:
            self._set_trend_text("유행 쇼츠 수집 중…")
            self._stream_start(f"{prefix}🔥 최근 유행 쇼츠를 분석해서 {o['model']}로 주제/대본 생성 중… "
                               "(PC 사양에 따라 수 분 걸릴 수 있습니다)")
            # 원클릭은 몇 시간 안에 수집한 결과를 다시 쓴다 (쿼터 0), [🔥 유행 쇼츠 분석]은 항상 새로 수집
            work = lambda: run_trends(self.cfg, on_status=status, on_token=self._on_token,  # noqa: E731
                                      cancel=cancel, use_cache=one_click)
        self.run_bg("🚀 원클릭: 대본 만드는 중…" if one_click else "유행 쇼츠 분석 중…", work,
                    self._on_trend_done, cancellable=True)

    def _on_trend_done(self, res):
        self._set_trend_text(res.table)
        g = res.generation
        self.show_generation(g)
        self._fill_video_script(g.script, g.title or "")
        self.status_var.set(f"유행 쇼츠 {len(res.videos)}개 분석 · 대본 저장됨: {g.output_path} "
                            f"(쿼터 약 {res.units} 사용)")
        one_click, self._one_click = getattr(self, "_one_click", False), False
        if one_click or self.trend_auto_var.get():
            self.nb.select(self.video_tab)
            self.make_video(one_click=one_click)

    # ---- 영상 생성 ----------------------------------------------------------------------

    def _default_video_pipeline(self, cfg):
        from .video.pipeline import VideoPipeline

        return VideoPipeline(cfg)

    def _fill_video_script(self, script: str, title: str):
        self.video_script.delete("1.0", "end")
        self.video_script.insert("1.0", script)
        if title:
            self.video_title_var.set(title)
            self.hook_var.set(title)

    def import_script(self):
        """주제/대본 탭의 결과를 영상 탭으로 가져온다 (생성 결과가 있으면 TTS용으로 정리된 대본)."""
        g = self.generation
        if g is not None and g.script_lines:
            self._fill_video_script(g.script, g.title or (g.selected_topic or {}).get("topic", ""))
            return True
        from .generator import tts_lines

        lines = tts_lines(self.result_box.get("1.0", "end"), self.cfg.language if self.cfg else None)
        if not lines:
            messagebox.showinfo(APP_TITLE, "가져올 대본이 없습니다. [✨ 새 주제/대본 생성]을 먼저 하거나 "
                                "대본을 직접 붙여넣으세요.")
            return False
        self._fill_video_script("\n".join(lines), "")
        return True

    def send_to_video(self):
        if self.import_script():
            self.nb.select(self.video_tab)

    def _video_log(self, msg: str):
        def append():
            self.video_log.configure(state="normal")
            self.video_log.insert("end", msg + "\n")
            self.video_log.see("end")
            self.video_log.configure(state="disabled")
        self.ui(append)

    def _on_video_progress(self, n: int, total: int, msg: str):
        text = f"{n}/{total} {msg}"

        def apply():
            self.video_progress.configure(maximum=total, value=n - 1)
            self.video_step_var.set(text)
            self.status_var.set(text)
        self.ui(apply)
        self._video_log(f"▶ {text}")

    def make_video(self, one_click: bool = False):
        from .video.ffmpeg import check_ffmpeg
        from .video.pexels import KEY_HELP

        script = self.video_script.get("1.0", "end").strip()
        if not script:
            messagebox.showinfo(APP_TITLE, "대본을 입력하거나 [📥 주제/대본 탭에서 가져오기]를 누르세요.")
            return
        ff = check_ffmpeg(self.cfg.video.get("ffmpeg_path"))
        if not ff.ok:
            messagebox.showerror(APP_TITLE, ff.message)
            return
        if not one_click and not self.cfg.secret("pexels", "api_key", required=False):
            if not messagebox.askyesno(APP_TITLE, KEY_HELP + "\n\n지금은 키 없이 단색 배경으로 만들까요?"):
                return
        title = self.video_title_var.get().strip() or "영상"
        choice = self.voice_var.get().strip()
        voice = choice.split()[0] if choice and choice != AUTO_VOICE else None
        hook = self.hook_var.get().strip()
        hook_text = "" if hook == "-" else (hook or None)
        self.cancel_event = threading.Event()
        cancel = self.cancel_event
        self.video_progress.configure(value=0)
        self.video_path_var.set("-")
        self.btn_open_folder.configure(state="disabled")
        self.btn_open_video.configure(state="disabled")
        self.btn_upload_info.configure(state="disabled")
        g = self.generation
        # 주제/대본 단계에서 만든 업로드 정보는 대본을 그대로 쓸 때만 (직접 바꾼 대본이면 영상 만들 때 새로 생성)
        same = g is not None and g.script.strip() == script
        upload = g.upload if same and g.upload else None
        visual_hint = g.visual_hint if same else ""
        self.video_log.configure(state="normal")
        self.video_log.delete("1.0", "end")
        self.video_log.configure(state="disabled")
        pipeline = self.video_pipeline_factory(self.cfg)
        self.run_bg("🚀 원클릭 2/2 — 영상 만드는 중…" if one_click else "영상 생성 중…",
                    lambda: pipeline.run(script, title, voice=voice, hook_text=hook_text, upload_meta=upload,
                                         visual_hint=visual_hint,
                                         on_progress=self._on_video_progress,
                                         on_status=self._video_log, cancel=cancel),
                    lambda res: self._on_video_done(res, one_click=one_click), cancellable=True)

    def _on_video_done(self, res, one_click: bool = False):
        self.video_result = res
        self.video_progress.configure(value=self.video_progress["maximum"])
        self.video_step_var.set(f"✅ 완료 ({res.duration:.1f}초)" +
                                (f" · 분위기 {res.mood} · {res.voice}" if res.mood else ""))
        self.video_path_var.set(str(res.video_path))
        self.btn_open_folder.configure(state="normal")
        self.btn_open_video.configure(state="normal")
        if getattr(res, "upload_path", None):
            self.btn_upload_info.configure(state="normal")
            self._video_log(f"📋 추천 제목: {res.upload_title}  → [📋 업로드 정보]에서 제목·카테고리·설명·해시태그 확인")
        if getattr(res, "bgm", ""):
            self._video_log(f"🎵 배경음악: {res.bgm}")
        self.status_var.set(f"영상 생성 완료 — {res.video_path}")
        if getattr(res, "upload", None) is not None:
            if one_click:
                try:
                    open_path(res.video_path, select=True)
                except OSError:
                    pass
            self._show_upload_dialog(res, res.warnings)
        elif one_click:
            self._one_click_summary(res)
        elif res.warnings:
            messagebox.showwarning(APP_TITLE, "영상은 만들어졌지만 확인할 점이 있습니다:\n\n" +
                                   "\n".join(f"· {w}" for w in res.warnings[:8]))

    def _one_click_summary(self, res):
        """원클릭 완료: 영상이 있는 폴더를 열고(파일 선택), 업로드에 필요한 것만 한눈에."""
        from .upload_meta import UploadMeta, category_label

        try:
            open_path(res.video_path, select=True)
        except OSError:
            pass
        g = self.generation
        meta = UploadMeta.from_dict(g.upload) if g is not None and g.upload else None
        lines = ["✅ 쇼츠 완성!", "", f"파일: {res.video_path.name} ({res.duration:.0f}초)"]
        if res.upload_title:
            lines.append(f"추천 제목: {res.upload_title}")
        if meta:
            lines.append(f"카테고리: {category_label(meta.category_id)}")
            lines.append(f"해시태그: {' '.join(meta.hashtags)}")
        if getattr(res, "bgm", ""):
            lines.append(f"배경음악: {res.bgm}")
        if getattr(res, "upload_path", None):
            lines += ["", "제목 후보 · 설명 · 출처는 영상 옆 '_업로드정보.txt'에 있어요 ([📋 업로드 정보] 버튼)."]
        if res.warnings:
            lines += ["", "참고:"] + [f"· {w}" for w in res.warnings[:6]]
        messagebox.showinfo(APP_TITLE, "\n".join(lines))

    def _open_bgm_dir(self):
        """배경음악 폴더(분위기별 하위 폴더 + 안내 파일)를 만들고 연다."""
        from .video.bgm import README_NAME, ensure_bgm_dirs

        root = ensure_bgm_dirs(self.cfg.bgm_dir)
        try:
            open_path(root)
        except OSError:
            messagebox.showinfo(APP_TITLE, (root / README_NAME).read_text(encoding="utf-8"))

    def _show_upload_dialog(self, res, warnings=None):
        old = getattr(self, "upload_dialog", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        self.upload_dialog = UploadDialog(self.root, res, warnings)

    def _open_upload_info(self):
        if getattr(self.video_result, "upload", None) is not None:
            self._show_upload_dialog(self.video_result)
            return
        path = getattr(self.video_result, "upload_path", None)
        if not path:
            return
        try:
            open_path(path)
        except OSError:
            messagebox.showinfo(APP_TITLE, Path(path).read_text(encoding="utf-8"))

    def _open_result(self, select: bool):
        if self.video_result is None:
            return
        try:
            open_path(self.video_result.video_path, select=select)
        except OSError as exc:
            messagebox.showinfo(APP_TITLE, f"{self.video_result.video_path}\n\n({exc})")

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
        if self.cancel_event is not None:
            self.cancel_event.set()
        self.root.destroy()


def run_gui(config_path: Path | str | None = None) -> int:
    from .paths import default_config_path

    if sys.platform == "win32":
        try:  # 고해상도 모니터에서 글자가 흐릿하지 않게
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    App(root, Path(config_path) if config_path else default_config_path())
    root.mainloop()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run_gui(sys.argv[1] if len(sys.argv) > 1 else None))
