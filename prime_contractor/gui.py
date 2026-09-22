"""원청 찾기 데스크톱 앱 (tkinter).

터미널 없이 쓰기 위한 화면이다. 탐색 로직은 전부 pipeline 쪽에 있고, 여기서는
입력을 받아 넘기고 결과를 보여 주기만 한다.

    python -m prime_contractor.gui
"""
from __future__ import annotations

import logging
import queue
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
from prime_contractor.config import load_config
from prime_contractor.pipeline import filter_sector, run_industry_screen, run_screen
from prime_contractor.report import write_csv
from prime_contractor.sales import load_sales, plan_to_close_gap

COLUMNS = (("순위", 45), ("등급", 45), ("업체/기관", 240), ("구분", 55), ("업종", 125),
           ("지역", 65), ("거리", 60), ("추정판넬", 85), ("적합도", 60))
SALES_COLUMNS = (("연월", 90), ("매출", 110), ("목표", 110), ("달성률", 70), ("부족", 110))
PLAN_COLUMNS = (("#", 35), ("등급", 45), ("업체/기관", 240), ("지역", 70),
                ("기대 월매출", 110), ("누적", 110))


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

        root.title("원청 찾기 — 자동제어 판넬")
        root.geometry("1060x740")
        root.minsize(920, 620)

        self.book = None            # 불러온 매출 기록
        self.plan = None            # 부족분 채우기 계획
        self.gap_target = 0         # 이번 실행이 메워야 할 금액 (0 이면 일반 탐색)

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill=BOTH, expand=True)
        find_tab = ttk.Frame(self.tabs)
        sales_tab = ttk.Frame(self.tabs)
        self.tabs.add(find_tab, text="  원청 찾기  ")
        self.tabs.add(sales_tab, text="  매출 · 부족분  ")

        self._build_inputs(find_tab, saved)
        self._build_table(find_tab)
        self._build_log(find_tab)
        self._build_sales(sales_tab, saved)
        self._pump_messages()

    # --- 화면 구성 -----------------------------------------------------------

    def _build_inputs(self, root, saved: dict) -> None:
        box = ttk.LabelFrame(root, text="조건", padding=10)
        box.pack(fill=X, padx=10, pady=(10, 5))

        self.mode = StringVar(value=saved.get("mode", MODE_PUBLIC))
        self.g2b_key = StringVar(value=saved.get("g2b_key", ""))
        self.dart_key = StringVar(value=saved.get("dart_key", ""))
        self.days = StringVar(value=str(saved.get("days", 90)))
        self.distance = StringVar(value=saved.get("distance", "70km 이내"))
        self.overlap = StringVar(value=saved.get("overlap", list(OVERLAP_CHOICES)[0]))
        self.sector = StringVar(value=saved.get("sector", SECTOR_ALL))
        self.include_orgs = BooleanVar(value=saved.get("include_demand_orgs", True))

        ttk.Label(box, text="탐색 방식").grid(row=0, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Combobox(box, textvariable=self.mode, values=list(MODES),
                     state="readonly", width=28).grid(row=0, column=1, sticky=W, pady=4)

        ttk.Label(box, text="조회 기간(일)").grid(row=0, column=2, sticky=W, padx=(20, 8))
        ttk.Entry(box, textvariable=self.days, width=10).grid(row=0, column=3, sticky=W)

        ttk.Label(box, text="나라장터 키").grid(row=1, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Entry(box, textvariable=self.g2b_key, width=46, show="•").grid(
            row=1, column=1, columnspan=2, sticky=W, pady=4)

        ttk.Label(box, text="DART 키(선택)").grid(row=2, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Entry(box, textvariable=self.dart_key, width=46, show="•").grid(
            row=2, column=1, columnspan=2, sticky=W, pady=4)

        ttk.Label(box, text="납품 반경").grid(row=3, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Combobox(box, textvariable=self.distance, values=list(DISTANCE_CHOICES),
                     state="readonly", width=18).grid(row=3, column=1, sticky=W, pady=4)

        ttk.Label(box, text="업종").grid(row=3, column=2, sticky=W, padx=(20, 8))
        self.sector_box = ttk.Combobox(box, textvariable=self.sector,
                                       values=sector_names(load_config()),
                                       state="readonly", width=22)
        self.sector_box.grid(row=3, column=3, sticky=W)

        ttk.Label(box, text="기존 원청 제외 범위").grid(row=4, column=0, sticky=W, padx=(0, 8), pady=4)
        ttk.Combobox(box, textvariable=self.overlap, values=list(OVERLAP_CHOICES),
                     state="readonly", width=30).grid(row=4, column=1, columnspan=2,
                                                      sticky=W, pady=4)

        ttk.Checkbutton(box, text="발주기관도 후보에 포함",
                        variable=self.include_orgs).grid(row=4, column=3, sticky=W)

        buttons = ttk.Frame(root)
        buttons.pack(fill=X, padx=10)
        self.run_button = ttk.Button(buttons, text="찾기 시작", command=self.on_run)
        self.run_button.pack(side=LEFT)
        self.save_button = ttk.Button(buttons, text="엑셀(CSV)로 저장",
                                      command=self.on_save, state="disabled")
        self.save_button.pack(side=LEFT, padx=6)
        ttk.Button(buttons, text="입력값 기억", command=self.on_remember).pack(side=LEFT)
        self.status = ttk.Label(buttons, text="대기 중")
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
        box = ttk.LabelFrame(root, text="진행 상황", padding=6)
        box.pack(fill=X, padx=10, pady=(0, 10))
        self.log = scrolledtext.ScrolledText(box, height=7, state="disabled")
        self.log.pack(fill=X)

    def _build_sales(self, root, saved: dict) -> None:
        top = ttk.LabelFrame(root, text="매출 기록 불러오기", padding=10)
        top.pack(fill=X, padx=10, pady=(10, 5))

        self.sales_path = StringVar(value=saved.get("sales_path", ""))
        ttk.Entry(top, textvariable=self.sales_path, width=62).grid(row=0, column=0, sticky=W)
        ttk.Button(top, text="파일 찾기", command=self.on_pick_sales).grid(row=0, column=1, padx=6)
        ttk.Button(top, text="불러오기", command=self.on_load_sales).grid(row=0, column=2)
        ttk.Label(top, text="매출 앱이 내보낸 JSON 또는  연월,매출,목표  형식의 CSV",
                  foreground="#666").grid(row=1, column=0, columnspan=3, sticky=W, pady=(6, 0))

        mid = ttk.LabelFrame(root, text="월별 실적", padding=6)
        mid.pack(fill=BOTH, expand=True, padx=10, pady=5)
        self.sales_tree = ttk.Treeview(mid, columns=[c for c, _ in SALES_COLUMNS],
                                       show="headings", height=8)
        for name, width in SALES_COLUMNS:
            self.sales_tree.heading(name, text=name)
            self.sales_tree.column(name, width=width, anchor=W)
        self.sales_tree.pack(fill=BOTH, expand=True)

        act = ttk.Frame(root)
        act.pack(fill=X, padx=10, pady=(0, 5))
        self.gap_label = ttk.Label(act, text="매출 파일을 불러오세요.", font=("", 10, "bold"))
        self.gap_label.pack(side=LEFT)
        self.months_back = StringVar(value=str(saved.get("months_back", 1)))
        ttk.Label(act, text="부족분 집계 개월").pack(side=LEFT, padx=(20, 4))
        ttk.Combobox(act, textvariable=self.months_back, values=["1", "2", "3", "6"],
                     state="readonly", width=4).pack(side=LEFT)
        self.gap_button = ttk.Button(act, text="이 부족분 채울 후보 찾기",
                                     command=self.on_find_for_gap, state="disabled")
        self.gap_button.pack(side=RIGHT)

        bottom = ttk.LabelFrame(root, text="접촉 계획", padding=6)
        bottom.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        self.plan_tree = ttk.Treeview(bottom, columns=[c for c, _ in PLAN_COLUMNS],
                                      show="headings", height=8)
        for name, width in PLAN_COLUMNS:
            self.plan_tree.heading(name, text=name)
            self.plan_tree.column(name, width=width, anchor=W)
        self.plan_tree.pack(fill=BOTH, expand=True)
        self.plan_note = ttk.Label(bottom, text="", foreground="#333", wraplength=980)
        self.plan_note.pack(fill=X, pady=(4, 0))

    # --- 매출 동작 -----------------------------------------------------------

    def on_pick_sales(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("매출 기록", "*.json *.csv"), ("모든 파일", "*.*")])
        if path:
            self.sales_path.set(path)
            self.on_load_sales()

    def on_load_sales(self) -> None:
        path = self.sales_path.get().strip()
        if not path:
            messagebox.showwarning("파일 필요", "매출 파일을 먼저 고르세요.")
            return
        try:
            book = load_sales(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("읽기 실패", f"{exc}")
            return
        if not book.months:
            messagebox.showwarning("비어 있음", "매출 기록이 비어 있습니다.")
            return

        self.book = book
        self.sales_tree.delete(*self.sales_tree.get_children())
        for m in book.sorted_months():
            rate = f"{m.rate * 100:.0f}%" if m.rate is not None else "-"
            self.sales_tree.insert("", END, values=(
                m.ym, f"{m.revenue / 1e4:,.0f}만원",
                f"{m.target / 1e4:,.0f}만원" if m.target else "-",
                rate, f"{m.gap / 1e4:,.0f}만원" if m.gap else "-"),
                tags=("miss",) if m.gap else ())
        self.sales_tree.tag_configure("miss", background="#fdecea")
        self._refresh_gap()

    def _refresh_gap(self) -> None:
        record = self.book.latest_closed() if self.book else None
        if record is None:
            self.gap_label.configure(text="마감된 달이 없습니다.")
            self.gap_button.configure(state="disabled")
            return
        months_back = int(self.months_back.get() or 1)
        gap = self.book.recent_gap(months_back) if months_back > 1 else record.gap
        self.gap_target_preview = gap
        if gap:
            rate = f"{record.rate * 100:.0f}%" if record.rate is not None else "-"
            span = f"최근 {months_back}개월" if months_back > 1 else record.ym
            self.gap_label.configure(
                text=f"{span}  {gap / 1e4:,.0f}만원 부족  (최근 마감 {record.ym} 달성률 {rate})")
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
        self.tabs.select(0)           # 진행 상황이 보이도록 탐색 탭으로
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
            "days": self.days.get(),
            "distance": self.distance.get(),
            "overlap": self.overlap.get(),
            "sector": self.sector.get(),
            "include_demand_orgs": self.include_orgs.get(),
            "sales_path": self.sales_path.get(),
            "months_back": self.months_back.get(),
        }

    def on_remember(self) -> None:
        path = save_settings(self.current_options())
        messagebox.showinfo("저장됨",
                            f"입력값을 기억했습니다.\n{path}\n\n"
                            "인증키가 그대로 저장되니 공용 PC 에서는 주의하세요.")

    def on_run(self) -> None:
        if self.running:
            return
        options = self.current_options()
        mode = options["mode"]
        if mode == MODE_PUBLIC and not options["g2b_key"].strip():
            messagebox.showwarning("키 필요", "나라장터 인증키를 넣어주세요.\n"
                                              "키 없이 보시려면 '샘플 데이터'를 고르세요.")
            return
        if mode == MODE_INDUSTRY and not options["dart_key"].strip():
            messagebox.showwarning("키 필요", "업종 훑기에는 DART 인증키가 필요합니다.")
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
        mode = options["mode"]

        if mode == MODE_SAMPLE:
            self.say("샘플 데이터로 실행합니다 (실제 업체 아님).")
            result = run_screen(cfg, offline=True)
        elif mode == MODE_INDUSTRY:
            from prime_contractor.sources.dart import DartClient
            self.say("DART 상장사를 훑습니다. 첫 실행은 몇 분 걸립니다…")
            result = run_industry_screen(cfg, DartClient(cfg.dart_api_key))
        else:
            from prime_contractor.sources.g2b import G2BClient
            self.say(f"나라장터 최근 {cfg.lookback_days}일치를 조회합니다…")
            dart = None
            if cfg.dart_api_key:
                from prime_contractor.sources.dart import DartClient
                dart = DartClient(cfg.dart_api_key)
            result = run_screen(cfg, g2b_client=G2BClient(cfg.g2b_service_key), dart_client=dart)

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
        summary = " ".join(f"{g} {grades[g]}" for g in "ABCD" if g in grades)
        self.say("등급별: " + (summary or "없음") +
                 "  (A 우선 접촉 / B 접촉 가치 있음 / C 여력 될 때 / D 보류)")
        self.say("줄을 더블클릭하면 점수가 어떻게 나왔는지 볼 수 있습니다.")
        if self.gap_target:
            self._fill_plan(result)
        self.status.configure(text=f"후보 {len(result.passed)}곳  [{summary}]")
        self.save_button.configure(state="normal" if result.passed else "disabled")
        if not result.passed:
            messagebox.showinfo("결과 없음",
                                "조건에 맞는 후보가 없습니다.\n"
                                "기간을 늘리거나 납품 반경을 넓혀 보세요.")
        self._finish()

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
            text=plan.note + "  기대 월매출 = 추정 판넬 물량 ÷ 조회월수 × 등급별 수주확률"
                 " (A 35% / B 25% / C 15% / D 8%). 모두 어림값입니다.")
        self.gap_target = 0
        self.tabs.select(1)
        self.say(f"부족분 채우기 계획: {plan.note}")

    def _failed(self, exc: Exception) -> None:
        messagebox.showerror("실패", f"{exc}\n\n아래 진행 상황 창의 내용을 확인해 주세요.")
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
