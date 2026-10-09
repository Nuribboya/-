"""원청 찾기 데스크톱 앱 (tkinter).

터미널 없이 쓰기 위한 화면이다. 탐색 로직은 전부 pipeline 쪽에 있고, 여기서는
입력을 받아 넘기고 결과를 보여 주기만 한다.

    python -m prime_contractor.gui
"""
from __future__ import annotations

import logging
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, W, X, Y, StringVar, BooleanVar, Tk, filedialog, messagebox
from tkinter import ttk, scrolledtext

try:
    # 순정 tkinter/ttk 는 위젯이 딱딱해 보여서, 있으면 부트스트랩 계열 테마를 입힌다.
    # 위젯 코드는 그대로 ttk.* 를 쓰고, Style 하나만 깔아서 전체 룩을 바꾼다.
    import ttkbootstrap as tb
    _HAS_BOOTSTRAP = True
except ImportError:                     # 소스에서 바로 실행할 때 설치 안 돼 있어도 앱은 뜬다
    tb = None
    _HAS_BOOTSTRAP = False

from prime_contractor.app_settings import (
    DISTANCE_CHOICES, OVERLAP_CHOICES, SECTOR_ALL, apply_target, build_config, load_settings, save_settings,
    sector_names,
)
from prime_contractor.help_text import HELP_TEXT
from prime_contractor.config import load_config
from prime_contractor.pipeline import filter_sector, run_screen
from prime_contractor.report import write_xlsx
from prime_contractor.sales import load_sales
from prime_contractor.textutil import redact_secrets
from prime_contractor.updater import check_for_update
from prime_contractor import __version__
from prime_contractor.build_info import label, should_check_updates

COLUMNS = (("순위", 45), ("등급", 45), ("회사 이름", 235), ("어떤 곳", 70), ("하는 일", 125),
           ("지역", 65), ("안성에서", 70), ("한 달 판넬(어림)", 110), ("점수", 55))
SALES_COLUMNS = (("연월", 85), ("실제 매출", 120), ("목표", 130),
                 ("달성률", 75), ("모자란 돈", 120), ("손익", 130))
REVIEW_COLUMNS = (("연월", 85), ("매출", 110), ("평소 대비", 90), ("손익", 110),
                  ("판정", 520))
#: 엑셀로 저장할 때 이 점수 '이하'는 뺀다. 다 넣으면 수백 곳이라 연락할 곳을 고르기 어렵다.
EXPORT_MIN_SCORE = 70
#: 입력 칸을 이 간격(밀리초)마다 조용히 저장한다. 창을 닫을 때도 한 번 더 저장한다.
AUTOSAVE_MS = 60_000


def _Button(parent, **kw):
    """강조색 버튼 하나.

    bootstyle 은 ttkbootstrap 전용 위젯에서만 먹는 옵션이라, 순정 tkinter.ttk.Button
    에 넘기면 TclError("unknown option -bootstyle")로 죽는다. 그래서 ttkbootstrap이
    있을 때는 tb.Button 을, 없을 때는 그 옵션을 뺀 순정 ttk.Button 을 만든다.
    """
    if _HAS_BOOTSTRAP:
        return tb.Button(parent, **kw)
    kw.pop("bootstyle", None)
    return ttk.Button(parent, **kw)


def _label_for(choices: dict, value) -> str:
    """값으로 선택지 문구를 찾는다. 문구를 다듬어도 기본값이 어긋나지 않는다."""
    return next((k for k, v in choices.items() if v == value), list(choices)[0])


class QueueLogHandler(logging.Handler):
    """백그라운드 스레드의 로그를 화면 큐로 보낸다."""

    def __init__(self, sink: queue.Queue) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        # 조회 실패 문구엔 인증키가 든 주소가 통째로 섞여 나온다. 화면에 찍기 전에 가린다.
        self.sink.put(redact_secrets(self.format(record)))


class App:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.messages: queue.Queue[str] = queue.Queue()
        self.result = None
        self.running = False
        saved = load_settings()

        root.title(f"원청 찾기 — 자동제어 판넬  ({label()})")
        root.geometry("1060x740")
        root.minsize(920, 620)

        self.book = None            # 불러온 매출 기록

        # 새 버전 알림 띠. 평소에는 숨어 있다가 업데이트가 있을 때만 나타난다.
        self.update_bar = ttk.Frame(root, padding=(10, 6))
        self.update_text = ttk.Label(self.update_bar, text="", font=("", 10, "bold"))
        self.update_text.pack(side=LEFT)
        ttk.Button(self.update_bar, text="받으러 가기",
                   command=self.on_open_update).pack(side=RIGHT)
        ttk.Button(self.update_bar, text="나중에",
                   command=self.update_bar.pack_forget).pack(side=RIGHT, padx=6)
        self.update_url = ""

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill=BOTH, expand=True)
        find_tab = ttk.Frame(self.tabs)
        sales_tab = ttk.Frame(self.tabs)
        help_tab = ttk.Frame(self.tabs)
        goal_tab = ttk.Frame(self.tabs)
        self.tabs.add(goal_tab, text="  ★ 최우선 목표  ")
        self.tabs.add(find_tab, text="  ① 일감 줄 회사 찾기  ")
        self.tabs.add(sales_tab, text="  ② 매출 보고 채우기  ")
        self.tabs.add(help_tab, text="  도움말  ")

        self.find_tab, self.sales_tab, self.goal_tab = find_tab, sales_tab, goal_tab
        self._build_inputs(find_tab, saved)
        self._build_table(find_tab)
        self._build_log(find_tab)
        self._build_sales(sales_tab, saved)
        self._build_help(help_tab)
        self._build_goal(goal_tab, saved)
        self._pump_messages()
        if should_check_updates():
            threading.Thread(target=self._check_update, daemon=True).start()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(AUTOSAVE_MS, self._autosave)

    # --- 자동저장 -------------------------------------------------------------

    def _autosave(self) -> None:
        """입력 칸을 조용히 저장한다. 실패해도 화면은 계속 써야 하니 그냥 넘어간다."""
        try:
            save_settings(self.current_options())
        except OSError:
            pass
        self.root.after(AUTOSAVE_MS, self._autosave)

    def on_close(self) -> None:
        try:
            save_settings(self.current_options())
        except OSError:
            pass
        self.root.destroy()

    # --- 화면 구성 -----------------------------------------------------------

    def _build_inputs(self, root, saved: dict) -> None:
        box = ttk.LabelFrame(root, text="찾을 조건", padding=10)
        box.pack(fill=X, padx=10, pady=(10, 5))

        def remembered(key: str, choices, fallback: str) -> str:
            """저장된 값이 지금 목록에 없으면(문구를 다듬었다면) 기본값으로 돌린다."""
            value = saved.get(key)
            return value if value in choices else fallback

        self.g2b_key = StringVar(value=saved.get("g2b_key", ""))
        self.dart_key = StringVar(value=saved.get("dart_key", ""))
        self.nts_key = StringVar(value=saved.get("nts_key", ""))
        self.days = StringVar(value=str(saved.get("days", 90)))
        self.distance = StringVar(
            value=remembered("distance", DISTANCE_CHOICES, _label_for(DISTANCE_CHOICES, 70.0)))
        self.overlap = StringVar(
            value=remembered("overlap", OVERLAP_CHOICES, list(OVERLAP_CHOICES)[0]))
        self.sector = StringVar(value=saved.get("sector", SECTOR_ALL))
        self.include_orgs = BooleanVar(value=saved.get("include_demand_orgs", True))
        # 매출이 줄었을 때는 큰 한 방보다 작아도 자주 나오는 일이 낫다 — 기본으로 켠다.
        self.prefer_small = BooleanVar(value=saved.get("prefer_small", True))
        # 대기업·중견 공장은 단가가 세고 유지보수 일이 꾸준하다. 나라장터엔 안 나와서
        # 상장사 목록을 따로 훑는다(기업정보 인증키 필요).
        self.with_factories = BooleanVar(value=saved.get("with_factories", True))
        self.fresh = BooleanVar(value=False)

        # '어디서 찾을까요'(상장사 목록 훑기·연습용 가짜 자료)는 뺐다. 상장사는 대기업이라
        # '작고 꾸준한 곳'과 반대고, 연습용은 인증키를 받은 뒤로 쓸 일이 없다.
        ttk.Label(box, text="최근 며칠치").grid(row=0, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Entry(box, textvariable=self.days, width=10).grid(row=0, column=1, sticky=W, pady=4)

        ttk.Label(box, text="나라장터 인증키").grid(row=1, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Entry(box, textvariable=self.g2b_key, width=46, show="•").grid(
            row=1, column=1, columnspan=2, sticky=W, pady=4)

        ttk.Label(box, text="기업정보 인증키").grid(row=2, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Entry(box, textvariable=self.dart_key, width=46, show="•").grid(
            row=2, column=1, columnspan=2, sticky=W, pady=4)

        ttk.Label(box, text="폐업조회 인증키").grid(row=3, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Entry(box, textvariable=self.nts_key, width=46, show="•").grid(
            row=3, column=1, columnspan=2, sticky=W, pady=4)
        ttk.Label(box, text="없어도 됩니다", foreground="#666").grid(row=3, column=3, sticky=W)

        ttk.Label(box, text="안성에서 얼마나").grid(row=4, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Combobox(box, textvariable=self.distance, values=list(DISTANCE_CHOICES),
                     state="readonly", width=18).grid(row=4, column=1, sticky=W, pady=4)

        ttk.Label(box, text="업종 고르기").grid(row=4, column=2, sticky=W, padx=(20, 8))
        self.sector_box = ttk.Combobox(box, textvariable=self.sector,
                                       values=sector_names(load_config()),
                                       state="readonly", width=22)
        self.sector_box.grid(row=4, column=3, sticky=W)

        ttk.Label(box, text="케이씨그룹과 겹치면").grid(row=5, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Combobox(box, textvariable=self.overlap, values=list(OVERLAP_CHOICES),
                     state="readonly", width=30).grid(row=5, column=1, columnspan=2,
                                                      sticky=W, pady=4)

        ttk.Checkbutton(box, text="관공서·공공기관도 같이 보기",
                        variable=self.include_orgs).grid(row=5, column=3, sticky=W)
        ttk.Checkbutton(box, text="작고 꾸준한 곳 위주 (우리 월매출의 3~20% 크기 · 경기 덜 타는 업종)",
                        variable=self.prefer_small).grid(row=6, column=1, columnspan=3, sticky=W)
        ttk.Checkbutton(box, text="근처 대기업·중견 공장도 같이 찾기 (기업정보 인증키 필요 · 처음엔 몇 분)",
                        variable=self.with_factories).grid(row=7, column=1, columnspan=3, sticky=W)
        ttk.Checkbutton(box, text="저장해 둔 결과 무시하고 전부 새로 받기 (느림)",
                        variable=self.fresh).grid(row=8, column=1, columnspan=3, sticky=W)

        # 제안서에만 쓰는 우리 회사 정보. 화면에 늘 펼쳐 둘 필요가 없어서
        # [제안서 만들기]를 누를 때 뜨는 작은 창에서 받는다(값은 자동저장된다).
        self.profile_name = StringVar(value=saved.get("profile_name", ""))
        self.profile_founded = StringVar(value=saved.get("profile_founded", ""))
        self.profile_certs = StringVar(value=saved.get("profile_certs", ""))
        self.profile_track = StringVar(value=saved.get("profile_track", ""))
        self.profile_contact = StringVar(value=saved.get("profile_contact", ""))
        self.profile_phone = StringVar(value=saved.get("profile_phone", ""))

        buttons = ttk.Frame(root)
        buttons.pack(fill=X, padx=10)
        self.run_button = _Button(buttons, text="  후보 찾기  ", command=self.on_run,
                                   bootstyle="primary")
        self.run_button.pack(side=LEFT)
        self.save_button = _Button(buttons, text="엑셀로 저장",
                                    command=self.on_save, state="disabled",
                                    bootstyle="success-outline")
        self.save_button.pack(side=LEFT, padx=6)
        _Button(buttons, text="영업 목록에 넣기",
                command=self.on_add_to_leads,
                bootstyle="info-outline").pack(side=LEFT, padx=6)
        self.proposal_button = _Button(buttons, text="제안서 만들기",
                                       command=self.on_make_proposal,
                                       bootstyle="info-outline")
        self.proposal_button.pack(side=LEFT, padx=6)
        self.market_button = ttk.Button(buttons, text="관공서 판넬 시장", state="disabled",
                                        command=self.on_show_market)
        self.market_button.pack(side=LEFT)
        self.factory_button = ttk.Button(buttons, text="근처 공장 목록", state="disabled",
                                         command=self.on_show_factories)
        self.factory_button.pack(side=LEFT, padx=6)
        _Button(buttons, text="공장 찾기", command=self.on_find_factories,
                bootstyle="primary-outline").pack(side=LEFT)
        self.factory_file = StringVar(value=saved.get("factory_file", ""))
        self.status = ttk.Label(buttons, text="준비됨")
        self.status.pack(side=RIGHT)

    def _build_table(self, root) -> None:
        frame = ttk.Frame(root)
        frame.pack(fill=BOTH, expand=True, padx=10, pady=8)
        self.tree = ttk.Treeview(frame, columns=[c for c, _ in COLUMNS], show="headings")
        for name, width in COLUMNS:
            self.tree.heading(name, text=name)
            self.tree.column(name, width=width, anchor=W)
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        bar.pack(side=RIGHT, fill=Y)

    def _build_log(self, root) -> None:
        box = ttk.LabelFrame(root, text="진행 상황 (여기에 설명이 나옵니다)", padding=6)
        box.pack(fill=X, padx=10, pady=(0, 10))
        self.log = scrolledtext.ScrolledText(box, height=7, state="disabled")
        self.log.pack(fill=X)

    def _build_help(self, root) -> None:
        """처음 쓰는 사람이 용어 때문에 막히지 않도록 풀어 쓴다."""
        text = scrolledtext.ScrolledText(root, wrap="word", padx=14, pady=12,
                                         font=("", 10), relief="flat")
        text.pack(fill=BOTH, expand=True, padx=10, pady=10)
        text.insert(END, HELP_TEXT)
        text.configure(state="disabled")

    def _build_sales(self, root, saved: dict) -> None:
        """입력은 한 상자에 모으고 [계산하기] 하나로 전부 돌린다. 결과는 작은 탭 셋.

        예전엔 불러오기·적용·손익분기·진단 버튼이 상자마다 따로 있어서 순서대로
        눌러야 했다. 회사 숫자는 한곳에서 넣고 한 번에 계산한다. 지출 장부·고정비
        장부 파일도 한때 받았지만 화면이 복잡해져서 뺐다 — 파일은 매출 장부
        하나, 비용은 '월 고정비'와 '재료·외주비 %' 두 숫자로만 받는다.
        """
        self.cost_model = None

        box = ttk.LabelFrame(root, text="우리 회사 숫자", padding=10)
        box.pack(fill=X, padx=10, pady=(10, 5))

        self.sales_path = StringVar(value=saved.get("sales_path", ""))
        ttk.Label(box, text="매출 장부").grid(row=0, column=0, sticky=W, padx=(0, 8), pady=3)
        ttk.Entry(box, textvariable=self.sales_path, width=58).grid(
            row=0, column=1, columnspan=5, sticky=W, pady=3)
        ttk.Button(box, text="파일 찾기", command=self.on_pick_sales).grid(
            row=0, column=6, padx=6, sticky=W)
        self.sales_vat_included = BooleanVar(value=saved.get("sales_vat_included", False))
        ttk.Checkbutton(box, text="부가세 포함 금액", variable=self.sales_vat_included).grid(
            row=0, column=7, sticky=W)

        self.fixed_cost = StringVar(value=str(saved.get("fixed_cost", "")))
        self.variable_ratio = StringVar(value=str(saved.get("variable_ratio", "")))
        self.employees = StringVar(value=str(saved.get("employees", "")))
        self.target_profit = StringVar(value=str(saved.get("target_profit", "")))

        def field(row: int, col: int, label: str, var, width: int, unit: str) -> None:
            ttk.Label(box, text=label).grid(row=row, column=col, sticky=W,
                                            padx=(0 if col == 0 else 16, 6), pady=(8, 0))
            cell = ttk.Frame(box)
            cell.grid(row=row, column=col + 1, sticky=W, pady=(8, 0))
            ttk.Entry(cell, textvariable=var, width=width).pack(side=LEFT)
            ttk.Label(cell, text=unit, foreground="#666").pack(side=LEFT, padx=(3, 0))

        field(1, 0, "월 고정비", self.fixed_cost, 13, "원 (인건비·임차료 등)")
        field(1, 2, "재료·외주비", self.variable_ratio, 5, "%")
        field(1, 4, "직원 수", self.employees, 5, "명")
        field(2, 0, "목표이익", self.target_profit, 13, "원 (적금 등)")

        buttons = ttk.Frame(box)
        buttons.grid(row=3, column=0, columnspan=8, sticky=W, pady=(12, 0))
        _Button(buttons, text="  계산하기  ", command=self.on_calculate,
                bootstyle="primary").pack(side=LEFT)
        # 결과 한 줄은 길어질 수 있다. 버튼 줄에 붙이면 그 폭만큼 위쪽 칸들이 밀려
        # 화면 밖으로 잘린다. 그래서 따로 한 줄, 넘치면 줄바꿈.
        self.calc_summary = ttk.Label(box, text="매출 장부를 고르고 [계산하기]를 누르세요.",
                                      font=("", 10, "bold"), wraplength=900, justify=LEFT)
        self.calc_summary.grid(row=4, column=0, columnspan=8, sticky=W, pady=(8, 0))
        ttk.Label(box, text="목표: 고정비·재료비가 있으면 손익분기(+목표이익), 없으면 최근 평균.\n"
                            "손익: 매출 × (1 − 재료·외주비 %) − 월 고정비. "
                            "장부가 부가세 포함이면 체크 — 매출을 1.1로 나눠 계산합니다.",
                  foreground="#666").grid(row=5, column=0, columnspan=8, sticky=W, pady=(6, 0))

        self.result_tabs = ttk.Notebook(root)
        self.result_tabs.pack(fill=BOTH, expand=True, padx=10, pady=(5, 10))
        monthly = ttk.Frame(self.result_tabs, padding=6)
        self.review_tab = ttk.Frame(self.result_tabs, padding=6)
        advice = ttk.Frame(self.result_tabs, padding=6)
        self.result_tabs.add(monthly, text="  달마다 손익  ")
        self.result_tabs.add(self.review_tab, text="  기준과 비교  ")
        self.result_tabs.add(advice, text="  뭐부터 챙길지  ")

        self.sales_tree = ttk.Treeview(monthly, columns=[c for c, _ in SALES_COLUMNS],
                                       show="headings", height=9)
        for name, width in SALES_COLUMNS:
            self.sales_tree.heading(name, text=name)
            self.sales_tree.column(name, width=width, anchor=W)
        self.sales_tree.pack(fill=BOTH, expand=True)
        act = ttk.Frame(monthly)
        act.pack(fill=X, pady=(6, 0))
        self.gap_label = ttk.Label(act, text="", font=("", 10, "bold"))
        self.gap_label.pack(side=LEFT)
        self.months_back = StringVar(value=str(saved.get("months_back", 1)))
        ttk.Label(act, text="몇 달치로 볼까요").pack(side=LEFT, padx=(20, 4))
        months = ttk.Combobox(act, textvariable=self.months_back, values=["1", "2", "3", "6"],
                              state="readonly", width=4)
        months.pack(side=LEFT)
        months.bind("<<ComboboxSelected>>", lambda _e: self.book and self._refresh_gap())

        self._build_review(self.review_tab, saved)

        self.diag_text = scrolledtext.ScrolledText(advice, height=8, wrap="word", state="disabled")
        self.diag_text.pack(fill=BOTH, expand=True)

    def _build_review(self, root, saved: dict) -> None:
        """'기준과 비교' 탭: 시작 달 이전을 평소(기준)로 잡고, 그 뒤 달마다 판정한다."""
        from datetime import date
        today = date.today()
        top = ttk.Frame(root)
        top.pack(fill=X)
        ttk.Label(top, text="이 달부터 점검").pack(side=LEFT)
        self.review_start = StringVar(
            value=saved.get("review_start") or f"{today.year:04d}-{today.month:02d}")
        start = ttk.Entry(top, textvariable=self.review_start, width=9)
        start.pack(side=LEFT, padx=(4, 4))
        start.bind("<Return>", lambda _e: self.book and self._render_review())
        ttk.Label(top, text="(예: 2026-10) — 그 전에 끝난 달들의 평균이 '평소'가 됩니다",
                  foreground="#666").pack(side=LEFT)
        self.review_base = ttk.Label(root, text="[계산하기]를 누르면 나옵니다.",
                                     font=("", 10, "bold"))
        self.review_base.pack(fill=X, pady=(6, 4))
        self.review_tree = ttk.Treeview(root, columns=[c for c, _ in REVIEW_COLUMNS],
                                        show="headings", height=7)
        for name, width in REVIEW_COLUMNS:
            self.review_tree.heading(name, text=name)
            self.review_tree.column(name, width=width, anchor=W)
        self.review_tree.pack(fill=BOTH, expand=True)
        self.review_tree.tag_configure("bad", background="#fdecea")
        self.review_tree.tag_configure("warn", background="#fff4e5")
        self.review_total = ttk.Label(root, text="", font=("", 10, "bold"), wraplength=980)
        self.review_total.pack(fill=X, pady=(6, 0))

    def _render_review(self) -> None:
        from prime_contractor.monthly_review import review, summary_lines
        start = self.review_start.get().strip()
        if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", start):
            self.review_base.configure(text="'이 달부터 점검'은 2026-10 처럼 적어 주세요.")
            return
        result = review(self.book, self.cost_model, start)
        lines = summary_lines(result, self.cost_model)
        self.review_base.configure(text=lines[0])
        self.review_total.configure(text=lines[1])
        self.review_tree.delete(*self.review_tree.get_children())
        for c in result.checks:
            # 진행 중인 달은 매출이 다 안 찍혀 '평소 대비'가 늘 크게 낮게 나온다. 빼 둔다.
            vs = (f"{c.vs_baseline * 100:+.0f}%" if result.baseline and c.level != "open"
                  else "-")
            profit = f"{c.profit / 1e4:+,.0f}만원" if c.profit is not None else "-"
            self.review_tree.insert("", END, values=(
                c.ym, f"{c.revenue / 1e4:,.0f}만원", vs, profit, c.verdict),
                tags=(c.level,) if c.level in ("bad", "warn") else ())

    # --- 업데이트 -------------------------------------------------------------

    def _check_update(self) -> None:
        """뒤에서 조용히 확인한다. 실패하면 아무 일도 없었던 것처럼 둔다."""
        info = check_for_update(__version__)
        if info:
            self.root.after(0, self._show_update, info)

    def _show_update(self, info) -> None:
        self.update_url = info.url
        self.update_text.configure(text=f"{info.message} — 받아서 덮어쓰시면 됩니다.")
        self.update_bar.pack(fill=X, before=self.tabs)

    def on_open_update(self) -> None:
        import webbrowser
        if self.update_url:
            webbrowser.open(self.update_url)

    # --- 매출 동작 -----------------------------------------------------------

    def on_pick_sales(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("매출 파일", "*.xlsx *.xlsm *.csv *.json"),
                       ("엑셀", "*.xlsx *.xlsm"), ("모든 파일", "*.*")])
        if path:
            self.sales_path.set(path)
            self.on_calculate()

    def on_calculate(self) -> None:
        """장부 읽기 → 목표 잡기 → 표·진단까지 한 번에."""
        path = self.sales_path.get().strip()
        if not path:
            messagebox.showwarning("매출 장부 필요", "'매출 장부' 칸에 파일을 먼저 골라 주세요.")
            return
        try:
            book = load_sales(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("매출 장부를 읽지 못했습니다",
                                 f"{exc}\n\n도움말 탭의 '매출 파일 만들기' 를 참고하세요.")
            return
        if not book.months:
            messagebox.showwarning("비어 있음", "매출 기록이 비어 있습니다.")
            return

        cost = self._read_cost_model(quiet=True)
        if cost is None and (self.fixed_cost.get().strip() or self.variable_ratio.get().strip()):
            self._read_cost_model()          # 반쯤만 채운 칸은 왜 안 쓰는지 알려 준다

        basis = apply_target(book, cost, 0)

        self.book, self.cost_model = book, cost
        latest = book.latest_closed()
        target = latest.target if latest else 0
        parts = [f"목표 월 {target / 1e4:,.0f}만원 — {basis}" if target else "목표 없음"]
        if book.title:
            # 엉뚱한 표(주유비 등)를 매출로 읽으면 바로 눈에 띄게, 읽은 표 제목을 보인다.
            from prime_contractor.sales import _title_name
            parts.append(f"읽은 표: {_title_name(book.title)}")
        if cost is None:
            parts.append("손익은 '월 고정비'와 '재료·외주비'를 둘 다 적어야 나옵니다")
        elif cost.vat_included:
            parts.append("매출 장부가 부가세 포함 — 손익은 부가세 빼고, 목표는 장부와 같은 포함 금액")
        self.target_basis = basis if target else ""
        diag = self._current_diagnosis()
        self.calc_summary.configure(text="  ·  ".join(parts))

        self._render_sales()
        self._render_diagnosis(diag)
        self._render_review()

    def _month_profit(self, month) -> int | None:
        return self.cost_model.profit_at(month.revenue) if self.cost_model else None

    # --- 손익분기 -------------------------------------------------------------

    def _read_cost_model(self, quiet: bool = False):
        """입력 칸에서 비용 구조를 만든다. 문제가 있으면 안내하고 None.

        quiet=True 면 안내창 없이 None 만 돌려준다 (파일 불러올 때 자동 적용용).
        """
        from prime_contractor.breakeven import CostModel, parse_ratio
        fixed = int(re.sub(r"[^\d]", "", self.fixed_cost.get() or "") or 0)
        ratio_text = self.variable_ratio.get().strip()
        profit = int(re.sub(r"[^\d]", "", self.target_profit.get() or "") or 0)
        if not fixed or not ratio_text:
            if quiet:
                return None
            messagebox.showwarning(
                "숫자가 필요합니다",
                "'월 고정비'와 '재료·외주비' 비율을 둘 다 적어주세요.\n\n"
                "모르시면 [재무제표로 채우기]로 손익계산서 숫자 세 개만 넣으셔도 됩니다.")
            return None
        try:
            return CostModel(monthly_fixed=fixed, variable_ratio=parse_ratio(ratio_text),
                             monthly_profit=profit,
                             vat_included=self.sales_vat_included.get())
        except ValueError as exc:
            if not quiet:
                messagebox.showwarning("숫자를 확인해 주세요", str(exc))
            return None

    def _current_diagnosis(self):
        from prime_contractor.diagnosis import analyze
        employees = int(re.sub(r"[^\d]", "", self.employees.get() or "0") or 0)
        return analyze(self.book, employees, self.cost_model)

    def _render_diagnosis(self, diag) -> None:
        lines: list[str] = []
        if diag.note:
            lines.append(diag.note)
        if diag.revenue_per_employee is not None:
            trend = f" — {diag.revenue_per_employee_trend}" if diag.revenue_per_employee_trend else ""
            lines.append(f"직원당 매출(최근 평균): {diag.revenue_per_employee / 1e4:,.0f}만원/인{trend}")
        if diag.latest_month_profit is not None:
            lines.append(f"{diag.latest_month} 추정 손익: {diag.latest_month_profit / 1e4:+,.0f}만원")
        if diag.priorities:
            lines.append("")
            lines.append("[뭐부터 챙길지]")
            for i, p in enumerate(diag.priorities, 1):
                lines.append(f"{i}. {p.text} — {p.reason}")
        self._render_diagnosis_text(
            "\n".join(lines) if lines else "장부에서 끝난 달을 찾지 못했습니다.")

    def _render_diagnosis_text(self, text: str) -> None:
        self.diag_text.configure(state="normal")
        self.diag_text.delete("1.0", END)
        self.diag_text.insert(END, text)
        self.diag_text.configure(state="disabled")

    def _render_sales(self) -> None:
        from datetime import date
        book = self.book
        today = date.today()
        current = f"{today.year:04d}-{today.month:02d}"
        # 목표가 손익분기면 '목표'가 아니라 '적자 안 나는 선'이다. 391% 같은 달성률이
        # 이상해 보이지 않게, 무엇을 기준으로 잡았는지 머리글에 적는다.
        basis = getattr(self, "target_basis", "")
        heading = "목표(손익분기)" if basis.startswith("손익분기") else "목표"
        self.sales_tree.heading("목표", text=heading)
        self.sales_tree.delete(*self.sales_tree.get_children())
        for m in book.sorted_months():
            rate = f"{m.rate * 100:.0f}%" if m.rate is not None else "-"
            if m.ym >= current:
                # 아직 안 끝난 달: 매출은 들어온 만큼만인데 고정비는 한 달치를 다 빼면
                # 손익이 엉뚱하게 나온다. 달성률(지금까지 얼마나 왔나)만 보여 준다.
                self.sales_tree.insert("", END, values=(
                    m.ym, f"{m.revenue / 1e4:,.0f}만원",
                    f"{m.target / 1e4:,.0f}만원" if m.target else "-",
                    rate, "-", "진행 중"))
                continue
            profit = self._month_profit(m)
            if profit is None:
                profit_text = "-"
            else:
                profit_text = f"{profit / 1e4:+,.0f}만원" + (" 적자" if profit < 0 else "")
            tag = "loss" if profit is not None and profit < 0 else ("miss" if m.gap else "")
            self.sales_tree.insert("", END, values=(
                m.ym, f"{m.revenue / 1e4:,.0f}만원",
                f"{m.target / 1e4:,.0f}만원" if m.target else "-",
                rate, f"{m.gap / 1e4:,.0f}만원" if m.gap else "-", profit_text),
                tags=(tag,) if tag else ())
        self.sales_tree.tag_configure("miss", background="#fff4e5")    # 목표 미달
        self.sales_tree.tag_configure("loss", background="#fdecea")    # 실제 적자
        self._refresh_gap()

    def _refresh_gap(self) -> None:
        record = self.book.latest_closed() if self.book else None
        if record is None:
            self.gap_label.configure(text="끝난 달이 없습니다. 장부를 확인해 주세요.")
            return
        if not self.book.has_targets:
            self.gap_label.configure(text="목표가 없습니다. 고정비·재료비를 적고 [계산하기]를 누르세요.")
            return
        months_back = int(self.months_back.get() or 1)
        gap = self.book.recent_gap(months_back) if months_back > 1 else record.gap
        if gap:
            loss = ""
            profit = self._month_profit(record)
            if profit is not None and profit < 0:
                loss = f"  · {record.ym} 은 적자입니다"
            if months_back > 1:
                # 여러 달이면 '목표의 몇 %'는 마지막 달 값이라 헷갈린다(6개월을 골라도
                # 8월 달성률이 나왔다). 몇 달이 모자랐는지로 말한다 — 모자란 달의
                # 부족분만 더한 값이라 넘친 달이 있어도 합계가 줄지 않는다.
                closed = [m for m in self.book.sorted_months() if m.ym <= record.ym]
                short = sum(1 for m in closed[-months_back:] if m.gap)
                text = (f"최근 {months_back}개월 중 {short}달 목표 미달 — "
                        f"모자란 달만 더하면 {gap / 1e4:,.0f}만원")
            else:
                rate = f"{record.rate * 100:.0f}%" if record.rate is not None else "-"
                text = f"{record.ym}  {gap / 1e4:,.0f}만원 모자람  (목표의 {rate})"
            self.gap_label.configure(text=text + loss)
        else:
            span = f"최근 {months_back}개월" if months_back > 1 else record.ym
            self.gap_label.configure(text=f"{span} 목표 달성 — 부족분 없음")

    # --- 동작 ---------------------------------------------------------------

    def say(self, text: str) -> None:
        self.messages.put(text)

    def _pump_messages(self) -> None:
        """백그라운드에서 온 메시지를 화면에 붙인다 (tkinter 는 메인 스레드 전용)."""
        while True:
            try:
                line = self.messages.get_nowait()
            except queue.Empty:
                break
            self.log.configure(state="normal")
            self.log.insert(END, line + "\n")
            self.log.see(END)
            self.log.configure(state="disabled")
        self.root.after(150, self._pump_messages)

    def current_options(self) -> dict:
        return {
            "g2b_key": self.g2b_key.get(),
            "dart_key": self.dart_key.get(),
            "nts_key": self.nts_key.get(),
            "days": self.days.get(),
            "distance": self.distance.get(),
            "overlap": self.overlap.get(),
            "sector": self.sector.get(),
            "include_demand_orgs": self.include_orgs.get(),
            "prefer_small": self.prefer_small.get(),
            "with_factories": self.with_factories.get(),
            "factory_file": self.factory_file.get(),
            "profile_name": self.profile_name.get(),
            "profile_founded": self.profile_founded.get(),
            "profile_certs": self.profile_certs.get(),
            "profile_track": self.profile_track.get(),
            "profile_contact": self.profile_contact.get(),
            "profile_phone": self.profile_phone.get(),
            "sales_path": self.sales_path.get(),
            "sales_vat_included": self.sales_vat_included.get(),
            "fixed_cost": self.fixed_cost.get(),
            "variable_ratio": self.variable_ratio.get(),
            "target_profit": self.target_profit.get(),
            "employees": self.employees.get(),
            "months_back": self.months_back.get(),
            "review_start": self.review_start.get(),
            "goal_target": self.goal_target.get(),
            "goal_current": self.goal_current.get(),
            "goal_months": self.goal_months.get(),
            "goal_per_client": self.goal_per_client.get(),
            "goal_cash": self.goal_cash.get(),
        }

    def on_run(self) -> None:
        if self.running:
            return
        options = self.current_options()
        if not options["g2b_key"].strip():
            messagebox.showwarning(
                "인증키가 필요합니다",
                "'나라장터 인증키' 칸을 채워주세요.\n\n"
                "키 받는 곳은 도움말 탭에 적어두었습니다.")
            return

        self.running = True
        self.run_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        self.status.configure(text="찾는 중…")
        self.tree.delete(*self.tree.get_children())
        threading.Thread(target=self._work, args=(options,), daemon=True).start()

    def _work(self, options: dict) -> None:
        handler = QueueLogHandler(self.messages)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root_log = logging.getLogger("prime_contractor")
        root_log.addHandler(handler)
        root_log.setLevel(logging.INFO)
        try:
            result = self._screen(options)
            self.root.after(0, self._done, result)
        except Exception as exc:                       # 화면이 통째로 죽는 것만은 막는다
            self.say(f"오류: {redact_secrets(exc)}")
            self.root.after(0, self._failed, exc)
        finally:
            root_log.removeHandler(handler)

    def _screen(self, options: dict):
        cfg = build_config(options)
        if not self.book and options.get("sales_path"):
            # ② 탭에서 [계산하기]를 안 눌렀어도 저장된 매출 장부가 있으면 읽어 둔다.
            # 우리 월매출을 모르면 '클수록 좋다'로 점수를 매겨, 직원 6명 회사가 감당 못 할
            # 큰 곳이 1등에 오른다.
            try:
                self.book = load_sales(options["sales_path"])
            except (OSError, ValueError):
                self.book = None
        if self.book and self.book.average_revenue():
            from dataclasses import replace
            ours = self.book.average_revenue()
            cfg = replace(cfg, our_monthly_revenue=ours)
            if cfg.prefer_small:
                self.say(f"작고 꾸준한 곳 위주: 한 달 판넬이 우리 월매출({ours / 1e4:,.0f}만원)의 "
                         f"3~20%({ours * 0.03 / 1e4:,.0f}~{ours * 0.2 / 1e4:,.0f}만원)인 곳을 "
                         "가장 좋게 보고, 경기를 덜 타는 업종에 점수를 더 줍니다.")
            else:
                self.say(f"우리 월매출 {ours / 1e4:,.0f}만원 기준으로, 너무 작거나 너무 큰 곳은 "
                         f"점수를 낮춥니다.")
        else:
            self.say("② 탭에서 매출 장부를 불러오면 '우리 크기에 맞는 곳'으로 점수를 매깁니다.")
        result = self._fetch(cfg)
        if options.get("with_factories"):
            result.factories = self._fetch_factories(cfg)

        sector = options.get("sector")
        if sector and sector != SECTOR_ALL:
            filter_sector(result, sector)
            result.factories = [c for c in result.factories if sector in c.sector]
        return result

    def _fetch(self, cfg):
        """나라장터에서 받아 후보를 만든다. 시험할 때는 이것만 바꿔 끼우면 된다."""
        from prime_contractor.sources.g2b import G2BClient, default_cache_dir
        self.say(f"나라장터에서 최근 {cfg.lookback_days}일치 공사를 찾아봅니다…")
        dart = nts = None
        if cfg.dart_api_key:
            from prime_contractor.sources.dart import DartClient
            dart = DartClient(cfg.dart_api_key)
        if cfg.nts_service_key:
            from prime_contractor.sources.nts import NtsClient
            nts = NtsClient(cfg.nts_service_key)
            self.say("폐업한 회사는 국세청에 확인해서 빼겠습니다.")
        g2b = G2BClient(cfg.g2b_service_key, cache_dir=default_cache_dir())
        if self.fresh.get() and g2b.cache:
            self.say(f"저장해 둔 조회 결과 {g2b.cache.clear()}건을 지우고 새로 받습니다.")
        return run_screen(cfg, g2b_client=g2b, dart_client=dart, nts_client=nts)

    def _fetch_factories(self, cfg) -> list:
        """근처 대기업·중견 공장. 키가 없거나 실패해도 나라장터 결과는 그대로 보여 준다."""
        if not cfg.dart_api_key:
            self.say("근처 공장 찾기는 '기업정보 인증키'가 있어야 합니다 — 이번엔 건너뜁니다.")
            return []
        from prime_contractor.pipeline import run_factory_screen
        from prime_contractor.sources.dart import DartClient
        self.say("근처 대기업·중견 공장을 상장사 목록에서 찾습니다. 처음엔 몇 분 걸리고, "
                 "다음부터는 저장해 둔 걸 씁니다…")
        try:
            factories = run_factory_screen(cfg, DartClient(cfg.dart_api_key))
        except Exception as exc:                 # 공장 목록 실패로 전체를 버리지 않는다
            self.say(f"근처 공장 찾기 실패(나라장터 결과는 그대로): {redact_secrets(exc)}")
            return []
        self.say(f"근처 공장 {len(factories)}곳 — [근처 공장 목록]에서 보세요.")
        return factories

    def _done(self, result) -> None:
        self.result = result
        for i, c in enumerate(result.passed, 1):
            dist = f"{c.distance_km:.0f}km" if c.distance_km is not None else "미상"
            # 조회 기간 전체 합계(수십억)는 '한 달에 우리한테 올 일'로 오해하기 쉬워
            # 한 달치로 나눠 보인다.
            month = getattr(c.fitness, "monthly_panel_amount", 0)
            self.tree.insert("", END, values=(
                i, c.grade or "-", c.name, "원청" if c.kind == "contractor" else "발주처",
                c.sector or "미분류", c.region or "미상", dist,
                _money(month), f"{c.score:.1f}"),
                tags=(c.grade,))
        for grade, color in (("A", "#e8f5e9"), ("B", "#f1f8e9")):
            self.tree.tag_configure(grade, background=color)
        self.tree.bind("<Double-1>", self._show_detail)
        for note in result.notes:
            self.say(note)
        stats = " / ".join(f"{k} {v}" for k, v in result.stats.items())
        self.say(f"완료 — {stats}")
        grades = {}
        for c in result.passed:
            grades[c.grade] = grades.get(c.grade, 0) + 1
        summary = " ".join(f"{g}등급 {grades[g]}곳" for g in "ABCD" if g in grades)
        self.say("찾은 회사: " + (summary or "없음"))
        self.say("A등급부터 연락해 보세요. B등급까지는 연락할 만합니다.")
        self.say("회사 이름을 두 번 클릭하면 왜 그 점수인지 자세히 나옵니다.")
        self.status.configure(text=f"{len(result.passed)}곳 찾음")
        self.save_button.configure(
            state="normal" if result.passed or result.factories else "disabled")
        self.market_button.configure(state="normal" if result.market else "disabled")
        self.factory_button.configure(state="normal" if result.factories else "disabled")
        if not result.passed:
            messagebox.showinfo("찾은 곳이 없습니다",
                                "조건에 맞는 회사가 없습니다.\n\n"
                                "이렇게 해보세요\n"
                                "  · '최근 며칠치' 를 180 이나 365 로 늘리기\n"
                                "  · '안성에서 얼마나' 를 100km 로 넓히기\n"
                                "  · '업종 고르기' 를 '업종 안 가림' 으로 두기")
        self._finish()

    # --- 최우선 목표 탭 ---------------------------------------------------------

    def _build_goal(self, root, saved: dict) -> None:
        from prime_contractor.leads import ALL_STAGES, LeadBook
        self.lead_book = LeadBook.load()
        self.goal_plan = None

        box = ttk.LabelFrame(root, text="목표 — 원청 한 곳에 기대는 비중을 낮추기", padding=10)
        box.pack(fill=X, padx=10, pady=(10, 5))
        self.goal_target = StringVar(value=str(saved.get("goal_target", "70")))
        self.goal_current = StringVar(value=str(saved.get("goal_current", "100")))
        self.goal_months = StringVar(value=str(saved.get("goal_months", "12")))
        self.goal_per_client = StringVar(value=str(saved.get("goal_per_client", "10000000")))
        self.goal_cash = StringVar(value=str(saved.get("goal_cash", "")))
        fields = [
            ("지금 가장 큰 원청 비중", self.goal_current, "%  (원청 한 곳이면 100)"),
            ("목표 비중", self.goal_target, "%  밑으로"),
            ("기한", self.goal_months, "개월 안에"),
            ("새 원청 한 곳당 월 발주", self.goal_per_client, "원  (처음엔 작게 시작 — 어림값)"),
            ("쓸 수 있는 현금", self.goal_cash, "원  (없어도 됨 — 몇 달 버티나 계산)"),
        ]
        for row, (label, var, hint) in enumerate(fields):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky=W, pady=2)
            ttk.Entry(box, textvariable=var, width=14).grid(row=row, column=1, sticky=W, pady=2)
            ttk.Label(box, text=hint, foreground="#666").grid(row=row, column=2, sticky=W, padx=6)
        _Button(box, text="  계산하기  ", command=self.on_goal,
                bootstyle="primary").grid(
            row=0, column=3, rowspan=2, padx=(20, 0))
        ttk.Label(box, text="월매출은 ② 탭에서 불러온 장부로,\n위험은 ② 탭의 고정비로 계산합니다.",
                  foreground="#666").grid(row=2, column=3, rowspan=3, padx=(20, 0), sticky=W)

        self.goal_text = scrolledtext.ScrolledText(root, height=12, wrap="word",
                                                   font=("", 10), relief="flat")
        self.goal_text.pack(fill=X, padx=10, pady=5)
        self.goal_text.insert(END, "② 탭에서 매출 장부를 불러온 뒤 [계산하기]를 누르세요.\n"
                                   "이번 주에 몇 곳에 연락해야 하는지 거꾸로 계산해 드립니다.")
        self.goal_text.configure(state="disabled")

        leads_box = ttk.LabelFrame(root, text="영업 진행 — ① 탭에서 회사를 골라 넣으세요",
                                   padding=6)
        leads_box.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        cols = (("회사", 190), ("등급", 45), ("단계", 75), ("다음 할 일", 210),
                ("날짜", 90), ("결제조건", 130), ("전화", 105))
        self.lead_tree = ttk.Treeview(leads_box, columns=[c for c, _ in cols],
                                      show="headings", height=8)
        for name, width in cols:
            self.lead_tree.heading(name, text=name)
            self.lead_tree.column(name, width=width, anchor=W)
        self.lead_tree.tag_configure("late", background="#fdecea")
        self.lead_tree.tag_configure("won", background="#e8f5e9")
        self.lead_tree.pack(fill=BOTH, expand=True)

        act = ttk.Frame(leads_box)
        act.pack(fill=X, pady=(6, 0))
        self.lead_stage = StringVar(value="연락함")
        ttk.Label(act, text="고른 회사를").pack(side=LEFT)
        ttk.Combobox(act, textvariable=self.lead_stage, values=list(ALL_STAGES),
                     state="readonly", width=8).pack(side=LEFT, padx=4)
        ttk.Button(act, text="단계로 옮기기", command=self.on_lead_move).pack(side=LEFT)
        ttk.Button(act, text="다음 할 일·결제조건 적기",
                   command=self.on_lead_edit).pack(side=LEFT, padx=6)
        _Button(act, text="빼기", command=self.on_lead_remove,
                bootstyle="danger-outline").pack(side=LEFT)
        ttk.Label(act, text="빨간 줄 = 할 일 날짜가 지남 · 초록 줄 = 첫 수주",
                  foreground="#666").pack(side=RIGHT)
        self._render_leads()

    def _goal_inputs(self):
        from prime_contractor.breakeven import parse_ratio
        from prime_contractor.goal import GoalInputs

        def number(var) -> int:
            return int(re.sub(r"[^\d]", "", var.get() or "") or 0)

        revenue = self.book.average_revenue() if self.book else 0
        if not revenue:
            messagebox.showwarning("매출 장부가 필요합니다",
                                   "② 탭에서 매출 장부를 먼저 불러와 주세요.\n"
                                   "지금 월매출을 알아야 얼마를 새로 벌어야 하는지 계산됩니다.")
            return None
        model = self.cost_model
        try:
            return GoalInputs(
                monthly_revenue=revenue,
                target_dependency=parse_ratio(self.goal_target.get() or "70"),
                current_dependency=parse_ratio(self.goal_current.get() or "100"),
                months=number(self.goal_months) or 12,
                revenue_per_new_client=number(self.goal_per_client) or 10_000_000,
                monthly_fixed=model.monthly_fixed if model else 0,
                margin_ratio=model.margin_ratio if model else 0.0,
                cash_on_hand=number(self.goal_cash))
        except ValueError as exc:
            messagebox.showwarning("숫자를 확인해 주세요", str(exc))
            return None

    def on_goal(self) -> None:
        from prime_contractor.goal import build_plan
        inputs = self._goal_inputs()
        if inputs is None:
            return
        self.goal_plan = build_plan(inputs)
        self._render_goal_text()

    def _render_goal_text(self) -> None:
        from prime_contractor.leads import progress_lines
        lines = [f"지금 월매출 {self.goal_plan.inputs.monthly_revenue / 1e4:,.0f}만원 (최근 평균) 기준", ""]
        lines += self.goal_plan.summary()
        if not self.cost_model:
            lines += ["", "(② 탭에서 고정비를 넣으시면, 원청이 멈췄을 때 매달 얼마씩 "
                          "적자인지도 보여 드립니다.)"]
        lines += [""] + progress_lines(self.lead_book, self.goal_plan)
        lines += ["", "전환율과 '한 곳당 월 발주'는 어림값입니다. 영업 기록이 쌓이면 "
                      "실제 전환율이 위에 나타납니다."]
        self.goal_text.configure(state="normal")
        self.goal_text.delete("1.0", END)
        self.goal_text.insert(END, "\n".join(lines))
        self.goal_text.configure(state="disabled")

    def _render_leads(self) -> None:
        from prime_contractor.leads import STAGES
        order = {s: i for i, s in enumerate(STAGES)}
        self.lead_tree.delete(*self.lead_tree.get_children())
        for lead in sorted(self.lead_book.leads,
                           key=lambda l: (-order.get(l.stage, -1), l.name)):
            tag = "won" if lead.stage == "첫수주" else ("late" if lead.is_overdue() else "")
            self.lead_tree.insert("", END, iid=lead.key, values=(
                lead.name, lead.grade or "-", lead.stage, lead.next_action or "-",
                lead.next_date or "-", lead.payment_terms or "-", lead.phone or "-"),
                tags=(tag,) if tag else ())
        if self.goal_plan is not None:
            self._render_goal_text()

    def _selected_lead(self):
        key = self.lead_tree.focus()
        if not key:
            messagebox.showinfo("회사를 고르세요", "아래 표에서 회사를 한 번 클릭해 고른 뒤 눌러주세요.")
            return None
        return self.lead_book.find(key)

    def _save_leads(self) -> None:
        try:
            self.lead_book.save()
        except OSError as exc:
            messagebox.showerror("저장하지 못했습니다", str(exc))
        self._render_leads()

    def on_add_to_leads(self) -> None:
        if not self.result:
            messagebox.showinfo("먼저 찾아 주세요", "[후보 찾기]로 회사를 찾은 뒤 표에서 골라 주세요.")
            return
        picked = self.tree.selection() or ((self.tree.focus(),) if self.tree.focus() else ())
        if not picked:
            messagebox.showinfo("회사를 고르세요",
                                "표에서 회사를 클릭해 고르세요.\n"
                                "Ctrl 을 누른 채 클릭하면 여러 곳을 한 번에 고를 수 있습니다.")
            return
        added = 0
        for item in picked:
            try:
                cand = self.result.passed[int(self.tree.item(item, "values")[0]) - 1]
            except (ValueError, IndexError):
                continue
            _, created = self.lead_book.add_candidate(cand)
            added += created
        self._save_leads()
        self.say(f"영업 목록에 {added}곳 넣었습니다. ★ 최우선 목표 탭에서 진행을 기록하세요.")

    def _current_profile(self):
        from prime_contractor.proposal import CompanyProfile
        return CompanyProfile(
            name=self.profile_name.get().strip(),
            founded_year=self.profile_founded.get().strip(),
            certifications=self.profile_certs.get().strip(),
            track_record=self.profile_track.get().strip(),
            contact_name=self.profile_contact.get().strip(),
            contact_phone=self.profile_phone.get().strip(),
        )

    def on_make_proposal(self) -> None:
        if not self.result:
            messagebox.showinfo("먼저 찾아 주세요", "[후보 찾기]로 회사를 찾은 뒤 표에서 골라 주세요.")
            return
        picked = self.tree.selection() or ((self.tree.focus(),) if self.tree.focus() else ())
        if not picked:
            messagebox.showinfo("회사를 고르세요",
                                "표에서 회사를 클릭해 고르세요.\n"
                                "Ctrl 을 누른 채 클릭하면 여러 곳을 한 번에 고를 수 있습니다.")
            return
        candidates = []
        for item in picked:
            try:
                candidates.append(self.result.passed[int(self.tree.item(item, "values")[0]) - 1])
            except (ValueError, IndexError):
                continue
        if not candidates:
            return
        if not self._ask_profile(len(candidates)):
            return
        folder = filedialog.askdirectory(title="제안서를 저장할 폴더를 고르세요")
        if not folder:
            return
        from prime_contractor.proposal import build_proposal
        profile = self._current_profile()
        made = []
        try:
            for cand in candidates:
                text = build_proposal(cand, profile)
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", cand.name).strip() or "회사"
                path = Path(folder) / f"제안서_{safe_name}.txt"
                path.write_text(text, encoding="utf-8")
                made.append(path.name)
        except OSError as exc:
            messagebox.showerror("저장하지 못했습니다", str(exc))
            return
        self.say(f"제안서 {len(made)}개를 만들었습니다: {folder}")
        messagebox.showinfo("만들었습니다",
                            f"{len(made)}개 파일을 저장했습니다 — 보내기 전에 한 번 읽어보세요.\n\n{folder}")

    def on_show_market(self) -> None:
        """관공서가 판넬을 물품으로 직접 산 계약 — 조달 등록하면 우리가 팔 수 있는 시장."""
        from tkinter import Toplevel
        market = self.result.market if self.result else []
        if not market:
            messagebox.showinfo("관공서 판넬 시장", "이번 조회 기간에 관공서가 판넬을 물품으로 산 기록이 "
                                               "없습니다.")
            return
        win = Toplevel(self.root)
        win.title("관공서 판넬 시장 — 관공서가 판넬을 직접 산 계약")
        win.geometry("980x560")
        body = ttk.Frame(win, padding=10)
        body.pack(fill=BOTH, expand=True)
        ttk.Label(body, text=_market_summary(market), justify=LEFT, wraplength=940).pack(
            fill=X, pady=(0, 8))
        cols = (("날짜", 90), ("사는 기관", 200), ("무엇을 샀나 (공고명)", 380),
                ("납품한 업체", 170), ("금액", 100))
        frame = ttk.Frame(body)
        frame.pack(fill=BOTH, expand=True)
        tree = ttk.Treeview(frame, columns=[c for c, _ in cols], show="headings")
        for name, width in cols:
            tree.heading(name, text=name)
            tree.column(name, width=width, anchor=W)
        bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.pack(side=LEFT, fill=BOTH, expand=True)
        bar.pack(side=RIGHT, fill=Y)
        for a in market:
            tree.insert("", END, values=((a.opening_dt or "")[:10], a.demand_org, a.title,
                                         a.winner_name, _money(a.amount)))
        ttk.Label(body, foreground="#666", justify=LEFT, wraplength=940, text=(
            "관공서는 판넬을 '직접생산확인증명서'가 있는 중소기업한테서만 삽니다. 우리가 조달청에 "
            "등록하고 확인증을 받으면 이 계약들에 직접 들어갈 수 있습니다. 납품한 업체는 그 시장의 "
            "경쟁사이고, 금액은 단가를 가늠하는 데 쓰세요. 엑셀로 저장하면 같은 목록이 "
            "'관공서 판넬 시장' 시트에 들어갑니다.")).pack(fill=X, pady=(8, 0))

    def on_show_factories(self) -> None:
        """근처 대기업·중견 공장 — 경기 덜 타는 업종 먼저, 가까운 순."""
        from tkinter import Toplevel
        factories = self.result.factories if self.result else []
        if not factories:
            return
        win = Toplevel(self.root)
        win.title(f"근처 공장 목록 — 대기업·중견 {len(factories)}곳")
        win.geometry("1000x580")
        body = ttk.Frame(win, padding=10)
        body.pack(fill=BOTH, expand=True)
        ttk.Label(body, justify=LEFT, wraplength=960, text=(
            "상장사 중 '안성에서 얼마나'로 고른 거리 안에 있는 공장입니다. 경기를 덜 타는 업종이 "
            "먼저, 같은 업종이면 가까운 순입니다. 단가는 세지만 협력업체 등록(재무제표·신용등급·"
            "품질인증·실사)이 까다로워, 본사 구매팀보다 그 공장 시설팀·공무팀에 '라인 개조나 판넬 "
            "교체 때 견적 낼 수 있게 해 달라'고 하는 게 빠릅니다.")).pack(fill=X, pady=(0, 8))
        cols = (("#", 40), ("회사", 210), ("업종", 150), ("경기", 70), ("지역", 70),
                ("거리", 60), ("대표자", 90), ("주소", 280))
        frame = ttk.Frame(body)
        frame.pack(fill=BOTH, expand=True)
        tree = ttk.Treeview(frame, columns=[c for c, _ in cols], show="headings")
        for name, width in cols:
            tree.heading(name, text=name)
            tree.column(name, width=width, anchor=W)
        bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.pack(side=LEFT, fill=BOTH, expand=True)
        bar.pack(side=RIGHT, fill=Y)
        from prime_contractor.config import load_config
        from prime_contractor.pipeline import steady_of
        from prime_contractor.report import _steady_text
        cfg = load_config()
        for i, c in enumerate(factories, 1):
            tree.insert("", END, iid=str(i), values=(
                i, c.name, c.sector or "미분류", _steady_text(steady_of(c, cfg)),
                c.region or "미상", f"{c.distance_km:.0f}km", c.ceo or "-", c.address))

        def add_picked() -> None:
            picked = tree.selection()
            if not picked:
                messagebox.showinfo("회사를 고르세요", "목록에서 회사를 클릭해 고르세요 (Ctrl+클릭으로 여러 곳).",
                                    parent=win)
                return
            added = sum(self.lead_book.add_candidate(factories[int(i) - 1])[1] for i in picked)
            self._save_leads()
            self.say(f"영업 목록에 {added}곳 넣었습니다. ★ 최우선 목표 탭에서 진행을 기록하세요.")
            messagebox.showinfo("넣었습니다", f"영업 목록에 {added}곳 넣었습니다.", parent=win)

        row = ttk.Frame(body)
        row.pack(fill=X, pady=(8, 0))
        _Button(row, text="고른 회사를 영업 목록에 넣기", command=add_picked,
                bootstyle="info-outline").pack(side=LEFT)
        ttk.Label(row, foreground="#666", text="엑셀로 저장하면 '근처 공장' 시트에도 들어갑니다.").pack(
            side=LEFT, padx=10)

    # --- 공장 찾기 -----------------------------------------------------------------

    def on_find_factories(self) -> None:
        """등록공장 파일에서 근처 공장을 고른다 — 판넬을 실제로 쓰는 곳."""
        start = Path(self.factory_file.get()).parent if self.factory_file.get() else None
        path = filedialog.askopenfilename(
            title="전국 등록공장 현황 파일을 고르세요 (공공데이터포털·팩토리온)",
            initialdir=str(start) if start and start.exists() else None,
            filetypes=[("공장 목록", "*.csv *.xlsx"), ("모든 파일", "*.*")])
        if not path:
            return
        self.factory_file.set(path)
        cfg = build_config(self.current_options())
        self.say("공장 목록을 읽는 중입니다 (몇십만 줄이면 1분쯤 걸립니다)…")
        threading.Thread(target=self._load_makers, args=(path, cfg), daemon=True).start()

    def _load_makers(self, path: str, cfg) -> None:
        from prime_contractor.makers import (
            find_factories, has_size_columns, mark_dart_registered, read_factory_file)
        try:
            records = read_factory_file(path)
            factories = find_factories(records, within_km=cfg.within_km)
        except (OSError, ValueError) as exc:
            self.root.after(0, messagebox.showerror, "공장 목록을 읽지 못했습니다", str(exc))
            return
        sized = has_size_columns(records)
        if cfg.dart_api_key:
            # 규모 칸이 없는 파일이 많아, 금감원(DART)에 등록된 회사인지로 규모를 가늠한다.
            from prime_contractor.sources.dart import DartClient
            try:
                hits = mark_dart_registered(factories, DartClient(cfg.dart_api_key).corp_index)
                self.say(f"그중 {hits}곳은 금감원(DART)에 등록된 회사(외부감사 받는 규모)입니다.")
            except Exception as exc:              # 규모 표시만 빠지고 목록은 그대로 보여 준다
                self.say(f"DART 확인 실패(목록은 그대로): {redact_secrets(exc)}")
        elif not sized:
            self.say("이 파일엔 종업원·면적 칸이 없습니다. '기업정보 인증키'를 넣으면 금감원 "
                     "등록 여부로 규모를 가늠합니다.")
        self.say(f"공장 {len(records):,}곳 중 거리 안의 공장 {len(factories)}곳 (판넬 업체 제외).")
        can_size = sized or bool(cfg.dart_api_key)
        self.root.after(0, self._show_makers, factories, cfg.within_km, can_size)

    def _show_makers(self, factories, within, can_size: bool = True) -> None:
        from collections import Counter
        from tkinter import Toplevel
        from prime_contractor.makers import (
            MIN_AREA_M2, MIN_EMPLOYEES, is_sizable, size_text, steady_text)
        if not factories:
            messagebox.showinfo("공장 찾기", "거리 안에서 공장을 찾지 못했습니다.\n"
                                          "'안성에서 얼마나'를 넓혀 보세요.")
            return
        ordered = sorted(factories, key=lambda c: (-c.sector_weight, c.distance_km))
        win = Toplevel(self.root)
        win.geometry("1080x620")
        body = ttk.Frame(win, padding=10)
        body.pack(fill=BOTH, expand=True)
        info = ttk.Label(body, justify=LEFT, wraplength=1040)
        info.pack(fill=X, pady=(0, 6))
        opts = ttk.Frame(body)
        opts.pack(fill=X, pady=(0, 6))
        only_big = BooleanVar(value=can_size)
        only_machines = BooleanVar(value=False)
        ttk.Checkbutton(opts, variable=only_big,
                        text=f"규모 있는 곳만 (직원 {MIN_EMPLOYEES}명+ · 면적 {MIN_AREA_M2:,}㎡+ · "
                             "금감원 등록 회사)",
                        state="normal" if can_size else "disabled").pack(side=LEFT)
        ttk.Checkbutton(opts, variable=only_machines,
                        text="기계·장비 만드는 공장만 (제어반 반복 수요)").pack(side=LEFT, padx=16)
        cols = (("#", 40), ("회사", 180), ("분야", 120), ("경기", 60), ("규모", 120),
                ("생산품", 220), ("지역", 55), ("거리", 50), ("전화", 105))
        frame = ttk.Frame(body)
        frame.pack(fill=BOTH, expand=True)
        tree = ttk.Treeview(frame, columns=[c for c, _ in cols], show="headings")
        for name, width in cols:
            tree.heading(name, text=name)
            tree.column(name, width=width, anchor=W)
        bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.pack(side=LEFT, fill=BOTH, expand=True)
        bar.pack(side=RIGHT, fill=Y)
        shown: list = []

        def rule_text() -> str:
            bits = []
            if only_big.get():
                bits.append("규모 있는 곳만")
            if only_machines.get():
                bits.append("기계·장비 만드는 공장만")
            return ", ".join(bits)

        def refresh(*_a) -> None:
            shown[:] = [c for c in ordered
                        if (not only_big.get() or is_sizable(c))
                        and (not only_machines.get() or c.kind == "maker")]
            tree.delete(*tree.get_children())
            for i, c in enumerate(shown, 1):
                tree.insert("", END, iid=str(i), values=(
                    i, c.name, c.sector, steady_text(c.sector_weight), size_text(c),
                    c.products, c.region, f"{c.distance_km:.0f}km", c.phone or "-"))
            fields = Counter(c.sector for c in shown).most_common(6)
            win.title(f"공장 찾기 — {len(shown)}곳")
            info.configure(text=(
                f"거리 안의 공장 {len(ordered)}곳 중 {len(shown)}곳"
                + (f" ({rule_text()})" if rule_text() else "") + ". 경기 덜 타는 분야 먼저, 가까운 순.\n"
                "많은 분야: " + (", ".join(f"{f} {n}" for f, n in fields) or "-") + "\n"
                "연락은 본사 구매팀보다 그 공장 시설팀·공무팀(기계 제작사면 설계팀·생산팀)에 "
                "'판넬 교체·라인 개조 때 견적 낼 수 있게 해 달라'고 하세요."
                + ("" if can_size else "\n※ 규모를 가늠할 자료가 없어 전부 보여 줍니다 — "
                                       "'기업정보 인증키'를 넣고 다시 찾으면 규모로 거를 수 있습니다.")))

        only_big.trace_add("write", refresh)
        only_machines.trace_add("write", refresh)
        refresh()

        def add_picked() -> None:
            picked = tree.selection()
            if not picked:
                messagebox.showinfo("회사를 고르세요", "목록에서 회사를 클릭해 고르세요 (Ctrl+클릭으로 여러 곳).",
                                    parent=win)
                return
            added = sum(self.lead_book.add_candidate(shown[int(i) - 1])[1] for i in picked)
            self._save_leads()
            messagebox.showinfo("넣었습니다", f"영업 목록에 {added}곳 넣었습니다.", parent=win)

        def save() -> None:
            from datetime import date
            from prime_contractor.report import write_factories_xlsx
            path = filedialog.asksaveasfilename(
                parent=win, defaultextension=".xlsx",
                initialfile=f"공장목록_{date.today():%Y%m%d}.xlsx", filetypes=[("엑셀", "*.xlsx")])
            if not path:
                return
            try:
                write_factories_xlsx(shown, path, within_km=within, rule=rule_text())
            except OSError as exc:
                messagebox.showerror("저장하지 못했습니다", f"{exc}\n\n같은 이름의 파일이 엑셀에 "
                                     "열려 있으면 닫고 다시 해주세요.", parent=win)
                return
            if messagebox.askyesno("저장 완료", f"{path}\n\n지금 보이는 {len(shown)}곳을 "
                                   "저장했습니다. 폴더를 열까요?", parent=win):
                _open_folder(Path(path).parent)

        row = ttk.Frame(body)
        row.pack(fill=X, pady=(8, 0))
        _Button(row, text="고른 회사를 영업 목록에 넣기", command=add_picked,
                bootstyle="info-outline").pack(side=LEFT)
        _Button(row, text="엑셀로 저장 (보이는 것만)", command=save,
                bootstyle="success-outline").pack(side=LEFT, padx=6)

    def _ask_profile(self, count: int) -> bool:
        """제안서에 넣을 우리 회사 정보를 작은 창에서 확인받는다. [만들기]를 누르면 True."""
        from tkinter import Toplevel
        win = Toplevel(self.root)
        win.title("제안서에 넣을 우리 회사 정보")
        win.transient(self.root)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=14)
        body.pack(fill=BOTH, expand=True)

        fields = (("회사명", self.profile_name, 24), ("설립연도", self.profile_founded, 8),
                  ("보유 인증", self.profile_certs, 24), ("대표 실적", self.profile_track, 24),
                  ("담당자", self.profile_contact, 12), ("연락처", self.profile_phone, 18))
        for i, (label, var, width) in enumerate(fields):
            row, col = divmod(i, 2)
            ttk.Label(body, text=label).grid(row=row, column=col * 2, sticky=W,
                                             padx=(0 if col == 0 else 16, 6), pady=4)
            ttk.Entry(body, textvariable=var, width=width).grid(
                row=row, column=col * 2 + 1, sticky=W, pady=4)
        ttk.Label(body, text="비워 둔 항목은 제안서에서 빠집니다. 적은 내용은 다음에도 그대로 남습니다.",
                  foreground="#666").grid(row=3, column=0, columnspan=4, sticky=W, pady=(8, 0))

        confirmed = BooleanVar(value=False)

        def go() -> None:
            confirmed.set(True)
            win.destroy()

        row = ttk.Frame(body)
        row.grid(row=4, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(row, text="취소", command=win.destroy).pack(side=RIGHT)
        _Button(row, text=f"  {count}곳 제안서 만들기  ", command=go,
                bootstyle="primary").pack(side=RIGHT, padx=6)

        win.grab_set()
        self.root.wait_window(win)
        return confirmed.get()

    def on_lead_move(self) -> None:
        lead = self._selected_lead()
        if lead is None:
            return
        stage = self.lead_stage.get()
        if stage == "첫수주" and not lead.monthly_revenue:
            from tkinter import simpledialog
            text = simpledialog.askstring(
                "축하합니다", f"{lead.name} 에서 한 달에 대략 얼마 정도 발주가 나올까요? (원)\n"
                              "모르면 비워 두세요.", parent=self.root)
            if text:
                lead.monthly_revenue = int(re.sub(r"[^\d]", "", text) or 0)
        self.lead_book.move(lead.key, stage)
        self._save_leads()

    def on_lead_edit(self) -> None:
        from tkinter import simpledialog
        lead = self._selected_lead()
        if lead is None:
            return
        action = simpledialog.askstring("다음 할 일", f"{lead.name} — 다음에 할 일은?",
                                        initialvalue=lead.next_action, parent=self.root)
        if action is None:
            return
        when = simpledialog.askstring("날짜", "언제까지? (예: 2026-10-05)  비우면 1주 뒤",
                                      initialvalue=lead.next_date, parent=self.root)
        terms = simpledialog.askstring("결제조건", "결제조건을 아시면 적어 두세요 "
                                       "(예: 현금 30일 / 어음 4개월)",
                                       initialvalue=lead.payment_terms, parent=self.root)
        lead.next_action = action.strip()
        if when:
            lead.next_date = when.strip()
        elif not lead.next_date:
            from datetime import date, timedelta
            lead.next_date = (date.today() + timedelta(days=7)).isoformat()
        if terms is not None:
            lead.payment_terms = terms.strip()
        self._save_leads()

    def on_lead_remove(self) -> None:
        lead = self._selected_lead()
        if lead and messagebox.askyesno("빼기", f"{lead.name} 을(를) 영업 목록에서 뺄까요?"):
            self.lead_book.remove(lead.key)
            self._save_leads()

    def _show_detail(self, _event=None) -> None:
        """줄을 더블클릭하면 그 후보의 점수 내역을 띄운다."""
        selected = self.tree.focus()
        if not selected or not self.result:
            return
        rank = self.tree.item(selected, "values")[0]
        try:
            cand = self.result.passed[int(rank) - 1]
        except (ValueError, IndexError):
            return
        text = cand.fitness.explain() if cand.fitness else "계산 내역이 없습니다."
        if cand.awards:
            text += "\n\n수주 내역:\n" + "\n".join(
                f"  [{a.category}] {a.title} / {a.demand_org} / {a.amount / 1e8:.2f}억"
                for a in cand.awards[:8])
        messagebox.showinfo(cand.name, text)

    def _failed(self, exc: Exception) -> None:
        messagebox.showerror(
            "잘 안 됐습니다",
            f"{redact_secrets(exc)}\n\n아래 '진행 상황' 칸에 자세한 내용이 적혀 있습니다.\n"
            "인증키가 맞는지, 인터넷이 되는지 먼저 확인해 보세요.")
        self.status.configure(text="실패")
        self._finish()

    def _finish(self) -> None:
        self.running = False
        self.run_button.configure(state="normal")

    def on_save(self) -> None:
        if not self.result:
            return
        kept = sum(1 for c in self.result.passed if c.score > EXPORT_MIN_SCORE)
        factories = len(self.result.factories)
        if not kept and not factories:
            messagebox.showinfo(
                "저장할 곳이 없습니다",
                f"{EXPORT_MIN_SCORE}점을 넘는 곳이 없습니다 (전체 {len(self.result.passed)}곳).\n"
                "조건(거리·업종·며칠치)을 넓혀서 다시 찾아보세요."
                + ("\n'작고 꾸준한 곳 위주'를 끄면 큰 곳도 점수가 올라갑니다."
                   if self.prefer_small.get() else ""))
            return
        from datetime import date
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx", initialfile=f"원청후보_{date.today():%Y%m%d}.xlsx",
            filetypes=[("엑셀", "*.xlsx")])
        if not path:
            return
        try:
            write_xlsx(self.result, path, min_score=EXPORT_MIN_SCORE)
        except OSError as exc:
            # 같은 이름 파일이 엑셀에 열려 있으면 윈도우가 덮어쓰기를 막는다.
            messagebox.showerror("저장하지 못했습니다",
                                 f"{exc}\n\n같은 이름의 파일이 엑셀에 열려 있으면 닫고 다시 해주세요.")
            return
        extra = f"\n근처 공장 {factories}곳은 '근처 공장' 시트에 따로 담았습니다." if factories else ""
        if messagebox.askyesno(
                "저장 완료",
                f"{path}\n\n" + (f"전체 {len(self.result.passed)}곳 중 {EXPORT_MIN_SCORE}점을 넘는 "
                                  f"{kept}곳만 저장했습니다." if kept else
                                  f"{EXPORT_MIN_SCORE}점을 넘는 원청 후보는 없었습니다.")
                + f"{extra}\n\n폴더를 열까요?"):
            _open_folder(Path(path).parent)


def _market_summary(market) -> str:
    """관공서 판넬 구매 기록 요약: 몇 건·얼마, 많이 산 기관, 많이 판 업체."""
    from collections import Counter
    total = sum(a.amount for a in market)
    buyers = Counter(a.demand_org for a in market if a.demand_org).most_common(5)
    sellers = Counter(a.winner_name for a in market if a.winner_name).most_common(5)
    lines = [f"관공서가 판넬·전기기기를 물품으로 산 계약 {len(market)}건, 합계 {_money(total)}원"]
    if buyers:
        lines.append("많이 산 기관: " + ", ".join(f"{n}({c}건)" for n, c in buyers))
    if sellers:
        lines.append("많이 판 업체(경쟁사): " + ", ".join(f"{n}({c}건)" for n, c in sellers))
    return "\n".join(lines)


def _money(won: int) -> str:
    """1억 넘으면 '1.2억', 아래면 '3,400만'."""
    if not won:
        return "-"
    return f"{won / 1e8:.1f}억" if won >= 1e8 else f"{won / 1e4:,.0f}만"


def _open_folder(folder: Path) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["explorer", str(folder)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", str(folder)], check=False)
        else:
            subprocess.run(["xdg-open", str(folder)], check=False)
    except OSError:
        pass


#: 밝고 차분한 파랑 계열 — 사무용 도구에 무난한 부트스트랩 테마.
#: 다른 느낌을 원하면 "cosmo"(더 쨍한 파랑), "minty"(초록), "darkly"(어두운 테마) 로 바꿔볼 수 있다.
THEME = "flatly"


def _make_root() -> Tk:
    if _HAS_BOOTSTRAP:
        window = tb.Window(themename=THEME)
        return window
    return Tk()          # ttkbootstrap 이 없는 환경(소스 직접 실행 등)에서도 앱은 떠야 한다


def main() -> int:
    root = _make_root()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
