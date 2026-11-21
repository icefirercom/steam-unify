"""High-performance virtual-scrolled dark table with responsive columns and tag pills.

Only rows inside the current viewport (plus a small buffer) are created on the
Canvas, so scrolling and hovering remain smooth even with thousands of games.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from typing import Any, Callable

BG = "#141820"
PANEL = "#1b2430"
PANEL2 = "#233042"
HEADER = "#111720"
FG = "#d8e2ec"
MUTED = "#8796a5"
ACCENT = "#4da6ff"
ZEBRA = "#1e2937"
HOVER = "#273a4f"
SELECTED = "#284c6b"
BORDER = "#2a3a4c"

CATEGORY_COLORS: dict[str, str] = {
    "视觉小说": "#9b6dff",
    "沙盒生存": "#d08b4f",
    "恐怖": "#d94126",
    "音乐节奏": "#e86fb3",
    "模拟经营": "#76b852",
    "肉鸽": "#f0a23a",
    "卡牌": "#c58b3a",
    "解谜": "#35c5c9",
    "平台跳跃": "#ff8fa3",
    "竞速": "#ef5b5b",
    "体育": "#6fcf97",
    "策略": "#5b9bd5",
    "射击": "#e67e22",
    "角色扮演": "#e2c044",
    "动作": "#e05a47",
    "冒险": "#48b3a2",
    "休闲": "#8fbf7f",
    "多人": "#8f9fd8",
    "独立": "#8fa6b8",
    "其他": "#7d8a96",
}

SOURCE_BADGE_COLORS: dict[str, tuple[str, str]] = {
    "官方": ("#1d4430", "#7ee2a8"),
    "已中文": ("#1d4430", "#7ee2a8"),
    "词典": ("#1c4147", "#6ce3e8"),
    "维基": ("#1d3857", "#7cc4ff"),
    "商店": ("#1b3d52", "#74d0f5"),
    "AI": ("#3a2757", "#c99dff"),
    "机翻": ("#4a3919", "#f5c451"),
    "手动": ("#1a4552", "#66e0ff"),
    "原文": ("#2b3440", "#9aa9b8"),
    "待翻译": ("#422a22", "#f29768"),
    "已忽略": ("#2a2d36", "#8d93a1"),
}

# (key, title, min_width, flex_weight, anchor)
COLUMN_SPECS: list[tuple[str, str, int, float, str]] = [
    ("check", "", 40, 0.0, "center"),
    ("appid", "AppID", 78, 0.0, "w"),
    ("game", "游戏名 / 原名", 260, 1.35, "w"),
    ("source", "来源", 72, 0.0, "center"),
    ("categories", "分类", 230, 1.0, "w"),
    ("status", "状态", 68, 0.0, "center"),
]

# Kept for external callers that inspect COLUMNS.
COLUMNS = [(k, t, w, a) for k, t, w, _f, a in COLUMN_SPECS]

ROW_H = 46
HEADER_H = 36
PILL_H = 20
BUFFER_ROWS = 4


def _round_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    r: float,
    **kwargs: Any,
) -> int:
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=10, **kwargs)


@dataclass
class TableRow:
    appid: int
    zh: str
    orig: str
    categories: list[str]
    source: str
    status: str
    installed: bool
    locked: bool = False
    ignored: bool = False
    raw: Any = None


class TagTable(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_selection: Callable[[set[int]], None] | None = None,
        on_check: Callable[[set[int]], None] | None = None,
        on_double: Callable[[int, str], None] | None = None,
        on_context: Callable[[tk.Event, int], None] | None = None,
        on_sort: Callable[[str], None] | None = None,
    ):
        super().__init__(parent, bg=BG, highlightthickness=0, bd=0)
        self.on_selection = on_selection
        self.on_check = on_check
        self.on_double = on_double
        self.on_context = on_context
        self.on_sort = on_sort
        self.rows: list[TableRow] = []
        self._row_index: dict[int, int] = {}
        self.selected: set[int] = set()
        self.checked: set[int] = set()
        self.hover_appid: int | None = None
        self.sort_key = ""
        self.sort_desc = True
        self._last_selected: int | None = None
        self._rendered_range: tuple[int, int] = (-1, -1)
        self._viewport_job: str | None = None
        self._last_width = 0

        self.body_font = tkfont.Font(family="Microsoft YaHei UI", size=10)
        self.small_font = tkfont.Font(family="Microsoft YaHei UI", size=8)
        self.bold_font = tkfont.Font(family="Microsoft YaHei UI", size=10, weight="bold")
        self.header_font = tkfont.Font(family="Microsoft YaHei UI", size=9, weight="bold")
        self.mono_font = tkfont.Font(family="Consolas", size=9)

        self.total_width = sum(c[2] for c in COLUMN_SPECS)
        self.x_offsets: dict[str, tuple[int, int]] = {}
        self._compute_columns(self.total_width)

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.header = tk.Canvas(
            self, height=HEADER_H, bg=HEADER, highlightthickness=0, bd=0
        )
        self.header.grid(row=0, column=0, sticky="ew")

        wrap = tk.Frame(self, bg=BG)
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            wrap,
            bg=PANEL,
            highlightthickness=0,
            bd=0,
            yscrollincrement=ROW_H // 2,
            takefocus=1,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb = tk.Scrollbar(wrap, orient="vertical", command=self._yview)
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb = tk.Scrollbar(wrap, orient="horizontal", command=self._xview)
        self.hsb.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=self._body_yscroll)
        self.canvas.configure(xscrollcommand=self._body_xscroll)
        self.header.configure(xscrollcommand=self._header_xscroll)

        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Control-Button-1>", self._on_ctrl_click)
        self.canvas.bind("<Shift-Button-1>", self._on_shift_click)
        self.canvas.bind("<Double-1>", self._on_double)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-3>", self._on_right)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Return>", self._on_key_edit)
        self.canvas.bind("<F2>", self._on_key_edit)
        self.canvas.bind("<space>", self._on_key_space)
        self.canvas.bind("<Up>", lambda _e: self._move_selection(-1))
        self.canvas.bind("<Down>", lambda _e: self._move_selection(1))
        self.canvas.bind("<Control-a>", lambda _e: self.select_all_checked())
        self.canvas.bind("<Escape>", self._on_key_escape)
        self.header.bind("<Button-1>", self._on_header_click)
        self.header.bind("<MouseWheel>", self._on_wheel)

    # ------------------------------------------------------------ layout
    def _compute_columns(self, viewport_width: int) -> None:
        min_sum = sum(c[2] for c in COLUMN_SPECS)
        flex_sum = sum(c[3] for c in COLUMN_SPECS)
        extra = max(0, viewport_width - min_sum)
        x = 0
        offsets: dict[str, tuple[int, int]] = {}
        for key, _title, min_w, weight, _anchor in COLUMN_SPECS:
            w = min_w + (int(extra * (weight / flex_sum)) if (weight > 0 and flex_sum > 0) else 0)
            offsets[key] = (x, x + w)
            x += w
        self.x_offsets = offsets
        self.total_width = max(x, min_sum)

    def _on_configure(self, event: tk.Event) -> None:
        w = max(100, int(event.width))
        if abs(w - self._last_width) >= 2:
            self._last_width = w
            self._compute_columns(w)
            self._update_scrollregion()
            self.render(force=True)
        else:
            self._schedule_viewport_render()

    def _update_scrollregion(self) -> None:
        total_h = max(ROW_H, ROW_H * len(self.rows))
        self.canvas.configure(scrollregion=(0, 0, self.total_width, total_h))
        self.header.configure(scrollregion=(0, 0, self.total_width, HEADER_H))

    # ---------------------------------------------------------------- data
    def set_rows(self, rows: list[TableRow], *, preserve_scroll: bool = True) -> None:
        old_y = self.canvas.yview()[0] if preserve_scroll and self.rows else 0.0
        self.rows = rows
        self._row_index = {r.appid: i for i, r in enumerate(rows)}
        visible = set(self._row_index)
        self.checked &= visible
        self.selected &= visible
        if self.hover_appid not in visible:
            self.hover_appid = None
        self._update_scrollregion()
        if preserve_scroll and rows:
            self.canvas.yview_moveto(min(old_y, 1.0))
        else:
            self.canvas.yview_moveto(0.0)
        self.render(force=True)
        self._notify()

    def update_row(self, row: TableRow) -> None:
        """Update a single row in-place; redraws only if inside the viewport."""
        idx = self._row_index.get(row.appid)
        if idx is None:
            return
        self.rows[idx] = row
        lo, hi = self._rendered_range
        if lo <= idx < hi:
            self.canvas.delete(f"row_{row.appid}")
            self._draw_row(idx, row)

    def set_checked(self, ids: set[int]) -> None:
        self.checked = set(ids) & set(self._row_index)
        self.render(force=True)
        self._notify()

    def selected_ids(self) -> set[int]:
        return set(self.selected)

    def checked_ids(self) -> set[int]:
        return set(self.checked)

    def active_ids(self) -> set[int]:
        """Checked rows take precedence for batch actions; fallback to selected rows."""
        if self.checked:
            return set(self.checked)
        return set(self.selected)

    def select_all_checked(self) -> None:
        visible = set(self._row_index)
        if visible and visible <= self.checked:
            self.checked -= visible
        else:
            self.checked |= visible
        self.render(force=True)
        self._notify()

    def invert_checked(self) -> None:
        visible = set(self._row_index)
        self.checked = visible - self.checked
        self.render(force=True)
        self._notify()

    def clear_checked(self) -> None:
        self.checked.clear()
        self.render(force=True)
        self._notify()

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0)
        self.render(force=True)

    # -------------------------------------------------------------- drawing
    def _fit(self, text: str, width: float, font: tkfont.Font) -> str:
        text = str(text or "")
        if width <= 8:
            return ""
        if font.measure(text) <= width:
            return text
        ell = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(text[:mid] + ell) <= width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ell if lo > 0 else ""

    def _pill(
        self,
        x: float,
        y: float,
        text: str,
        bg_color: str,
        fg_color: str,
        max_right: float,
        tags: tuple[str, ...],
    ) -> float:
        fitted = self._fit(text, max(26, max_right - x - 14), self.small_font)
        if not fitted:
            return x
        width = self.small_font.measure(fitted) + 14
        if x + width > max_right:
            return x
        _round_rect(
            self.canvas, x, y, x + width, y + PILL_H, 7,
            fill=bg_color, outline="", tags=tags,
        )
        self.canvas.create_text(
            x + width / 2, y + PILL_H / 2,
            text=fitted, fill=fg_color, font=self.small_font,
            anchor="center", tags=tags,
        )
        return x + width + 5

    def _row_bg(self, idx: int, appid: int) -> str:
        if appid in self.selected:
            return SELECTED
        if appid == self.hover_appid:
            return HOVER
        return ZEBRA if idx % 2 else PANEL

    def _visible_slice(self) -> tuple[int, int]:
        n = len(self.rows)
        if n == 0:
            return (0, 0)
        h = max(self.canvas.winfo_height(), 480)
        y0 = max(0.0, self.canvas.canvasy(0))
        y1 = y0 + h
        first = max(0, int(y0 // ROW_H) - BUFFER_ROWS)
        last = min(n, int(y1 // ROW_H) + BUFFER_ROWS + 2)
        return (first, last)

    def _schedule_viewport_render(self) -> None:
        if self._viewport_job is not None:
            return
        self._viewport_job = self.after(16, self._render_viewport_if_needed)

    def _render_viewport_if_needed(self) -> None:
        self._viewport_job = None
        first, last = self._visible_slice()
        old_lo, old_hi = self._rendered_range
        # Re-render only when scrolled close to or beyond the buffered edges.
        if old_lo >= 0 and first >= old_lo and last <= old_hi:
            return
        self.render(force=True)

    def render(self, force: bool = True) -> None:
        if self._viewport_job is not None:
            try:
                self.after_cancel(self._viewport_job)
            except tk.TclError:
                pass
            self._viewport_job = None
        c = self.canvas
        c.delete("all")
        self._draw_header()
        if not self.rows:
            self._rendered_range = (0, 0)
            c.create_text(
                20, 28, text="没有符合条件的游戏", fill=MUTED,
                font=self.body_font, anchor="nw",
            )
            return
        first, last = self._visible_slice()
        self._rendered_range = (first, last)
        for idx in range(first, last):
            self._draw_row(idx, self.rows[idx])

    def _draw_row(self, idx: int, row: TableRow) -> None:
        c = self.canvas
        y = idx * ROW_H
        width = self.total_width
        rtag = f"row_{row.appid}"
        bg = self._row_bg(idx, row.appid)
        c.create_rectangle(
            0, y, width, y + ROW_H, fill=bg, outline="",
            tags=(rtag, f"bg_{row.appid}"),
        )
        if row.appid in self.selected:
            c.create_rectangle(
                0, y, 3, y + ROW_H, fill=ACCENT, outline="", tags=(rtag,)
            )

        # checkbox + installed dot
        cx, cy = 10, y + 14
        chk_fill = "#1f4868" if row.appid in self.checked else ""
        chk_out = ACCENT if row.appid in self.checked else "#597086"
        c.create_rectangle(
            cx, cy, cx + 16, cy + 16, outline=chk_out, fill=chk_fill, tags=(rtag,)
        )
        if row.appid in self.checked:
            c.create_line(
                cx + 3, cy + 8, cx + 7, cy + 12, cx + 13, cy + 4,
                fill="#ffffff", width=2, tags=(rtag,),
            )
        if row.installed:
            c.create_oval(
                cx + 21, cy + 5, cx + 27, cy + 11,
                fill="#7bd88f", outline="", tags=(rtag,),
            )

        # AppID
        x1, x2 = self.x_offsets["appid"]
        c.create_text(
            x1 + 6, y + ROW_H / 2,
            text=str(row.appid),
            fill=MUTED, font=self.mono_font, anchor="w", tags=(rtag,),
        )

        # Game name (Chinese on top + lock icon, original underneath)
        x1, x2 = self.x_offsets["game"]
        game_width = x2 - x1 - 14
        title_text = row.zh or row.orig or "（空）"
        if row.locked:
            title_text = "🔒 " + title_text
        title_color = FG if row.zh else ("#e5a00d" if not row.ignored else MUTED)
        if row.ignored:
            title_color = MUTED
        c.create_text(
            x1 + 8, y + (15 if (row.orig and row.zh) else ROW_H / 2),
            text=self._fit(title_text, game_width, self.body_font),
            fill=title_color, font=self.body_font, anchor="w", tags=(rtag,),
        )
        if row.orig and row.zh:
            c.create_text(
                x1 + 8, y + 33,
                text=self._fit(row.orig, game_width, self.small_font),
                fill=MUTED, font=self.small_font, anchor="w", tags=(rtag,),
            )

        # Source badge pill
        x1, x2 = self.x_offsets["source"]
        src_label = row.source or "待翻译"
        s_bg, s_fg = SOURCE_BADGE_COLORS.get(src_label, ("#2a3441", MUTED))
        sw = min(x2 - x1 - 10, max(44, self.small_font.measure(src_label) + 14))
        sx = (x1 + x2 - sw) / 2
        sy = y + (ROW_H - PILL_H) / 2
        _round_rect(c, sx, sy, sx + sw, sy + PILL_H, 6, fill=s_bg, outline="", tags=(rtag,))
        c.create_text(
            (x1 + x2) / 2, y + ROW_H / 2,
            text=src_label, fill=s_fg, font=self.small_font, anchor="center", tags=(rtag,),
        )

        # Category pills
        x1, x2 = self.x_offsets["categories"]
        px = x1 + 8
        max_right = x2 - 8
        py = y + (ROW_H - PILL_H) / 2
        cats = row.categories[:3]
        if not cats:
            self._pill(px, py, "未分类", "#2c3846", MUTED, max_right, (rtag,))
        else:
            for cat in cats:
                color = CATEGORY_COLORS.get(cat, "#7d8a96")
                nx = self._pill(px, py, cat, color, "#10161d", max_right, (rtag,))
                if nx == px:
                    break
                px = nx

        # Status
        x1, x2 = self.x_offsets["status"]
        c.create_text(
            (x1 + x2) / 2, y + ROW_H / 2,
            text=self._fit(row.status, x2 - x1 - 8, self.small_font),
            fill=self._status_color(row.status), font=self.small_font,
            anchor="center", tags=(rtag,),
        )
        c.create_line(0, y + ROW_H - 1, width, y + ROW_H - 1, fill="#151d26", tags=(rtag,))

    def _draw_header(self) -> None:
        h = self.header
        h.delete("all")
        h.create_rectangle(0, 0, self.total_width, HEADER_H, fill=HEADER, outline="")
        all_checked = bool(self.rows) and set(self._row_index) <= self.checked
        h.create_rectangle(
            10, 10, 26, 26,
            outline=ACCENT if all_checked else "#597086",
            fill="#1f4868" if all_checked else "",
        )
        if all_checked:
            h.create_line(13, 18, 17, 22, 23, 14, fill="#ffffff", width=2)
        for key, title, _min_w, _flex, anchor in COLUMN_SPECS:
            if key == "check":
                continue
            x1, x2 = self.x_offsets[key]
            arrow = ""
            if key == self.sort_key:
                arrow = " ▼" if self.sort_desc else " ▲"
            tx = (x1 + x2) / 2 if anchor == "center" else (x1 + 8)
            h.create_text(
                tx, HEADER_H / 2, text=title + arrow,
                fill="#c7d5e0", font=self.header_font, anchor=anchor,
            )
        h.create_line(0, HEADER_H - 1, self.total_width, HEADER_H - 1, fill=BORDER)

    @staticmethod
    def _status_color(status: str) -> str:
        if status in ("已应用", "已复原"):
            return "#7bd88f"
        if status == "可应用":
            return ACCENT
        if status == "待翻译":
            return "#f5b041"
        return MUTED

    # -------------------------------------------------------------- events
    def _content_pos(self, event: tk.Event) -> tuple[float, float]:
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def _row_at(self, y: float) -> int | None:
        if y < 0:
            return None
        idx = int(y // ROW_H)
        return idx if 0 <= idx < len(self.rows) else None

    def _col_at(self, x: float) -> str:
        for key, (x1, x2) in self.x_offsets.items():
            if x1 <= x < x2:
                return key
        return ""

    def _notify(self) -> None:
        if self.on_selection:
            self.on_selection(set(self.selected))
        if self.on_check:
            self.on_check(set(self.checked))

    def _toggle_checked(self, idx: int) -> None:
        row = self.rows[idx]
        if row.appid in self.checked:
            self.checked.remove(row.appid)
        else:
            self.checked.add(row.appid)
        self._notify()
        self.update_row(row)
        self._draw_header()

    def _on_click(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        x, y = self._content_pos(event)
        idx = self._row_at(y)
        if idx is None:
            self.selected.clear()
            self._notify()
            self.render(force=True)
            return
        row = self.rows[idx]
        if self._col_at(x) == "check":
            self._toggle_checked(idx)
            return
        old_sel = set(self.selected)
        self.selected = {row.appid}
        self._last_selected = row.appid
        self._notify()
        for aid in old_sel | self.selected:
            ridx = self._row_index.get(aid)
            if ridx is not None:
                self.update_row(self.rows[ridx])

    def _on_ctrl_click(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        x, y = self._content_pos(event)
        idx = self._row_at(y)
        if idx is None:
            return
        row = self.rows[idx]
        if self._col_at(x) == "check":
            self._toggle_checked(idx)
            return
        if row.appid in self.selected:
            self.selected.remove(row.appid)
        else:
            self.selected.add(row.appid)
        self._last_selected = row.appid
        self._notify()
        self.update_row(row)

    def _on_shift_click(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        if self._last_selected is None:
            self._on_click(event)
            return
        _x, y = self._content_pos(event)
        idx = self._row_at(y)
        if idx is None:
            return
        a = self._row_index.get(self._last_selected, idx)
        lo, hi = sorted((a, idx))
        self.selected.update(self.rows[i].appid for i in range(lo, hi + 1))
        self._notify()
        self.render(force=True)

    def _on_double(self, event: tk.Event) -> None:
        x, y = self._content_pos(event)
        idx = self._row_at(y)
        if idx is None:
            return
        row = self.rows[idx]
        col = self._col_at(x)
        if col == "check":
            self._toggle_checked(idx)
            return
        if self.on_double:
            self.on_double(row.appid, "categories" if col == "categories" else "game")

    def _on_motion(self, event: tk.Event) -> None:
        _x, y = self._content_pos(event)
        idx = self._row_at(y)
        new_id = self.rows[idx].appid if idx is not None else None
        if new_id == self.hover_appid:
            return
        old_id = self.hover_appid
        self.hover_appid = new_id
        if old_id is not None and old_id in self._row_index:
            self.canvas.itemconfigure(
                f"bg_{old_id}", fill=self._row_bg(self._row_index[old_id], old_id)
            )
        if new_id is not None and new_id in self._row_index:
            self.canvas.itemconfigure(
                f"bg_{new_id}", fill=self._row_bg(self._row_index[new_id], new_id)
            )

    def _on_leave(self, _event: tk.Event) -> None:
        if self.hover_appid is not None:
            old_id = self.hover_appid
            self.hover_appid = None
            if old_id in self._row_index:
                self.canvas.itemconfigure(
                    f"bg_{old_id}", fill=self._row_bg(self._row_index[old_id], old_id)
                )

    def _on_right(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        _x, y = self._content_pos(event)
        idx = self._row_at(y)
        if idx is None or not self.on_context:
            return
        row = self.rows[idx]
        if row.appid not in self.selected:
            old_sel = set(self.selected)
            self.selected = {row.appid}
            self._last_selected = row.appid
            self._notify()
            for aid in old_sel | self.selected:
                ridx = self._row_index.get(aid)
                if ridx is not None:
                    self.update_row(self.rows[ridx])
        self.on_context(event, row.appid)

    def _on_header_click(self, event: tk.Event) -> None:
        x = self.header.canvasx(event.x)
        if x < self.x_offsets["check"][1]:
            self.select_all_checked()
            return
        col = self._col_at(x)
        if col and col != "check" and self.on_sort:
            self.on_sort(col)

    def _on_wheel(self, event: tk.Event) -> None:
        step = int(-event.delta / 120) * 2
        if step == 0:
            step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step, "units")
        self._schedule_viewport_render()

    def _on_key_edit(self, _event: tk.Event) -> None:
        if not self.selected or not self.on_double:
            return
        target = self._last_selected if self._last_selected in self.selected else next(iter(self.selected))
        self.on_double(target, "game")

    def _on_key_space(self, _event: tk.Event) -> None:
        if not self.selected:
            return
        all_in = self.selected <= self.checked
        if all_in:
            self.checked -= self.selected
        else:
            self.checked |= self.selected
        self._notify()
        for aid in self.selected:
            idx = self._row_index.get(aid)
            if idx is not None:
                self.update_row(self.rows[idx])
        self._draw_header()

    def _on_key_escape(self, _event: tk.Event) -> None:
        if self.selected or self.checked:
            self.selected.clear()
            self.checked.clear()
            self._notify()
            self.render(force=True)

    def _move_selection(self, delta: int) -> None:
        if not self.rows:
            return
        if self._last_selected is not None and self._last_selected in self._row_index:
            cur = self._row_index[self._last_selected]
            nxt = max(0, min(len(self.rows) - 1, cur + delta))
        else:
            nxt = 0
        row = self.rows[nxt]
        old_sel = set(self.selected)
        self.selected = {row.appid}
        self._last_selected = row.appid
        self._notify()
        # Scroll row into view if outside viewport.
        y0 = self.canvas.canvasy(0)
        h = max(self.canvas.winfo_height(), ROW_H * 4)
        ry = nxt * ROW_H
        total_h = max(1, len(self.rows) * ROW_H)
        if ry < y0:
            self.canvas.yview_moveto(ry / total_h)
            self.render(force=True)
        elif ry + ROW_H > y0 + h:
            self.canvas.yview_moveto(max(0.0, (ry + ROW_H - h) / total_h))
            self.render(force=True)
        else:
            for aid in old_sel | self.selected:
                idx = self._row_index.get(aid)
                if idx is not None:
                    self.update_row(self.rows[idx])

    def _yview(self, *args: Any) -> None:
        self.canvas.yview(*args)
        self._schedule_viewport_render()

    def _xview(self, *args: Any) -> None:
        self.canvas.xview(*args)
        self.header.xview(*args)

    def _body_yscroll(self, first: str, last: str) -> None:
        self.vsb.set(first, last)
        self._schedule_viewport_render()

    def _body_xscroll(self, first: str, last: str) -> None:
        self.hsb.set(first, last)
        self.header.xview_moveto(first)

    def _header_xscroll(self, first: str, last: str) -> None:
        self.hsb.set(first, last)
