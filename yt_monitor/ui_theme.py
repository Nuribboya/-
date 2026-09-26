"""화면 테마: Windows 11 느낌의 Sun Valley(sv-ttk) 밝은 테마 + 한글 글꼴 + 공통 스타일.

sv-ttk가 없으면(예전 설치) 기본 테마(vista/clam)에 같은 스타일 이름만 맞춰서 그대로 동작한다.
"""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

log = logging.getLogger(__name__)

# 색 (밝은 테마 기준)
COLORS = {
    "bg": "#fafafa",
    "card": "#ffffff",
    "border": "#e5e7eb",
    "text": "#1f2937",
    "muted": "#6b7280",
    "accent": "#005fb8",
    "accent_soft": "#e8f1fb",
    "success": "#15803d",
    "danger": "#b91c1c",
    "warning": "#b45309",
    "text_bg": "#ffffff",
    "select": "#cfe3f7",
    "stripe": "#f5f7fa",
}

KOREAN_FAMILIES = ("Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo", "Noto Sans CJK KR", "NanumGothic",
                   "Noto Sans KR")


def pick_family(root: tk.Misc) -> str | None:
    families = set(tkfont.families(root))
    return next((f for f in KOREAN_FAMILIES if f in families), None)


def apply_theme(root: tk.Tk) -> dict:
    """테마 · 글꼴 · 스타일을 적용하고 {"family", "text", "mono"} 글꼴 정보를 돌려준다."""
    style = ttk.Style(root)
    themed = False
    try:
        import sv_ttk

        sv_ttk.set_theme("light", root)
        themed = True
    except Exception as exc:  # sv-ttk가 없거나 Tcl 오류 → 기본 테마
        log.info("sv-ttk 테마를 쓸 수 없음 (%s) → 기본 테마", exc)
        try:
            style.theme_use("vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            pass

    family = pick_family(root) or "TkDefaultFont"
    # sv-ttk 글꼴(Segoe UI)은 한글이 없어서 한글 글꼴로 바꾼다
    sizes = {"SunValleyCaptionFont": (9, "normal"), "SunValleyBodyFont": (10, "normal"),
             "SunValleyBodyStrongFont": (10, "bold"), "SunValleyBodyLargeFont": (12, "normal"),
             "SunValleySubtitleFont": (13, "bold"), "SunValleyTitleFont": (17, "bold")}
    for name, (size, weight) in sizes.items():
        try:
            tkfont.nametofont(name).configure(family=family, size=size, weight=weight)
        except tk.TclError:
            pass
    for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
        tkfont.nametofont(name).configure(family=family, size=10)

    root.configure(bg=style.lookup("TFrame", "background") or COLORS["bg"])
    _configure_styles(style, family, themed)
    # sv-ttk는 테마가 바뀐 직후(<<ThemeChanged>>) tk_setPalette로 "이미 있는" 위젯에 글자색/배경색을 직접 박아 넣는다.
    # 그러면 Muted/Success 같은 스타일 색이 무시되므로, 그 직후 위젯에 박힌 색을 지워 스타일 색을 되살린다.
    for delay in (50, 400):
        root.after(delay, lambda: clear_palette_colors(root))
    return {"family": family, "text": (family, 10), "mono": (family, 10), "themed": themed}


def clear_palette_colors(widget: tk.Misc) -> None:
    """ttk 위젯에 직접 설정된 foreground/background를 지워 스타일 값을 쓰게 한다 (하위 위젯까지)."""
    try:
        if isinstance(widget, ttk.Widget):          # ttk 위젯만 (Tk 창 · Text 상자는 그대로)
            for opt in ("foreground", "background"):
                try:
                    if str(widget.cget(opt)):
                        widget.configure(**{opt: ""})
                except tk.TclError:
                    pass
        for child in widget.winfo_children():
            clear_palette_colors(child)
    except tk.TclError:
        pass


def _configure_styles(style: ttk.Style, family: str, themed: bool) -> None:
    style.configure("Title.TLabel", font=(family, 16, "bold"), foreground=COLORS["text"])
    style.configure("Subtitle.TLabel", font=(family, 12, "bold"), foreground=COLORS["text"])
    style.configure("Section.TLabel", font=(family, 10, "bold"), foreground=COLORS["text"])
    style.configure("Muted.TLabel", foreground=COLORS["muted"])
    style.configure("Hint.TLabel", foreground=COLORS["muted"], font=(family, 9))
    style.configure("Success.TLabel", foreground=COLORS["success"])
    style.configure("Danger.TLabel", foreground=COLORS["danger"])
    style.configure("Warning.TLabel", foreground=COLORS["warning"])
    style.configure("Treeview", rowheight=30)
    style.configure("Treeview.Heading", font=(family, 10, "bold"))
    style.configure("TNotebook.Tab", padding=(14, 6))
    if not themed:
        # sv-ttk에 있는 스타일 이름을 기본 테마에서도 쓸 수 있게
        style.configure("Card.TFrame", background=COLORS["card"], relief="solid", borderwidth=1)
        style.configure("Accent.TButton", font=(family, 10, "bold"))
    style.configure("Big.Accent.TButton", font=(family, 11, "bold"), padding=(18, 8))
    # 카드 안 글자: 테두리 없이 (sv-ttk 라벨은 옅은 테두리가 보일 때가 있다)
    style.configure("CardTitle.TLabel", font=(family, 12, "bold"), foreground=COLORS["text"], borderwidth=0,
                    relief="flat")
    style.configure("CardMuted.TLabel", foreground=COLORS["muted"], borderwidth=0, relief="flat")


def style_text(widget: tk.Text, fonts: dict | None, *, readonly_bg: bool = False) -> tk.Text:
    """tk.Text / ScrolledText를 테마에 맞게: 흰 배경, 여백, 옅은 테두리."""
    text_font = fonts["text"] if fonts else (tkfont.nametofont("TkDefaultFont").actual("family"), 10)
    widget.configure(font=text_font, bg=COLORS["stripe"] if readonly_bg else COLORS["text_bg"],
                     fg=COLORS["text"], relief="flat", borderwidth=0, highlightthickness=1,
                     highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"],
                     padx=12, pady=10, selectbackground=COLORS["select"], selectforeground=COLORS["text"],
                     insertbackground=COLORS["text"], spacing1=2, spacing3=2)
    return widget
