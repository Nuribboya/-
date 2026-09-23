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

from prime_contractor.app_settings import (
    DISTANCE_CHOICES, MODE_INDUSTRY, MODE_PUBLIC, MODE_SAMPLE, MODES,
    OVERLAP_CHOICES, SECTOR_ALL, build_config, load_settings, save_settings, sector_names,
)
from prime_contractor.help_text import HELP_TEXT
from prime_contractor.config import load_config
from prime_contractor.pipeline import filter_sector, run_industry_screen, run_screen
from prime_contractor.report import write_csv
from prime_contractor.sales import load_sales, plan_to_close_gap
from prime_contractor.updater import check_for_update
from prime_contractor import __version__
from prime_contractor.build_info import label, should_check_updates

COLUMNS = (("순위", 45), ("등급", 45), ("회사 이름", 235), ("어떤 곳", 70), ("하는 일", 125),
           ("지역", 65), ("안성에서", 70), ("예상 판넬 일감", 100), ("점수", 55))
SALES_COLUMNS = (("연월", 85), ("실제 매출", 110), ("목표", 110),
                 ("달성률", 65), ("모자란 돈", 105), ("예상 손익", 110))
PLAN_COLUMNS = (("#", 35), ("등급", 45), ("회사 이름", 235), ("지역", 70),
                ("한 달 예상 금액", 120), ("합치면", 120))


def _label_for(choices: dict, value) -> str:
    """값으로 선택지 문구를 찾는다. 문구를 다듬어도 기본값이 어긋나지 않는다."""
    return next((k for k, v in choices.items() if v == value), list(choices)[0])


class QueueLogHandler(logging.Handler):
    """백그라운드 스레드의 로그를 화면 큐로 보낸다."""

    def __init__(self, sink: queue.Queue) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.put(self.format(record))


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
        self.plan = None            # 부족분 채우기 계획
        self.gap_target = 0         # 이번 실행이 메워야 할 금액 (0 이면 일반 탐색)

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

    # --- 화면 구성 -----------------------------------------------------------

    def _build_inputs(self, root, saved: dict) -> None:
        box = ttk.LabelFrame(root, text="찾을 조건", padding=10)
        box.pack(fill=X, padx=10, pady=(10, 5))

        def remembered(key: str, choices, fallback: str) -> str:
            """저장된 값이 지금 목록에 없으면(문구를 다듬었다면) 기본값으로 돌린다."""
            value = saved.get(key)
            return value if value in choices else fallback

        self.mode = StringVar(value=remembered("mode", MODES, MODE_PUBLIC))
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

        ttk.Label(box, text="어디서 찾을까요").grid(row=0, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Combobox(box, textvariable=self.mode, values=list(MODES),
                     state="readonly", width=28).grid(row=0, column=1, sticky=W, pady=4)

        ttk.Label(box, text="최근 며칠치").grid(row=0, column=2, sticky=W, padx=(20, 8))
        ttk.Entry(box, textvariable=self.days, width=10).grid(row=0, column=3, sticky=W)

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

        buttons = ttk.Frame(root)
        buttons.pack(fill=X, padx=10)
        self.run_button = ttk.Button(buttons, text="  후보 찾기  ", command=self.on_run)
        self.run_button.pack(side=LEFT)
        self.save_button = ttk.Button(buttons, text="엑셀로 저장",
                                      command=self.on_save, state="disabled")
        self.save_button.pack(side=LEFT, padx=6)
        ttk.Button(buttons, text="입력 내용 저장", command=self.on_remember).pack(side=LEFT)
        ttk.Button(buttons, text="고른 회사를 영업 목록에 넣기",
                   command=self.on_add_to_leads).pack(side=LEFT, padx=6)
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
        top = ttk.LabelFrame(root, text="매출 파일 불러오기", padding=10)
        top.pack(fill=X, padx=10, pady=(10, 5))

        self.sales_path = StringVar(value=saved.get("sales_path", ""))
        ttk.Entry(top, textvariable=self.sales_path, width=62).grid(row=0, column=0, sticky=W)
        ttk.Button(top, text="파일 찾기", command=self.on_pick_sales).grid(row=0, column=1, padx=6)
        ttk.Button(top, text="불러오기", command=self.on_load_sales).grid(row=0, column=2)
        ttk.Label(top, text="엑셀 장부(.xlsx)를 그대로 고르셔도 됩니다. '1월' 옆 칸의 금액을 읽고,\n합계 줄은 알아서 걸러냅니다.  매출 앱이 내보낸 .json 이나 .csv 도 됩니다.",
                  foreground="#666").grid(row=1, column=0, columnspan=3, sticky=W, pady=(6, 0))

        ttk.Label(top, text="월 목표").grid(row=2, column=0, sticky=W, pady=(8, 0))
        self.monthly_target = StringVar(value=str(saved.get("monthly_target", "")))
        target_row = ttk.Frame(top)
        target_row.grid(row=2, column=1, columnspan=2, sticky=W, pady=(8, 0))
        ttk.Entry(target_row, textvariable=self.monthly_target, width=16).pack(side=LEFT)
        ttk.Label(target_row, text="원  — 장부에 목표가 없을 때 여기에 적으세요",
                  foreground="#666").pack(side=LEFT, padx=6)
        ttk.Button(target_row, text="적용", command=self.on_apply_target).pack(side=LEFT)

        cost = ttk.LabelFrame(root, text="손익분기로 목표 잡기 — 이 밑으로 가면 적자인 선",
                              padding=10)
        cost.pack(fill=X, padx=10, pady=5)
        self.fixed_cost = StringVar(value=str(saved.get("fixed_cost", "")))
        self.variable_ratio = StringVar(value=str(saved.get("variable_ratio", "")))
        self.target_profit = StringVar(value=str(saved.get("target_profit", "")))

        ttk.Label(cost, text="월 고정비").grid(row=0, column=0, sticky=W)
        ttk.Entry(cost, textvariable=self.fixed_cost, width=14).grid(row=0, column=1, sticky=W)
        ttk.Label(cost, text="원  (인건비·임차료·경비·이자)", foreground="#666").grid(
            row=0, column=2, sticky=W, padx=(4, 16))
        ttk.Label(cost, text="재료·외주비").grid(row=0, column=3, sticky=W)
        ttk.Entry(cost, textvariable=self.variable_ratio, width=6).grid(row=0, column=4, sticky=W)
        ttk.Label(cost, text="%  (매출 대비)", foreground="#666").grid(
            row=0, column=5, sticky=W, padx=(4, 0))

        ttk.Label(cost, text="월 목표이익").grid(row=1, column=0, sticky=W, pady=(6, 0))
        ttk.Entry(cost, textvariable=self.target_profit, width=14).grid(
            row=1, column=1, sticky=W, pady=(6, 0))
        ttk.Label(cost, text="원  (비워두면 손익분기가 곧 목표)", foreground="#666").grid(
            row=1, column=2, sticky=W, padx=(4, 16), pady=(6, 0))
        buttons = ttk.Frame(cost)
        buttons.grid(row=1, column=3, columnspan=3, sticky=W, pady=(6, 0))
        ttk.Button(buttons, text="손익분기로 목표 잡기",
                   command=self.on_apply_breakeven).pack(side=LEFT)
        ttk.Button(buttons, text="재무제표로 채우기",
                   command=self.on_fill_from_financials).pack(side=LEFT, padx=6)
        self.cost_model = None

        mid = ttk.LabelFrame(root, text="달마다 얼마 벌었나", padding=6)
        mid.pack(fill=BOTH, expand=True, padx=10, pady=5)
        self.sales_tree = ttk.Treeview(mid, columns=[c for c, _ in SALES_COLUMNS],
                                       show="headings", height=8)
        for name, width in SALES_COLUMNS:
            self.sales_tree.heading(name, text=name)
            self.sales_tree.column(name, width=width, anchor=W)
        self.sales_tree.pack(fill=BOTH, expand=True)

        act = ttk.Frame(root)
        act.pack(fill=X, padx=10, pady=(0, 5))
        self.gap_label = ttk.Label(act, text="위에서 매출 파일을 먼저 불러오세요.", font=("", 10, "bold"))
        self.gap_label.pack(side=LEFT)
        self.months_back = StringVar(value=str(saved.get("months_back", 1)))
        ttk.Label(act, text="몇 달치로 볼까요").pack(side=LEFT, padx=(20, 4))
        ttk.Combobox(act, textvariable=self.months_back, values=["1", "2", "3", "6"],
                     state="readonly", width=4).pack(side=LEFT)
        self.gap_button = ttk.Button(act, text="  이만큼 채울 회사 찾기  ",
                                     command=self.on_find_for_gap, state="disabled")
        self.gap_button.pack(side=RIGHT)

        bottom = ttk.LabelFrame(root, text="어디에 연락하면 되나", padding=6)
        bottom.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        self.plan_tree = ttk.Treeview(bottom, columns=[c for c, _ in PLAN_COLUMNS],
                                      show="headings", height=8)
        for name, width in PLAN_COLUMNS:
            self.plan_tree.heading(name, text=name)
            self.plan_tree.column(name, width=width, anchor=W)
        self.plan_tree.pack(fill=BOTH, expand=True)
        self.plan_note = ttk.Label(bottom, text="", foreground="#333", wraplength=980)
        self.plan_note.pack(fill=X, pady=(4, 0))

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
            self.on_load_sales()

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
                             monthly_profit=profit)
        except ValueError as exc:
            if not quiet:
                messagebox.showwarning("숫자를 확인해 주세요", str(exc))
            return None

    def on_apply_breakeven(self) -> None:
        if not self.book:
            messagebox.showwarning("파일 먼저", "매출 파일을 먼저 불러오세요.")
            return
        model = self._read_cost_model()
        if model is None:
            return
        self.cost_model = model
        self.book.apply_target(model.target, overwrite=True)
        self.monthly_target.set(str(model.target))
        self._render_sales()
        self.say(f"손익분기 매출은 월 {model.breakeven / 1e4:,.0f}만원입니다. "
                 f"이 밑이면 적자입니다.")
        if model.monthly_profit:
            self.say(f"이익 {model.monthly_profit / 1e4:,.0f}만원까지 보면 "
                     f"목표는 월 {model.target / 1e4:,.0f}만원입니다.")

    def on_fill_from_financials(self) -> None:
        """손익계산서 숫자 세 개로 고정비와 비율을 채운다."""
        from tkinter import simpledialog
        from prime_contractor.breakeven import from_financials

        asks = [("연 매출액", "손익계산서의 '매출액' (원)"),
                ("매출원가", "손익계산서의 '매출원가' (원)"),
                ("판매비와관리비", "손익계산서의 '판매비와관리비' (원)")]
        values = []
        for title, prompt in asks:
            text = simpledialog.askstring(title, prompt + "\n숫자만 적어주세요.", parent=self.root)
            if text is None:
                return
            number = int(re.sub(r"[^\d]", "", text) or 0)
            if number <= 0 and title == "연 매출액":
                messagebox.showwarning("확인", "연 매출액은 0보다 커야 합니다.")
                return
            values.append(number)
        try:
            model = from_financials(*values)
        except ValueError as exc:
            messagebox.showwarning("숫자를 확인해 주세요", str(exc))
            return
        self.fixed_cost.set(str(model.monthly_fixed))
        self.variable_ratio.set(f"{model.variable_ratio * 100:.0f}")
        messagebox.showinfo(
            "채웠습니다",
            f"월 고정비 약 {model.monthly_fixed / 1e4:,.0f}만원, "
            f"재료·외주비 약 {model.variable_ratio * 100:.0f}% 로 잡았습니다.\n\n"
            "⚠ 제조업은 매출원가 안에 공장 인건비 같은 고정비가 섞여 있어서,\n"
            "이 계산은 손익분기를 실제보다 낮게 — 즉 더 안전해 보이게 — 잡습니다.\n"
            "공장 인건비·감가상각을 아시면 '월 고정비'에 더하고,\n"
            "'재료·외주비' 비율은 그만큼 낮춰서 고쳐 주세요.\n\n"
            "[손익분기로 목표 잡기]를 누르면 반영됩니다.")

    def on_apply_target(self) -> None:
        if not self.book:
            messagebox.showwarning("파일 먼저", "매출 파일을 먼저 불러오세요.")
            return
        try:
            target = int(re.sub(r"[^\d]", "", self.monthly_target.get() or "0"))
        except ValueError:
            target = 0
        if target <= 0:
            messagebox.showwarning("금액 확인", "월 목표 금액을 숫자로 적어주세요. (예: 100000000)")
            return
        self.book.apply_target(target, overwrite=True)
        self._render_sales()
        self.say(f"월 목표를 {target / 1e4:,.0f}만원으로 잡았습니다.")

    def on_load_sales(self) -> None:
        path = self.sales_path.get().strip()
        if not path:
            messagebox.showwarning("파일 필요", "매출 파일을 먼저 고르세요.")
            return
        try:
            book = load_sales(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("파일을 읽지 못했습니다",
                                 f"{exc}\n\n도움말 탭의 '매출 파일 만들기' 를 참고하세요.")
            return
        if not book.months:
            messagebox.showwarning("비어 있음", "매출 기록이 비어 있습니다.")
            return

        self.book = book
        # 저장해 둔 고정비·비율이 있으면 손익분기 목표를 바로 적용한다.
        saved_model = self._read_cost_model(quiet=True)
        if saved_model is not None:
            self.cost_model = saved_model
            book.apply_target(saved_model.target, overwrite=True)
            self.say(f"저장해 둔 비용 숫자로 손익분기(월 {saved_model.breakeven / 1e4:,.0f}만원)를 "
                     f"적용했습니다.")
            self._render_sales()
            return
        # 장부에 목표가 없으면 최근 평균을 제안해 둔다. 그대로 쓰든 고치든 사장님 몫.
        if not book.has_targets and not self.monthly_target.get().strip():
            average = book.average_revenue()
            if average:
                self.monthly_target.set(str(average))
                self.say(f"장부에 목표가 없어 최근 평균({average / 1e4:,.0f}만원)을 "
                         f"'월 목표' 칸에 넣어 두었습니다. 고치고 [적용]을 누르세요.")
        self._render_sales()

    def _render_sales(self) -> None:
        book = self.book
        model = self.cost_model
        self.sales_tree.delete(*self.sales_tree.get_children())
        for m in book.sorted_months():
            rate = f"{m.rate * 100:.0f}%" if m.rate is not None else "-"
            profit = model.profit_at(m.revenue) if model else None
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
            self.gap_button.configure(state="disabled")
            return
        if not self.book.has_targets:
            self.gap_label.configure(text="'월 목표'를 적고 [적용]을 누르세요.")
            self.gap_button.configure(state="disabled")
            return
        months_back = int(self.months_back.get() or 1)
        gap = self.book.recent_gap(months_back) if months_back > 1 else record.gap
        self.gap_target_preview = gap
        if gap:
            rate = f"{record.rate * 100:.0f}%" if record.rate is not None else "-"
            span = f"최근 {months_back}개월" if months_back > 1 else record.ym
            loss = ""
            if self.cost_model and self.cost_model.profit_at(record.revenue) < 0:
                loss = f"  · {record.ym} 은 적자입니다"
            self.gap_label.configure(
                text=f"{span}  {gap / 1e4:,.0f}만원 모자람  (목표의 {rate}){loss}")
            self.gap_button.configure(state="normal")
        else:
            self.gap_label.configure(text=f"{record.ym} 목표 달성 — 부족분 없음")
            self.gap_button.configure(state="disabled")

    def on_find_for_gap(self) -> None:
        self._refresh_gap()
        gap = getattr(self, "gap_target_preview", 0)
        if not gap:
            return
        self.gap_target = gap
        self.tabs.select(self.find_tab)   # 진행 상황이 보이도록 탐색 탭으로
        self.on_run()

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
            "mode": self.mode.get(),
            "g2b_key": self.g2b_key.get(),
            "dart_key": self.dart_key.get(),
            "nts_key": self.nts_key.get(),
            "days": self.days.get(),
            "distance": self.distance.get(),
            "overlap": self.overlap.get(),
            "sector": self.sector.get(),
            "include_demand_orgs": self.include_orgs.get(),
            "sales_path": self.sales_path.get(),
            "monthly_target": self.monthly_target.get(),
            "fixed_cost": self.fixed_cost.get(),
            "variable_ratio": self.variable_ratio.get(),
            "target_profit": self.target_profit.get(),
            "months_back": self.months_back.get(),
            "goal_target": self.goal_target.get(),
            "goal_current": self.goal_current.get(),
            "goal_months": self.goal_months.get(),
            "goal_per_client": self.goal_per_client.get(),
            "goal_cash": self.goal_cash.get(),
        }

    def on_remember(self) -> None:
        path = save_settings(self.current_options())
        messagebox.showinfo("저장했습니다",
                            f"다음에 앱을 켤 때 자동으로 채워집니다.\n\n{path}\n\n"
                            "인증키가 그대로 적혀 저장되니, 여러 사람이 쓰는 PC 에서는\n"
                            "이 버튼을 누르지 마세요.")

    def on_run(self) -> None:
        if self.running:
            return
        options = self.current_options()
        mode = options["mode"]
        if mode == MODE_PUBLIC and not options["g2b_key"].strip():
            messagebox.showwarning(
                "인증키가 필요합니다",
                "'나라장터 인증키' 칸을 채워주세요.\n\n"
                "키가 아직 없으시면 '어디서 찾을까요' 를\n"
                f"'{MODE_SAMPLE}' 로 바꾸면 그냥 해보실 수 있습니다.\n\n"
                "키 받는 곳은 도움말 탭에 적어두었습니다.")
            return
        if mode == MODE_INDUSTRY and not options["dart_key"].strip():
            messagebox.showwarning(
                "인증키가 필요합니다",
                f"'{MODE_INDUSTRY}' 에는 '기업정보 인증키' 가 필요합니다.\n"
                "받는 곳은 도움말 탭에 적어두었습니다.")
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
            self.say(f"오류: {exc}")
            self.root.after(0, self._failed, exc)
        finally:
            root_log.removeHandler(handler)

    def _screen(self, options: dict):
        cfg = build_config(options)
        if self.book and self.book.average_revenue():
            from dataclasses import replace
            ours = self.book.average_revenue()
            cfg = replace(cfg, our_monthly_revenue=ours)
            self.say(f"우리 월매출 {ours / 1e4:,.0f}만원 기준으로, 너무 작거나 너무 큰 곳은 "
                     f"점수를 낮춥니다.")
        else:
            self.say("② 탭에서 매출 장부를 불러오면 '우리 크기에 맞는 곳'으로 점수를 매깁니다.")
        mode = options["mode"]

        if mode == MODE_SAMPLE:
            self.say("연습용 가짜 자료로 돌립니다. 여기 나오는 회사는 실제로 없는 곳입니다.")
            result = run_screen(cfg, offline=True)
        elif mode == MODE_INDUSTRY:
            from prime_contractor.sources.dart import DartClient
            self.say("상장 회사 목록을 하나씩 확인합니다. 처음에는 몇 분 걸립니다…")
            result = run_industry_screen(cfg, DartClient(cfg.dart_api_key))
        else:
            from prime_contractor.sources.g2b import G2BClient
            self.say(f"나라장터에서 최근 {cfg.lookback_days}일치 공사를 찾아봅니다…")
            dart = nts = None
            if cfg.dart_api_key:
                from prime_contractor.sources.dart import DartClient
                dart = DartClient(cfg.dart_api_key)
            if cfg.nts_service_key:
                from prime_contractor.sources.nts import NtsClient
                nts = NtsClient(cfg.nts_service_key)
                self.say("폐업한 회사는 국세청에 확인해서 빼겠습니다.")
            result = run_screen(cfg, g2b_client=G2BClient(cfg.g2b_service_key),
                                dart_client=dart, nts_client=nts)

        sector = options.get("sector")
        if sector and sector != SECTOR_ALL:
            filter_sector(result, sector)
        return result

    def _done(self, result) -> None:
        self.result = result
        for i, c in enumerate(result.passed, 1):
            dist = f"{c.distance_km:.0f}km" if c.distance_km is not None else "미상"
            est = getattr(c.fitness, "est_panel_amount", 0)
            self.tree.insert("", END, values=(
                i, c.grade or "-", c.name, "원청" if c.kind == "contractor" else "발주처",
                c.sector or "미분류", c.region or "미상", dist,
                f"{est / 1e8:.1f}억" if est else "-", f"{c.score:.1f}"),
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
        if self.gap_target:
            self._fill_plan(result)
        self.status.configure(text=f"{len(result.passed)}곳 찾음")
        self.save_button.configure(state="normal" if result.passed else "disabled")
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
        ttk.Button(box, text="  계산하기  ", command=self.on_goal).grid(
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
        cols = (("회사", 200), ("등급", 45), ("단계", 75), ("다음 할 일", 230),
                ("날짜", 90), ("결제조건", 150))
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
        ttk.Button(act, text="빼기", command=self.on_lead_remove).pack(side=LEFT)
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
                lead.next_date or "-", lead.payment_terms or "-"),
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

    def _fill_plan(self, result) -> None:
        """부족분을 채울 후보를 골라 매출 탭에 띄운다."""
        cfg = build_config(self.current_options())
        plan = plan_to_close_gap(self.gap_target, result.passed,
                                 lookback_days=cfg.lookback_days)
        self.plan = plan
        self.plan_tree.delete(*self.plan_tree.get_children())
        for i, row in enumerate(plan.rows, 1):
            c = row.candidate
            mark = " ✔" if row.cumulative >= plan.gap else ""
            self.plan_tree.insert("", END, values=(
                i, c.grade or "-", c.name, c.region or "미상",
                f"{row.monthly_expected / 1e4:,.0f}만원",
                f"{row.cumulative / 1e4:,.0f}만원{mark}"))
        self.plan_note.configure(
            text=plan.note + "\n'한 달 예상 금액' 은 그 회사가 최근에 한 공사 규모에서 판넬 몫을"
                 " 잡고, 연락했을 때 실제로 일이 올 확률(A 35% · B 25% · C 15% · D 8%)을"
                 " 곱한 값입니다. 모두 어림짐작이니 연락할 순서를 정하는 데만 쓰세요.")
        self.gap_target = 0
        self.tabs.select(self.sales_tab)
        self.say(f"모자란 만큼 채우려면: {plan.note}")

    def _failed(self, exc: Exception) -> None:
        messagebox.showerror(
            "잘 안 됐습니다",
            f"{exc}\n\n아래 '진행 상황' 칸에 자세한 내용이 적혀 있습니다.\n"
            "인증키가 맞는지, 인터넷이 되는지 먼저 확인해 보세요.")
        self.status.configure(text="실패")
        self._finish()

    def _finish(self) -> None:
        self.running = False
        self.run_button.configure(state="normal")

    def on_save(self) -> None:
        if not self.result:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="원청후보.csv",
            filetypes=[("CSV (엑셀)", "*.csv")])
        if not path:
            return
        write_csv(self.result, path, include_excluded=True)
        if messagebox.askyesno("저장 완료", f"{path}\n\n폴더를 열까요?"):
            _open_folder(Path(path).parent)


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


def main() -> int:
    root = Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
