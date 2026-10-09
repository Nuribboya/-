"""원청 찾기 데스크톱 앱 (tkinter).

터미널 없이 쓰기 위한 화면이다. 공장 찾기 로직은 makers 쪽에 있고, 여기서는
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
    DISTANCE_CHOICES, apply_target, build_config, load_settings, save_settings,
)
from prime_contractor.help_text import HELP_TEXT
from prime_contractor.sales import load_sales
from prime_contractor.textutil import redact_secrets
from prime_contractor.updater import check_for_update
from prime_contractor import __version__
from prime_contractor.build_info import label, should_check_updates

SALES_COLUMNS = (("연월", 85), ("실제 매출", 120), ("목표", 130),
                 ("달성률", 75), ("모자란 돈", 120), ("손익", 130))
REVIEW_COLUMNS = (("연월", 85), ("매출", 110), ("평소 대비", 90), ("손익", 110),
                  ("판정", 520))
FACTORY_COLUMNS = (("#", 40), ("회사", 175), ("그룹", 125), ("분야", 115), ("경기", 55),
                   ("규모", 95), ("생산품", 190), ("지역", 55), ("거리", 50), ("전화", 100))
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
        self.factories: list = []       # 공장 찾기 결과 (거르기 전)
        self.shown: list = []           # 지금 표에 보이는 것
        self.running = False
        saved = load_settings()
        self._saved = saved             # 화면에 없는 예전 설정(나라장터 키 등)은 지우지 않는다

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
        self.tabs.add(find_tab, text="  ① 공장 찾기  ")
        self.tabs.add(sales_tab, text="  ② 매출 보고 채우기  ")
        self.tabs.add(help_tab, text="  도움말  ")

        self.find_tab, self.sales_tab, self.goal_tab = find_tab, sales_tab, goal_tab
        self._build_find(find_tab, saved)
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
            save_settings({**self._saved, **self.current_options()})
        except OSError:
            pass
        self.root.after(AUTOSAVE_MS, self._autosave)

    def on_close(self) -> None:
        try:
            save_settings({**self._saved, **self.current_options()})
        except OSError:
            pass
        self.root.destroy()

    # --- 화면 구성 -----------------------------------------------------------

    def _build_find(self, root, saved: dict) -> None:
        """① 공장 찾기: 등록공장 파일 → 거리 안의 공장 → 상장사·계열사·규모로 거르기.

        나라장터(공사를 따낸 회사)는 뺐다. 그중엔 판넬을 직접 만드는 경쟁사가 섞이고,
        판넬을 실제로 쓰는 쪽은 공장이다 — 특히 규모 있는 상장사·계열사 공장.
        """
        box = ttk.LabelFrame(root, text="찾을 조건", padding=10)
        box.pack(fill=X, padx=10, pady=(10, 5))

        self.factory_file = StringVar(value=saved.get("factory_file", ""))
        self.dart_key = StringVar(value=saved.get("dart_key", ""))
        # 공공데이터포털 키는 나라장터 때 쓰던 것과 같은 키다(같은 계정이면 서비스마다 같음).
        self.data_key = StringVar(value=saved.get("data_key") or saved.get("g2b_key", ""))
        value = saved.get("distance")
        self.distance = StringVar(value=value if value in DISTANCE_CHOICES
                                  else _label_for(DISTANCE_CHOICES, 70.0))

        ttk.Label(box, text="공공데이터포털 인증키").grid(row=0, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Entry(box, textvariable=self.data_key, width=46, show="•").grid(
            row=0, column=1, sticky=W, pady=4)
        ttk.Label(box, text="data.go.kr — 공장 정보 조회", foreground="#666").grid(
            row=0, column=2, sticky=W, padx=6)
        ttk.Label(box, text="기업정보 인증키").grid(row=1, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Entry(box, textvariable=self.dart_key, width=46, show="•").grid(
            row=1, column=1, sticky=W, pady=4)
        ttk.Label(box, text="opendart.fss.or.kr — 상장사·계열사 확인", foreground="#666").grid(
            row=1, column=2, sticky=W, padx=6)
        ttk.Label(box, text="안성에서 얼마나").grid(row=2, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Combobox(box, textvariable=self.distance, values=list(DISTANCE_CHOICES),
                     state="readonly", width=18).grid(row=2, column=1, sticky=W, pady=4)
        ttk.Label(box, text="공장 목록 파일 (선택)").grid(row=3, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Entry(box, textvariable=self.factory_file, width=46).grid(
            row=3, column=1, sticky=W, pady=4)
        ttk.Button(box, text="파일 찾기", command=self.on_pick_factory_file).grid(
            row=3, column=2, sticky=W, padx=6)
        ttk.Label(box, foreground="#666", justify=LEFT, text=(
            "인증키 두 개만 넣으면 상장사·계열사 공장을 바로 찾아옵니다. 파일은 비워 두세요 — "
            "비상장 작은 공장까지 다 보고 싶을 때만 '전국등록공장현황' 파일을 고릅니다.")).grid(
            row=4, column=0, columnspan=4, sticky=W, pady=(4, 0))

        buttons = ttk.Frame(root)
        buttons.pack(fill=X, padx=10)
        self.run_button = _Button(buttons, text="  공장 찾기  ", command=self.on_find_factories,
                                  bootstyle="primary")
        self.run_button.pack(side=LEFT)
        self.only_listed = BooleanVar(value=True)       # 상장사 + 그 계열사
        self.only_big = BooleanVar(value=False)
        self.only_machines = BooleanVar(value=False)
        from prime_contractor.makers import MIN_AREA_M2, MIN_EMPLOYEES
        self.filter_boxes = [
            ttk.Checkbutton(buttons, variable=self.only_listed, text="상장사·계열사 공장만"),
            ttk.Checkbutton(buttons, variable=self.only_big,
                            text=f"규모 있는 곳만 (직원 {MIN_EMPLOYEES}명+·{MIN_AREA_M2:,}㎡+·금감원 등록)"),
            ttk.Checkbutton(buttons, variable=self.only_machines, text="기계·장비 만드는 공장만"),
        ]
        for box_ in self.filter_boxes:
            box_.pack(side=LEFT, padx=(14, 0))
            box_.configure(state="disabled")
        for var in (self.only_listed, self.only_big, self.only_machines):
            var.trace_add("write", lambda *_a: self._refresh_factories())
        self.status = ttk.Label(buttons, text="준비됨")
        self.status.pack(side=RIGHT)

        frame = ttk.Frame(root)
        frame.pack(fill=BOTH, expand=True, padx=10, pady=(8, 4))
        self.tree = ttk.Treeview(frame, columns=[c for c, _ in FACTORY_COLUMNS], show="headings")
        for name, width in FACTORY_COLUMNS:
            self.tree.heading(name, text=name)
            self.tree.column(name, width=width, anchor=W)
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        bar.pack(side=RIGHT, fill=Y)

        act = ttk.Frame(root)
        act.pack(fill=X, padx=10)
        self.lead_add_button = _Button(act, text="고른 회사를 영업 목록에 넣기",
                                       command=self.on_add_to_leads, state="disabled",
                                       bootstyle="info-outline")
        self.lead_add_button.pack(side=LEFT)
        self.save_button = _Button(act, text="엑셀로 저장 (보이는 것만)", command=self.on_save,
                                   state="disabled", bootstyle="success-outline")
        self.save_button.pack(side=LEFT, padx=6)
        self.factory_info = ttk.Label(act, text="", foreground="#333", wraplength=700, justify=LEFT)
        self.factory_info.pack(side=LEFT, padx=10)

        logbox = ttk.LabelFrame(root, text="진행 상황 (여기에 설명이 나옵니다)", padding=6)
        logbox.pack(fill=X, padx=10, pady=(6, 10))
        self.log = scrolledtext.ScrolledText(logbox, height=5, state="disabled")
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
            "dart_key": self.dart_key.get(),
            "data_key": self.data_key.get(),
            "distance": self.distance.get(),
            "factory_file": self.factory_file.get(),
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

    # --- 공장 찾기 -----------------------------------------------------------------

    def on_pick_factory_file(self) -> None:
        start = Path(self.factory_file.get()).parent if self.factory_file.get() else None
        path = filedialog.askopenfilename(
            title="전국 등록공장 현황 파일을 고르세요 (공공데이터포털·팩토리온)",
            initialdir=str(start) if start and start.exists() else None,
            filetypes=[("공장 목록", "*.csv *.xlsx"), ("모든 파일", "*.*")])
        if path:
            self.factory_file.set(path)

    def on_find_factories(self) -> None:
        """등록공장 파일에서 근처 공장을 고른다 — 판넬을 실제로 쓰는 곳."""
        if self.running:
            return
        path = self.factory_file.get().strip()
        if path and not Path(path).exists():
            messagebox.showwarning("파일이 없습니다", f"{path}\n\n공장 목록 파일을 다시 고르거나 "
                                   "칸을 비워 두세요(인증키로 찾습니다).")
            return
        if not path and not (self.data_key.get().strip() and self.dart_key.get().strip()):
            messagebox.showwarning(
                "인증키가 필요합니다",
                "'공공데이터포털 인증키'와 '기업정보 인증키'를 둘 다 넣어 주세요.\n\n"
                "받는 곳은 도움말 탭에 적어 두었습니다.")
            return
        cfg = build_config(self.current_options())
        self.running = True
        self.run_button.configure(state="disabled")
        self.status.configure(text="찾는 중…")
        if path:
            self.say("공장 목록을 읽는 중입니다 (몇십만 줄이면 1분쯤 걸립니다)…")
            target = self._load_factories
            args = (path, cfg)
        else:
            self.say("시작합니다 — 저장해 둔 상장사 목록을 읽는 중입니다 (처음이면 DART 에서 받느라 "
                     "1분쯤 걸립니다)…")
            target = self._load_factories_by_key
            args = (self.data_key.get().strip(), cfg)
        threading.Thread(target=target, args=args, daemon=True).start()

    def _load_factories(self, path: str, cfg) -> None:
        from prime_contractor.makers import (
            build_group_map, find_factories, has_size_columns, listed_names,
            mark_dart_registered, mark_groups, read_factory_file)
        try:
            records = read_factory_file(path)
            factories = find_factories(records, within_km=cfg.within_km)
        except (OSError, ValueError) as exc:
            self.root.after(0, self._factories_failed, str(exc))
            return
        sized = has_size_columns(records)
        knows_listed = False
        if cfg.dart_api_key:
            # 상장사(코스피·코스닥)인지, 금감원에 등록된 규모인지 DART 목록과 맞춰 본다.
            # 거리는 이 파일의 '공장 주소'로 쟀으니 본사가 서울인 회사의 안성 공장도 잡힌다.
            from prime_contractor.sources.dart import DartClient
            try:
                dart = DartClient(cfg.dart_api_key)
                listed = dart.listed_companies
                registered, on_market = mark_dart_registered(
                    factories, dart.corp_index, listed_names(listed))
                knows_listed = True
                self.say(f"그중 상장사(코스피·코스닥) 공장 {on_market}곳, 금감원 등록 회사 "
                         f"{registered}곳입니다.")
                fresh = sum(1 for _n, code, stock in listed
                            if stock and not dart.has_investments(code))
                if fresh:
                    self.say(f"상장사 {fresh}곳의 계열사(지분 가진 회사)를 확인합니다 — 처음 "
                             "한 번은 10분쯤 걸리고, 다음부터는 저장해 둔 걸 씁니다…")
                group_map = build_group_map(
                    listed, dart.investments,
                    progress=lambda done, total: self.say(f"  계열사 확인 {done}/{total}"))
                dart.save_investment_cache()
                tagged = mark_groups(factories, group_map, listed, cfg.incumbent)
                affiliates = sum(1 for c in factories if c.group.endswith(" 계열"))
                self.say(f"상장사 계열사 공장 {affiliates}곳을 더 찾았습니다 "
                         f"(그룹이 붙은 공장 모두 {tagged}곳).")
            except Exception as exc:              # 상장·규모 표시만 빠지고 목록은 그대로
                self.say(f"DART 확인 실패(목록은 그대로): {redact_secrets(exc)}")
        else:
            self.say("'기업정보 인증키'가 없어 상장사인지 확인하지 못했습니다. 키를 넣으면 "
                     "상장사·계열사 공장만 골라 볼 수 있습니다.")
        self.say(f"공장 {len(records):,}곳 중 거리 안의 공장 {len(factories)}곳 (판넬 업체 제외). "
                 "연락은 그 공장 시설팀·공무팀(기계 제작사면 설계팀·생산팀)에 하세요.")
        self.root.after(0, self._factories_loaded, factories, cfg.within_km,
                        sized or knows_listed, knows_listed)

    def _load_factories_by_key(self, data_key: str, cfg) -> None:
        """파일 없이: 상장사(제조업)·계열사 이름으로 공장등록 API 를 물어 공장을 모은다."""
        from prime_contractor.makers import (
            affiliate_names, build_group_map, collect_by_name, find_factories,
            listed_names, mark_dart_registered, mark_groups, sort_listed)
        from prime_contractor.sources.dart import DartClient
        from prime_contractor.sources.factory_api import FactoryApi
        try:
            dart = DartClient(cfg.dart_api_key)
            api = FactoryApi(data_key, cache_dir=dart.cache_dir)
            listed = [row for row in dart.listed_companies if row[2]]
            self.say(f"상장사 {len(listed)}곳 중 제조업만 고릅니다 — 처음 한 번은 몇 분 걸리고, "
                     "다음부터는 저장해 둔 걸 씁니다…")
            makers, heads = sort_listed(
                listed, dart.company,
                progress=lambda done, total: self.say(f"  업종 확인 {done}/{total}"))
            dart.save_company_cache()
            self.say(f"제조업 상장사 {len(makers)}곳. 이들과 지주회사 {len(heads) - len(makers)}곳의 "
                     "계열사를 확인합니다…")
            failed: list[str] = []

            def group_progress(done: int, total: int) -> None:
                self.say(f"  계열사 확인 {done}/{total}")
                if done % 500 == 0:                # 중간에 창을 닫아도 받은 데까지는 남긴다
                    dart.save_investment_cache()

            group_map = build_group_map(heads, dart.investments, failed=failed,
                                        progress=group_progress)
            dart.save_investment_cache()
            if dart.invest_stopped:
                self.say(f"⚠ {dart.invest_stopped}")
            elif failed:
                self.say(f"  {len(failed)}곳은 DART 응답이 없어 건너뜀 — 다음에 누르면 다시 확인합니다.")
            queries = list(dict.fromkeys([name for name, _c, _s in makers]
                                         + affiliate_names(heads, dart.cached_investments)))
            fresh = sum(1 for q in queries if not api.cached(q))
            if fresh:
                self.say("공장 조회 API 를 시험합니다…")
                self.say(f"  시험 조회 성공 — 삼성전자 공장 {api.check()}곳을 읽었습니다.")
            self.say(f"회사 {len(queries)}곳의 공장을 공공데이터포털에 묻습니다"
                     + (f" (새로 {fresh}곳 — 처음엔 오래 걸립니다)…" if fresh else "…"))
            if fresh > 900:
                self.say("  공공데이터포털 개발계정은 하루 1,000번까지라 며칠에 나눠 받습니다 — "
                         "받은 건 저장되니 다음 날 다시 누르면 이어서 받습니다.")
            records, stopped = collect_by_name(
                queries, api.factories_of,
                progress=lambda done, total: self.say(f"  공장 조회 {done}/{total}"))
            api.save_cache()
            if stopped:
                self.say(f"⚠ {stopped} 받은 데까지만 보여 줍니다 — 내일 다시 누르면 이어서 받습니다.")
            if not records and api.sample_keys:
                self.say("공장 칸을 못 읽었습니다. 받은 칸 이름: " + ", ".join(api.sample_keys))
            if not records and not stopped:
                raise RuntimeError("공장을 하나도 받지 못했습니다. 이 창과 진행 기록을 캡처해 보내 주세요.")
            factories = find_factories(records, within_km=cfg.within_km)
            registered, on_market = mark_dart_registered(
                factories, dart.corp_index, listed_names(listed))
            mark_groups(factories, group_map, listed, cfg.incumbent)
        except Exception as exc:
            self.root.after(0, self._factories_failed, redact_secrets(exc))
            return
        self.say(f"공장 {len(records):,}곳 중 거리 안의 공장 {len(factories)}곳 (판넬 업체 제외, "
                 f"상장사 공장 {on_market}곳). 연락은 그 공장 시설팀·공무팀에 하세요.")
        self.root.after(0, self._factories_loaded, factories, cfg.within_km, True, True)

    def _factories_failed(self, message: str) -> None:
        self.running = False
        self.run_button.configure(state="normal")
        self.status.configure(text="실패")
        messagebox.showerror("공장 목록을 읽지 못했습니다", message)

    def _factories_loaded(self, factories, within, can_size: bool, knows_listed: bool) -> None:
        self.running = False
        self.run_button.configure(state="normal")
        # 가까운 순. 적합도 점수는 아직 없다 — 거리로만 줄 세운다.
        self.factories = sorted(factories, key=lambda c: (c.distance_km, c.name))
        self.factory_within = within
        self.filter_boxes[0].configure(state="normal" if knows_listed else "disabled")
        self.filter_boxes[1].configure(state="normal" if can_size else "disabled")
        self.filter_boxes[2].configure(state="normal")
        # 작은 공장은 수작업이 많아 판넬 수요가 적다 — 상장사·계열사 공장을 기본으로 본다.
        self.only_listed.set(knows_listed)
        self.only_big.set(can_size and not knows_listed)
        self._refresh_factories()
        if not factories:
            messagebox.showinfo("공장 찾기", "거리 안에서 공장을 찾지 못했습니다.\n"
                                          "'안성에서 얼마나'를 넓혀 보세요.")

    def _filter_rule(self) -> str:
        bits = []
        if self.only_listed.get():
            bits.append("상장사·계열사 공장만")
        if self.only_big.get():
            bits.append("규모 있는 곳만")
        if self.only_machines.get():
            bits.append("기계·장비 만드는 공장만")
        return ", ".join(bits)

    def _refresh_factories(self) -> None:
        from collections import Counter
        from prime_contractor.makers import is_sizable, size_text, steady_text
        self.shown = [c for c in self.factories
                      if (not self.only_listed.get() or c.stock_code or c.group)
                      and (not self.only_big.get() or is_sizable(c))
                      and (not self.only_machines.get() or c.kind == "maker")]
        self.tree.delete(*self.tree.get_children())
        for i, c in enumerate(self.shown, 1):
            self.tree.insert("", END, iid=str(i), values=(
                i, c.name, c.group or "-", c.sector, steady_text(c.sector_weight), size_text(c),
                c.products, c.region, f"{c.distance_km:.0f}km", c.phone or "-"))
        if not self.factories:
            self.status.configure(text="준비됨")
            return
        self.status.configure(text=f"{len(self.factories)}곳 중 {len(self.shown)}곳")
        fields = Counter(c.sector for c in self.shown).most_common(4)
        self.factory_info.configure(text=(
            "많은 분야: " + (", ".join(f"{f} {n}" for f, n in fields) or "-")))
        state = "normal" if self.shown else "disabled"
        self.save_button.configure(state=state)
        self.lead_add_button.configure(state=state)

    def on_add_to_leads(self) -> None:
        picked = self.tree.selection()
        if not picked:
            messagebox.showinfo("회사를 고르세요", "표에서 회사를 클릭해 고르세요 (Ctrl+클릭으로 여러 곳).")
            return
        added = sum(self.lead_book.add_candidate(self.shown[int(i) - 1])[1] for i in picked)
        self._save_leads()
        self.say(f"영업 목록에 {added}곳 넣었습니다. ★ 최우선 목표 탭에서 진행을 기록하세요.")

    def on_save(self) -> None:
        from datetime import date
        from prime_contractor.makers import write_factories_xlsx
        if not self.shown:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx", initialfile=f"공장목록_{date.today():%Y%m%d}.xlsx",
            filetypes=[("엑셀", "*.xlsx")])
        if not path:
            return
        try:
            write_factories_xlsx(self.shown, path, within_km=self.factory_within,
                                 rule=self._filter_rule())
        except OSError as exc:
            # 같은 이름 파일이 엑셀에 열려 있으면 윈도우가 덮어쓰기를 막는다.
            messagebox.showerror("저장하지 못했습니다",
                                 f"{exc}\n\n같은 이름의 파일이 엑셀에 열려 있으면 닫고 다시 해주세요.")
            return
        if messagebox.askyesno("저장 완료", f"{path}\n\n지금 보이는 {len(self.shown)}곳을 "
                               "저장했습니다. 폴더를 열까요?"):
            _open_folder(Path(path).parent)

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
