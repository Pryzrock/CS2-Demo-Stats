"""
CS2 Demo Statistics — стильный тёмный GUI + парсер.

Зависимости:
    pip install awpy polars customtkinter

Сборка в .exe (после установки pyinstaller):
    pyinstaller --onefile --windowed --name "CS2DemoStats" cs2_demo_parser.py

Если демка в формате .dem.zst — распакуй сначала:
    zstd -d match.dem.zst -o match.dem
"""

import threading
import traceback
import tkinter as tk
from tkinter import filedialog
import ctypes
import sys

import customtkinter as ctk
import polars as pl

from awpy import Demo
from awpy.stats import adr, kast, rating


# ══════════════════════════════════════════════════════════════════════════════
#  ЦВЕТА / СТИЛЬ
# ══════════════════════════════════════════════════════════════════════════════

BG_APP       = "#0d0f14"
BG_SIDEBAR   = "#080a0e"
BG_CARD      = "#12151c"
BG_INPUT     = "#1a1d26"
BG_TABLE_ROW = "#0f1117"
BG_ROW_HOVER = "#161921"

ACCENT_BLUE   = "#2563eb"
ACCENT_BLUE_H = "#1d4ed8"
CT_COLOR      = "#3b82f6"
CT_BG         = "#1e2d4a"
T_COLOR       = "#f97316"
T_BG          = "#3d2010"

TEXT_PRIMARY   = "#e2e8f0"
TEXT_SECONDARY = "#94a3b8"
TEXT_MUTED     = "#475569"
TEXT_CT        = "#93c5fd"
TEXT_T         = "#fdba74"

BORDER         = "#1e2330"
BORDER_STRONG  = "#2d3348"

FONT_UI    = ("Segoe UI", 11)
FONT_LABEL = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO  = ("Consolas", 10)
FONT_TITLE = ("Segoe UI Semibold", 13)
FONT_BTN   = ("Segoe UI Semibold", 11)

COL_HEADERS = ["Player", "K / D / A", "K/D", "ADR", "KAST %", "Rating"]
COL_KEYS    = ["name", "kda", "kd", "adr", "kast", "rating"]
COL_WIDTHS  = [160, 90, 55, 60, 60, 65]
COL_ALIGN   = ["w", "center", "center", "center", "center", "center"]


# ══════════════════════════════════════════════════════════════════════════════
#  ПАРСИНГ  (выполняется в отдельном потоке)
# ══════════════════════════════════════════════════════════════════════════════

def parse_demo(demo_path: str, save_path: str, on_progress, on_done, on_error):
    try:
        on_progress(0.08, "Загружаем демку…")
        dem = Demo(demo_path)

        on_progress(0.20, "Парсим события…")
        dem.parse(player_props=["team_name", "team_clan_name"])

        on_progress(0.40, "Считаем статистику…")

        first_kill = dem.kills.sort("tick").row(0, named=True)
        ct_team = first_kill["ct_team_clan_name"]
        t_team  = first_kill["t_team_clan_name"]

        map_name   = dem.header.get("map_name", "?")
        num_rounds = dem.rounds.height

        adr_df    = adr(dem).filter(pl.col("side") == "all")
        kast_df   = kast(dem).filter(pl.col("side") == "all")
        rating_df = rating(dem).filter(pl.col("side") == "all")

        kills_df = (
            dem.kills.group_by("attacker_steamid").len()
            .rename({"attacker_steamid": "steamid", "len": "kills"})
        )
        deaths_df = (
            dem.kills.group_by("victim_steamid").len()
            .rename({"victim_steamid": "steamid", "len": "deaths"})
        )
        assists_df = (
            dem.kills.filter(pl.col("assister_steamid").is_not_null())
            .group_by("assister_steamid").len()
            .rename({"assister_steamid": "steamid", "len": "assists"})
        )

        team_df = (
            pl.concat([
                dem.kills.select([pl.col("attacker_steamid").alias("steamid"),
                                  pl.col("attacker_team_clan_name").alias("team")]),
                dem.kills.select([pl.col("victim_steamid").alias("steamid"),
                                  pl.col("victim_team_clan_name").alias("team")]),
                dem.kills.filter(pl.col("assister_steamid").is_not_null())
                .select([pl.col("assister_steamid").alias("steamid"),
                         pl.col("assister_team_clan_name").alias("team")]),
            ])
            .drop_nulls().filter(pl.col("team") != "")
            .group_by(["steamid", "team"]).len()
            .sort("len", descending=True)
            .group_by("steamid").first()
            .with_columns(
                pl.when(pl.col("team") == ct_team).then(pl.lit("CT"))
                .when(pl.col("team") == t_team).then(pl.lit("T"))
                .otherwise(pl.col("team")).alias("team")
            )
            .select(["steamid", "team"])
        )

        on_progress(0.72, "Собираем таблицу…")

        scoreboard = (
            rating_df.select(["steamid", "name", "rating"])
            .join(adr_df.select(["steamid", "adr"]),   on="steamid", how="left")
            .join(kast_df.select(["steamid", "kast"]), on="steamid", how="left")
            .join(team_df,    on="steamid", how="left")
            .join(kills_df,   on="steamid", how="left")
            .join(deaths_df,  on="steamid", how="left")
            .join(assists_df, on="steamid", how="left")
            .with_columns([
                pl.col("kills").fill_null(0),
                pl.col("deaths").fill_null(0),
                pl.col("assists").fill_null(0),
            ])
            .with_columns(
                (pl.col("kills") /
                 pl.when(pl.col("deaths") == 0).then(1).otherwise(pl.col("deaths"))
                 ).round(2).alias("kd")
            )
            .with_columns(
                (pl.col("kills").cast(pl.String) + " / " +
                 pl.col("deaths").cast(pl.String) + " / " +
                 pl.col("assists").cast(pl.String)).alias("kda")
            )
            .with_columns([
                pl.col("adr").round(1),
                pl.col("kast").round(1),
                pl.col("rating").round(2),
            ])
            .sort(["team", "rating"], descending=[False, True])
            .select(["team", "name", "kills", "deaths", "assists", "kd", "adr", "kast", "rating", "kda"])
        )

        on_progress(0.92, "Сохраняем CSV…")
        scoreboard.write_csv(save_path)

        on_done(map_name, num_rounds, scoreboard)

    except Exception:
        on_error(traceback.format_exc())


# ══════════════════════════════════════════════════════════════════════════════
#  ВИДЖЕТЫ-ПОМОЩНИКИ
# ══════════════════════════════════════════════════════════════════════════════

class Divider(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BORDER, height=1, **kw)


class SectionLabel(tk.Label):
    def __init__(self, parent, text, **kw):
        super().__init__(
            parent, text=text.upper(),
            fg=TEXT_MUTED, bg=BG_APP,
            font=("Segoe UI", 9), **kw
        )


class FileRow(tk.Frame):
    """Строка: метка + поле ввода + кнопка выбора файла."""

    def __init__(self, parent, label: str, btn_text: str, cmd, **kw):
        super().__init__(parent, bg=BG_APP, **kw)

        SectionLabel(self, label).pack(anchor="w", pady=(0, 4))

        row = tk.Frame(self, bg=BG_APP)
        row.pack(fill="x")

        self.var = tk.StringVar()

        entry = tk.Entry(
            row, textvariable=self.var,
            bg=BG_INPUT, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            relief="flat", font=FONT_UI,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT_BLUE,
        )
        entry.pack(side="left", fill="x", expand=True, ipady=6)

        btn = tk.Button(
            row, text=btn_text,
            bg=BG_INPUT, fg=TEXT_SECONDARY,
            activebackground=BG_CARD, activeforeground=TEXT_PRIMARY,
            relief="flat", font=FONT_UI, cursor="hand2",
            width=4, command=cmd,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        btn.pack(side="left", padx=(6, 0))


class ScoreTable(tk.Frame):
    """Кастомная таблица результатов."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG_CARD, **kw)
        self._build_header()
        self._rows: list[tk.Frame] = []

    def _build_header(self):
        hdr = tk.Frame(self, bg=BG_SIDEBAR)
        hdr.pack(fill="x")
        for i, (col, w, align) in enumerate(zip(COL_HEADERS, COL_WIDTHS, COL_ALIGN)):
            anchor = "w" if align == "w" else "center"
            tk.Label(
                hdr, text=col,
                width=w // 8,
                bg=BG_SIDEBAR, fg=TEXT_MUTED,
                font=("Segoe UI", 9), anchor=anchor,
            ).grid(row=0, column=i, padx=(10 if i == 0 else 4, 4), pady=6, sticky="ew")
        hdr.grid_columnconfigure(0, weight=1)

        Divider(self).pack(fill="x")

    def _team_section(self, label: str, color: str, bg: str):
        f = tk.Frame(self, bg=bg)
        f.pack(fill="x")
        tk.Label(
            f, text=f"  {label}",
            bg=bg, fg=color,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill="x", pady=4)
        Divider(self).pack(fill="x")

    def load(self, scoreboard: pl.DataFrame):
        for w in self._rows:
            w.destroy()
        self._rows.clear()

        current_team = None
        max_rating = scoreboard["rating"].max() or 1.0

        for row in scoreboard.iter_rows(named=True):
            team = row["team"]

            if team != current_team:
                current_team = team
                if team == "CT":
                    self._team_section("CT side", TEXT_CT, CT_BG)
                elif team == "T":
                    self._team_section("T side", TEXT_T, T_BG)
                else:
                    self._team_section(team, TEXT_SECONDARY, BG_SIDEBAR)

            f = tk.Frame(self, bg=BG_CARD, cursor="arrow")
            f.pack(fill="x")
            self._rows.append(f)

            is_ct = (team == "CT")
            av_bg   = CT_BG   if is_ct else T_BG
            av_fg   = TEXT_CT if is_ct else TEXT_T
            rating_color = TEXT_CT if is_ct else TEXT_T

            # Аватар (инициалы)
            initials = "".join(w[0].upper() for w in row["name"].split()[:2]) or "??"
            av = tk.Label(
                f, text=initials[:2],
                bg=av_bg, fg=av_fg,
                font=("Segoe UI", 8, "bold"),
                width=3, relief="flat",
            )
            av.grid(row=0, column=0, padx=(10, 6), pady=5, sticky="w")

            # Имя
            tk.Label(
                f, text=row["name"],
                bg=BG_CARD, fg=TEXT_PRIMARY,
                font=FONT_UI, anchor="w",
            ).grid(row=0, column=1, padx=(0, 8), pady=5, sticky="ew")

            # K/D/A
            tk.Label(
                f, text=row["kda"],
                bg=BG_CARD, fg=TEXT_SECONDARY,
                font=FONT_MONO, anchor="center",
            ).grid(row=0, column=2, padx=4, pady=5)

            # KD
            kd_val = row["kd"]
            kd_color = TEXT_CT if kd_val >= 1.0 else (TEXT_T if kd_val < 0.8 else TEXT_SECONDARY)
            tk.Label(
                f, text=f"{kd_val:.2f}",
                bg=BG_CARD, fg=kd_color,
                font=FONT_MONO, anchor="center", width=5,
            ).grid(row=0, column=3, padx=4, pady=5)

            # ADR
            tk.Label(
                f, text=f"{row['adr']:.1f}",
                bg=BG_CARD, fg=TEXT_SECONDARY,
                font=FONT_MONO, anchor="center", width=6,
            ).grid(row=0, column=4, padx=4, pady=5)

            # KAST
            tk.Label(
                f, text=f"{row['kast']:.1f}%",
                bg=BG_CARD, fg=TEXT_SECONDARY,
                font=FONT_MONO, anchor="center", width=6,
            ).grid(row=0, column=5, padx=4, pady=5)

            # Rating + мини-бар
            rat_frame = tk.Frame(f, bg=BG_CARD)
            rat_frame.grid(row=0, column=6, padx=(4, 10), pady=5, sticky="e")

            tk.Label(
                rat_frame, text=f"{row['rating']:.2f}",
                bg=BG_CARD, fg=rating_color,
                font=("Segoe UI Semibold", 10), anchor="e", width=5,
            ).pack(side="left")

            bar_bg = tk.Frame(rat_frame, bg=BORDER, width=48, height=3)
            bar_bg.pack(side="left", padx=(6, 0))
            bar_bg.pack_propagate(False)

            fill_w = max(2, int(48 * min(row["rating"] / (max_rating * 1.05), 1.0)))
            bar_color = CT_COLOR if is_ct else T_COLOR
            tk.Frame(bar_bg, bg=bar_color, width=fill_w, height=3).place(x=0, y=0)

            f.grid_columnconfigure(1, weight=1)
            Divider(f).grid(row=1, column=0, columnspan=7, sticky="ew")

    def clear(self):
        for w in self._rows:
            w.destroy()
        self._rows.clear()


# ══════════════════════════════════════════════════════════════════════════════
#  ГЛАВНОЕ ОКНО
# ══════════════════════════════════════════════════════════════════════════════

def _apply_dark_titlebar(window: tk.Tk):
    """Включает тёмный системный заголовок окна (Windows 10 1809+ / 11)."""
    if sys.platform != "win32":
        return
    try:
        window.update()  # окно должно быть отрисовано перед получением hwnd
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20  # для старых сборок Windows 10 — 19
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except Exception:
        pass

class DarkScrollbar(tk.Frame):
    """Кастомный тёмный скроллбар, синхронизированный с Canvas."""

    def __init__(self, parent, canvas, width=8, **kw):
        super().__init__(parent, bg=BG_CARD, width=width, **kw)
        self.canvas = canvas
        self.width = width
        self._thumb = tk.Frame(self, bg=BORDER_STRONG, cursor="arrow")
        self._thumb.place(x=0, y=0, width=width)

        self.bind("<Configure>", lambda e: self._redraw())
        self._thumb.bind("<Button-1>", self._start_drag)
        self._thumb.bind("<B1-Motion>", self._drag)
        self._drag_start_y = 0
        self._drag_start_top = 0

    def set(self, first, last):
        """Вызывается canvas через yscrollcommand=scrollbar.set"""
        first, last = float(first), float(last)
        h = self.winfo_height()
        if h <= 1:
            self.after(10, lambda: self.set(first, last))
            return
        top = first * h
        bottom = last * h
        self._thumb.place(x=1, y=top, width=self.width - 2, height=max(20, bottom - top))

    def _redraw(self):
        pass  # пересчёт идёт через set()

    def _start_drag(self, event):
        self._drag_start_y = event.y_root
        self._drag_start_top = self._thumb.winfo_y()

    def _drag(self, event):
        h = self.winfo_height()
        delta = event.y_root - self._drag_start_y
        new_top = max(0, min(h - self._thumb.winfo_height(),
                              self._drag_start_top + delta))
        frac = new_top / h
        self.canvas.yview_moveto(frac)

class DemoGUI(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("CS2 Demo Statistics")
        self.geometry("820x700")
        self.minsize(820, 700)
        self.configure(bg=BG_APP)

        try:
            self.iconbitmap("")
        except Exception:
            pass

        self._build_ui()
        self.after(10, lambda: _apply_dark_titlebar(self))

    # ── вёрстка ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=0)  # sidebar
        self.columnconfigure(1, weight=1)  # main
        self.rowconfigure(0, weight=1)

        # ── Sidebar ──
        sidebar = tk.Frame(self, bg=BG_SIDEBAR, width=52)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        for icon, active in [("⊞", True), ("◷", False), ("⚙", False)]:
            bg = "#1e2330" if active else BG_SIDEBAR
            fg = TEXT_PRIMARY if active else TEXT_MUTED
            lbl = tk.Label(sidebar, text=icon, bg=bg, fg=fg, font=("Segoe UI", 14),
                           width=3, pady=8, cursor="hand2")
            lbl.pack(padx=6, pady=2)

        # ── Main area ──
        main = tk.Frame(self, bg=BG_APP)
        main.grid(row=0, column=1, sticky="nsew", padx=0)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        # Titlebar strip
        title_bar = tk.Frame(main, bg=BG_SIDEBAR, height=42)
        title_bar.grid(row=0, column=0, sticky="ew")
        title_bar.grid_propagate(False)

        tk.Label(
            title_bar, text="CS2 Demo Statistics",
            bg=BG_SIDEBAR, fg=TEXT_PRIMARY,
            font=FONT_TITLE,
        ).place(relx=0.5, rely=0.5, anchor="center")

        Divider(main).grid(row=1, column=0, sticky="ew")

        # Inputs block
        inputs = tk.Frame(main, bg=BG_APP, pady=14)
        inputs.grid(row=2, column=0, sticky="ew", padx=20)
        inputs.columnconfigure(0, weight=1)
        inputs.columnconfigure(1, weight=1)

        self._demo_row = FileRow(inputs, "Demo file (.dem)", "📂", self._pick_demo)
        self._demo_row.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self._csv_row = FileRow(inputs, "Save as CSV", "💾", self._pick_csv)
        self._csv_row.grid(row=0, column=1, sticky="ew")

        # Run button + progress
        ctrl = tk.Frame(main, bg=BG_APP)
        ctrl.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 14))
        ctrl.columnconfigure(0, weight=1)

        self._run_btn = tk.Button(
            ctrl,
            text="▶  Analyse demo",
            bg=ACCENT_BLUE, fg="#ffffff",
            activebackground=ACCENT_BLUE_H, activeforeground="#ffffff",
            relief="flat", font=FONT_BTN,
            cursor="hand2", pady=9,
            command=self._run,
        )
        self._run_btn.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        # Progress bar (canvas для кастомной отрисовки)
        prog_frame = tk.Frame(ctrl, bg=BG_APP)
        prog_frame.grid(row=1, column=0, sticky="ew")
        prog_frame.columnconfigure(0, weight=1)

        self._prog_canvas = tk.Canvas(
            prog_frame, bg=BORDER, height=4,
            highlightthickness=0, relief="flat",
        )
        self._prog_canvas.grid(row=0, column=0, sticky="ew")
        self._prog_fill = None
        self._prog_value = 0.0

        status_row = tk.Frame(prog_frame, bg=BG_APP)
        status_row.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        status_row.columnconfigure(0, weight=1)

        self._status_lbl = tk.Label(
            status_row, text="Ready",
            bg=BG_APP, fg=TEXT_MUTED, font=FONT_SMALL, anchor="w",
        )
        self._status_lbl.grid(row=0, column=0, sticky="w")

        self._pct_lbl = tk.Label(
            status_row, text="",
            bg=BG_APP, fg=ACCENT_BLUE, font=FONT_SMALL, anchor="e",
        )
        self._pct_lbl.grid(row=0, column=1, sticky="e")

        Divider(main).grid(row=3, column=0, sticky="ew", pady=(0, 0))

        # ── Scoreboard area ──
        sb_wrap = tk.Frame(main, bg=BG_APP)
        sb_wrap.grid(row=4, column=0, sticky="nsew", padx=0, pady=0)
        sb_wrap.columnconfigure(0, weight=1)
        sb_wrap.rowconfigure(1, weight=1)

        # Шапка таблицы с инфо о матче
        self._sb_header = tk.Frame(sb_wrap, bg=BG_CARD)
        self._sb_header.grid(row=0, column=0, sticky="ew")

        self._map_lbl = tk.Label(
            self._sb_header, text="",
            bg=BG_CARD, fg=TEXT_MUTED, font=FONT_SMALL, anchor="w",
        )
        self._map_lbl.pack(side="left", padx=14, pady=6)

        self._rounds_lbl = tk.Label(
            self._sb_header, text="",
            bg=BG_CARD, fg=TEXT_MUTED, font=FONT_SMALL, anchor="e",
        )
        self._rounds_lbl.pack(side="right", padx=14, pady=6)

        Divider(sb_wrap).grid(row=0, column=0, sticky="sew")

        # Скролл-обёртка
        scroll_frame = tk.Frame(sb_wrap, bg=BG_CARD)
        scroll_frame.grid(row=1, column=0, sticky="nsew")
        scroll_frame.columnconfigure(0, weight=1)
        scroll_frame.rowconfigure(0, weight=1)

        canvas = tk.Canvas(scroll_frame, bg=BG_CARD, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")

        vsb = DarkScrollbar(scroll_frame, canvas, width=8)
        vsb.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=vsb.set)

        self._table_inner = tk.Frame(canvas, bg=BG_CARD)
        canvas_win = canvas.create_window((0, 0), window=self._table_inner, anchor="nw")

        def _on_resize(e):
            canvas.itemconfig(canvas_win, width=e.width)
        canvas.bind("<Configure>", _on_resize)
        self._table_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        self._score_table = ScoreTable(self._table_inner)
        self._score_table.pack(fill="both", expand=True)

        # Bind progress bar resize
        self._prog_canvas.bind("<Configure>", self._redraw_progress)

    # ── выбор файлов ────────────────────────────────────────────────────────

    def _pick_demo(self):
        path = filedialog.askopenfilename(
            title="Select CS2 demo",
            filetypes=[("CS2 Demo", "*.dem"), ("All files", "*.*")],
        )
        if path:
            self._demo_row.var.set(path)

    def _pick_csv(self):
        path = filedialog.asksaveasfilename(
            title="Save CSV",
            defaultextension=".csv",
            initialfile="scoreboard.csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self._csv_row.var.set(path)

    # ── прогресс-бар ────────────────────────────────────────────────────────

    def _redraw_progress(self, _event=None):
        c = self._prog_canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 2:
            return
        c.delete("all")
        fill_w = max(0, int(w * self._prog_value))
        if fill_w:
            c.create_rectangle(0, 0, fill_w, h, fill=ACCENT_BLUE, outline="")

    def _set_progress(self, value: float, status: str):
        self._prog_value = value
        self._redraw_progress()
        self._status_lbl.configure(text=status)
        self._pct_lbl.configure(text=f"{int(value * 100)}%" if value > 0 else "")

    # ── запуск ──────────────────────────────────────────────────────────────

    def _run(self):
        demo = self._demo_row.var.get().strip()
        save = self._csv_row.var.get().strip()

        if not demo:
            self._set_progress(0, "⚠  Select a demo file.")
            return
        if not save:
            self._set_progress(0, "⚠  Select output CSV path.")
            return

        self._run_btn.configure(state="disabled", bg="#1e3a8a")
        self._score_table.clear()
        self._map_lbl.configure(text="")
        self._rounds_lbl.configure(text="")
        self._set_progress(0.04, "Starting…")

        threading.Thread(
            target=parse_demo,
            args=(demo, save, self._on_progress, self._on_done, self._on_error),
            daemon=True,
        ).start()

    # ── колбэки из потока ───────────────────────────────────────────────────

    def _on_progress(self, value: float, text: str):
        self.after(0, lambda: self._set_progress(value, text))

    def _on_done(self, map_name: str, num_rounds: int, scoreboard: pl.DataFrame):
        def _update():
            self._set_progress(1.0, f"Done  ·  saved to {self._csv_row.var.get()}")
            self._pct_lbl.configure(text="✓", fg="#22c55e")
            self._run_btn.configure(state="normal", bg=ACCENT_BLUE)
            self._map_lbl.configure(
                text=f"Map: {map_name}",
                fg=TEXT_CT,
            )
            self._rounds_lbl.configure(
                text=f"{num_rounds} rounds",
                fg=TEXT_MUTED,
            )
            self._score_table.load(scoreboard)
        self.after(0, _update)

    def _on_error(self, tb: str):
        def _update():
            self._set_progress(0, "✖  Parse error — see below")
            self._run_btn.configure(state="normal", bg=ACCENT_BLUE)
            # Показываем traceback в таблице как текст
            for w in self._score_table._rows:
                w.destroy()
            self._score_table._rows.clear()
            err_frame = tk.Frame(self._score_table, bg=BG_CARD)
            err_frame.pack(fill="both", expand=True, padx=14, pady=14)
            self._score_table._rows.append(err_frame)
            tk.Label(
                err_frame, text=tb,
                bg=BG_CARD, fg="#f87171",
                font=FONT_MONO, anchor="nw", justify="left", wraplength=700,
            ).pack(fill="both", expand=True)
        self.after(0, _update)


# ══════════════════════════════════════════════════════════════════════════════
#  ТОЧКА ВХОДА
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = DemoGUI()
    app.mainloop()
