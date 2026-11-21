"""Main desktop GUI for Steam Unify (virtual-scrolled, DPI-aware, single-row workflow)."""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import (
    category_model,
    category_state,
    descriptions,
    scoring,
    service,
    steamenv,
    translate,
    ui_widgets,
)
from .gui_dialogs import (
    ACCENT,
    APP_TITLE,
    BG,
    CATEGORY_COLORS,
    FG,
    MUTED,
    PANEL,
    PANEL2,
    SOURCE_LABEL,
    STATUS_FILTERS,
    ApplyDialog,
    BatchCategoryDialog,
    CategoryPickerDialog,
    NameEditDialog,
    SettingsDialog,
)
from .state import State


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class SteamZhApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w = min(int(sw * 0.88), 1440)
        h = min(int(sh * 0.86), 900)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 16)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(1024, 640)
        self.root.configure(bg=BG)
        self.root.option_add("*TCombobox*Listbox.background", PANEL2)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#284c6b")
        self.root.option_add("*TCombobox*Listbox.selectForeground", FG)

        self.settings = State.load_settings()
        self.ctx: service.Context | None = None
        self.busy = False
        self.cancel_flag = False
        self.games_by_id: dict[int, object] = {}
        self.bus: queue.Queue = queue.Queue()
        self.sort_key = ""
        self.sort_desc = True
        self.log_visible = False
        self.last_batch_snapshot: dict[int, list[str]] | None = None
        self._sidebar_guard = False
        self._search_after: str | None = None
        self._steam_running_val = False
        self._steam_running_ts = 0.0
        self.buttons: dict[str, ttk.Button] = {}

        self._build_style()
        self._build_ui()
        self._bind_shortcuts()
        self._update_undo_button()
        self.root.after(100, self._poll_bus)
        self.reload()

    # ------------------------------------------------------------------ style
    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            ".", background=BG, foreground=FG, fieldbackground=PANEL2,
            font=("Microsoft YaHei UI", 10),
        )
        style.configure("TButton", background=PANEL2, foreground=FG, padding=(10, 5), borderwidth=0)
        style.map("TButton", background=[("active", "#2e445c"), ("disabled", "#1b2430")],
                  foreground=[("disabled", "#5c6b78")])
        style.configure(
            "Accent.TButton", background="#246ea3", foreground="#ffffff",
            padding=(12, 5), borderwidth=0,
        )
        style.map("Accent.TButton", background=[("active", "#2f85c4"), ("disabled", "#1f394d")],
                  foreground=[("disabled", "#7a95aa")])
        style.configure(
            "Danger.TButton", background="#8b2e2e", foreground="#ffffff",
            padding=(10, 5), borderwidth=0,
        )
        style.map("Danger.TButton", background=[("active", "#a93b3b")])
        style.configure(
            "Small.TButton", background=PANEL2, foreground=FG,
            padding=(7, 3), font=("Microsoft YaHei UI", 8), borderwidth=0,
        )
        style.map("Small.TButton", background=[("active", "#2e445c")])
        style.configure(
            "Sidebar.Treeview", background=PANEL, fieldbackground=PANEL,
            foreground=FG, rowheight=27, borderwidth=0,
            font=("Microsoft YaHei UI", 9),
        )
        style.map("Sidebar.Treeview", background=[("selected", "#284c6b")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(12, 6))
        style.map(
            "TNotebook.Tab",
            background=[("selected", PANEL2)],
            foreground=[("selected", FG)],
        )
        style.configure(
            "TCombobox", fieldbackground=PANEL2, background=PANEL2,
            foreground=FG, arrowcolor=FG, selectbackground=PANEL2, selectforeground=FG,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", PANEL2)],
            foreground=[("readonly", FG)],
            selectbackground=[("readonly", PANEL2)],
            selectforeground=[("readonly", FG)],
        )
        style.configure("TProgressbar", background=ACCENT, troughcolor=PANEL2, borderwidth=0)

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-f>", self._focus_search)
        self.root.bind("<Control-F>", self._focus_search)
        self.root.bind("<F5>", lambda _e: self.reload())

    def _focus_search(self, _event=None) -> str:
        if hasattr(self, "search_entry"):
            self.search_entry.focus_set()
            self.search_entry.select_range(0, "end")
        return "break"

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        self.status_var = tk.StringVar(value="正在加载 ...")
        self.count_var = tk.StringVar(value="")
        self.sel_var = tk.StringVar(value="")
        self.header_status = tk.StringVar(value="正在扫描 Steam 库 …")

        # Compact Header Bar
        header = tk.Frame(self.root, bg=PANEL, height=52)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        tk.Label(
            header, text="Steam 统译", bg=PANEL, fg="#ffffff",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(side="left", padx=(16, 8), pady=8)
        tk.Label(
            header, text="多源并发汉化 · 智能过滤 · 批量分类", bg=PANEL, fg=MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", pady=10)

        self.log_toggle_btn = ttk.Button(
            header, text="日志", style="Small.TButton", command=self._toggle_log
        )
        self.log_toggle_btn.pack(side="right", padx=(4, 14))
        self.cancel_btn = ttk.Button(
            header, text="取消", style="Small.TButton",
            command=self.on_cancel, state="disabled",
        )
        self.cancel_btn.pack(side="right", padx=4)
        self.progress = ttk.Progressbar(header, mode="determinate", maximum=100, length=140)
        self.progress.pack(side="right", padx=6)
        tk.Label(
            header, textvariable=self.header_status, bg=PANEL, fg=ACCENT,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right", padx=(6, 6))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        # Sidebar
        sidebar = tk.Frame(body, bg=PANEL, width=196)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(
            sidebar, text="库导航", bg=PANEL, fg=MUTED,
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        ).pack(fill="x", padx=14, pady=(10, 4))
        sb_wrap = tk.Frame(sidebar, bg=PANEL)
        sb_wrap.pack(fill="both", expand=True, padx=(4, 0), pady=(0, 8))
        self.sidebar = ttk.Treeview(
            sb_wrap, show="tree", selectmode="browse", style="Sidebar.Treeview"
        )
        self.sidebar.pack(side="left", fill="both", expand=True)
        sb_vsb = ttk.Scrollbar(sb_wrap, orient="vertical", command=self.sidebar.yview)
        self.sidebar.configure(yscrollcommand=sb_vsb.set)
        sb_vsb.pack(side="right", fill="y")
        self.sidebar.bind("<<TreeviewSelect>>", self._on_sidebar)

        # Main workspace
        main = tk.Frame(body, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        main.grid_rowconfigure(3, weight=1)
        main.grid_columnconfigure(0, weight=1)

        toolbar = tk.Frame(main, bg=BG)
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        self._build_toolbar(toolbar)

        filters = tk.Frame(main, bg=BG)
        filters.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        self._build_filters(filters)

        batch_bar = tk.Frame(main, bg=PANEL, padx=10, pady=4)
        batch_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
        self._build_batch_bar(batch_bar)

        table_frame = tk.Frame(main, bg=BG)
        table_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 28))
        self.table = ui_widgets.TagTable(
            table_frame,
            on_selection=lambda _ids: self._update_selection_label(),
            on_check=lambda _ids: self._update_selection_label(),
            on_double=self.on_table_double,
            on_context=self.on_table_context,
            on_sort=self.on_sort,
        )
        self.table.pack(fill="both", expand=True)

        # Overlay Log Drawer
        self.log_frame = tk.Frame(
            main, bg="#0d1218", highlightthickness=1, highlightbackground=ui_widgets.BORDER
        )
        log_head = tk.Frame(self.log_frame, bg=PANEL)
        log_head.pack(fill="x")
        self.log_last_var = tk.StringVar(value="日志")
        tk.Label(
            log_head, textvariable=self.log_last_var, bg=PANEL, fg=MUTED,
            anchor="w", font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=8, pady=3, fill="x", expand=True)
        ttk.Button(
            log_head, text="收起", style="Small.TButton", command=self._toggle_log
        ).pack(side="right", padx=4, pady=2)
        self.log_body = tk.Frame(self.log_frame, bg="#0d1218")
        self.log_body.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            self.log_body, height=7, bg="#0d1218", fg=MUTED, insertbackground=FG,
            relief="flat", wrap="word", font=("Microsoft YaHei UI", 9),
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        log_sb = ttk.Scrollbar(self.log_body, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        log_sb.pack(side="right", fill="y")

        # Bottom Status Bar
        status_bar = tk.Frame(main, bg=PANEL)
        status_bar.place(relx=0.0, rely=1.0, anchor="sw", relwidth=1.0, height=26)
        status_bar.lift()
        tk.Label(
            status_bar, textvariable=self.status_var, bg=PANEL, fg=MUTED,
            anchor="w", font=("Microsoft YaHei UI", 8),
        ).pack(side="left", padx=10, pady=3)
        tk.Label(
            status_bar, textvariable=self.count_var, bg=PANEL, fg=MUTED,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right", padx=10)

    def _build_toolbar(self, parent: tk.Frame) -> None:
        # Clean single-row primary workflow bar.
        specs = [
            ("scan", "扫描库", self.reload, "TButton"),
            ("translate", "自动翻译", self.on_translate, "Accent.TButton"),
            ("categorize", "自动分类", self.on_categorize, "TButton"),
            ("batch", "批量分类", self.on_batch, "TButton"),
            ("apply_all", "应用到 Steam", lambda: self.on_apply(False), "Accent.TButton"),
        ]
        for key, text, cmd, style_name in specs:
            b = ttk.Button(parent, text=text, command=cmd, style=style_name)
            b.pack(side="left", padx=(0, 6))
            self.buttons[key] = b

        # Compact "More" dropdown menu for secondary & dangerous tools.
        self.more_btn = ttk.Button(parent, text="更多操作 ▾", command=self._show_more_menu)
        self.more_btn.pack(side="left", padx=(0, 6))
        self.buttons["more"] = self.more_btn

        settings_btn = ttk.Button(parent, text="设置", command=self.on_settings)
        settings_btn.pack(side="right")
        self.buttons["settings"] = settings_btn

    def _show_more_menu(self) -> None:
        menu = tk.Menu(
            self.root, tearoff=0, bg=PANEL2, fg=FG,
            activebackground="#284c6b", activeforeground="#ffffff",
            font=("Microsoft YaHei UI", 9),
        )
        menu.add_command(label="仅应用当前勾选/选中…", command=lambda: self.on_apply(True))
        menu.add_command(
            label="仅应用中文名（不改 Steam 收藏集）…",
            command=lambda: self.on_apply(False, force_collections=False),
        )
        menu.add_command(label="补全 Steam 商店简介缓存", command=self.on_fetch_descriptions)
        menu.add_separator()
        menu.add_command(label="导入自定义词典 (JSON / CSV)…", command=self.on_import_dict)
        menu.add_command(label="导出自定义词典 (JSON / CSV)…", command=self.on_export_dict)
        menu.add_command(label="导出完整清单报表 (CSV)…", command=self.on_export)
        menu.add_separator()
        menu.add_command(
            label="撤销上次批量分类",
            command=self.on_undo_batch,
            state="normal" if (self.last_batch_snapshot and not self.busy) else "disabled",
        )
        menu.add_command(label="一键修复（被 Steam 覆盖时）", command=self.on_autofix)
        menu.add_command(label="从备份还原…", command=self.on_restore)
        menu.add_separator()
        menu.add_command(
            label="⚠ 一键复原（后悔药：恢复原英文名并删除收藏集）",
            foreground="#ff7b6b",
            command=self.on_undo_all,
        )
        bx = self.more_btn.winfo_rootx()
        by = self.more_btn.winfo_rooty() + self.more_btn.winfo_height() + 2
        try:
            menu.tk_popup(bx, by)
        finally:
            menu.grab_release()

    def _build_filters(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="搜索", bg=BG, fg=MUTED).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._schedule_search_refresh())
        self.search_entry = tk.Entry(
            parent, textvariable=self.search_var, width=26, bg=PANEL2, fg=FG,
            insertbackground=FG, relief="flat",
        )
        self.search_entry.pack(side="left", padx=(6, 4), ipady=4)
        ttk.Button(
            parent, text="×", style="Small.TButton", width=2,
            command=lambda: self.search_var.set(""),
        ).pack(side="left", padx=(0, 10))

        tk.Label(parent, text="状态", bg=BG, fg=MUTED).pack(side="left")
        self.filter_var = tk.StringVar(value="全部")
        status_combo = ttk.Combobox(
            parent, textvariable=self.filter_var, state="readonly", width=10,
            values=STATUS_FILTERS,
        )
        status_combo.pack(side="left", padx=(4, 10))
        status_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_table(preserve_scroll=False))

        tk.Label(parent, text="分类", bg=BG, fg=MUTED).pack(side="left")
        self.category_var = tk.StringVar(value="全部分类")
        cat_values = ["全部分类"] + category_model.category_names()
        cat_combo = ttk.Combobox(
            parent, textvariable=self.category_var, state="readonly", width=10,
            values=cat_values,
        )
        cat_combo.pack(side="left", padx=(4, 10))
        cat_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_table(preserve_scroll=False))

        self.installed_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            parent, text="仅已安装", variable=self.installed_var,
            bg=BG, fg=MUTED, selectcolor=PANEL2, activebackground=BG,
            activeforeground=FG, command=lambda: self.refresh_table(preserve_scroll=False),
        ).pack(side="left", padx=(0, 6))

        ttk.Button(
            parent, text="重置筛选", style="Small.TButton", command=self._reset_filters
        ).pack(side="left", padx=(2, 0))

    def _build_batch_bar(self, parent: tk.Frame) -> None:
        tk.Label(
            parent, textvariable=self.sel_var, bg=PANEL, fg=ACCENT,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(side="left")
        ttk.Button(
            parent, text="全选", style="Small.TButton",
            command=lambda: self.table.select_all_checked(),
        ).pack(side="left", padx=(8, 3))
        ttk.Button(
            parent, text="反选", style="Small.TButton",
            command=lambda: self.table.invert_checked(),
        ).pack(side="left", padx=3)
        ttk.Button(
            parent, text="清空勾选", style="Small.TButton",
            command=lambda: self.table.clear_checked(),
        ).pack(side="left", padx=3)

        self.batch_retrans_btn = ttk.Button(
            parent, text="重新翻译选中", style="Small.TButton",
            command=self.on_retranslate_selected,
        )
        self.batch_retrans_btn.pack(side="right", padx=(4, 0))
        self.buttons["retrans_sel"] = self.batch_retrans_btn

        self.batch_ignore_btn = ttk.Button(
            parent, text="忽略选中", style="Small.TButton",
            command=self.on_toggle_ignore_selected,
        )
        self.batch_ignore_btn.pack(side="right", padx=4)
        self.buttons["ignore_sel"] = self.batch_ignore_btn

        tk.Label(
            parent, text="提示：双击改名/改分类 · 回车编辑 · 空格勾选 · 右键更多",
            bg=PANEL, fg=MUTED, font=("Microsoft YaHei UI", 8),
        ).pack(side="right", padx=(0, 12))

    def _schedule_search_refresh(self) -> None:
        if self._search_after is not None:
            try:
                self.root.after_cancel(self._search_after)
            except tk.TclError:
                pass
        self._search_after = self.root.after(120, lambda: self.refresh_table(preserve_scroll=False))

    def _reset_filters(self) -> None:
        self.search_var.set("")
        self.filter_var.set("全部")
        self.category_var.set("全部分类")
        self.installed_var.set(False)
        self.refresh_table(preserve_scroll=False)

    # ------------------------------------------------------------- logging
    def log(self, message: str) -> None:
        self.bus.put(("log", str(message)))

    def _append_log(self, message: str) -> None:
        if not hasattr(self, "log_last_var"):
            return
        stamp = time.strftime("%H:%M:%S")
        self.log_last_var.set(f"[{stamp}] {message}")
        if hasattr(self, "log_text"):
            self.log_text.insert("end", f"[{stamp}] {message}\n")
            self.log_text.see("end")
        if not self.log_visible and any(k in message for k in ("失败", "错误", "异常")):
            self._toggle_log(force=True)

    def _toggle_log(self, force: bool = False) -> None:
        if self.log_visible and not force:
            self.log_frame.place_forget()
            self.log_visible = False
            self.log_toggle_btn.configure(text="日志")
            return
        self.log_frame.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0, height=188)
        self.log_frame.lift()
        self.log_visible = True
        self.log_toggle_btn.configure(text="收起日志")

    # --------------------------------------------------------------- tasks
    def _poll_bus(self) -> None:
        streamed_any = False
        try:
            for _ in range(80):
                kind, payload = self.bus.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    cur, total, msg = payload
                    if total:
                        self.progress.stop()
                        self.progress.configure(mode="determinate", maximum=total, value=cur)
                    else:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(12)
                    if msg:
                        self.header_status.set(msg)
                        self.status_var.set(msg)
                elif kind == "item_updated":
                    appid = int(payload)
                    self._update_single_row(appid)
                    streamed_any = True
                elif kind == "task_done":
                    name, result = payload
                    self._task_finished(name, result, None)
                elif kind == "task_error":
                    name, exc, tb = payload
                    self._task_finished(name, None, (exc, tb))
                elif kind == "status":
                    self.header_status.set(payload)
        except queue.Empty:
            pass
        if streamed_any:
            self.update_status(check_steam=False)
        self.root.after(100, self._poll_bus)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in self.buttons.values():
            b.configure(state=state)
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        self._update_undo_button()

    def _update_undo_button(self) -> None:
        if "undo_batch" in self.buttons:
            enabled = bool(self.last_batch_snapshot) and not self.busy
            self.buttons["undo_batch"].configure(state="normal" if enabled else "disabled")

    def run_task(self, name: str, func) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)

        def worker():
            try:
                result = func()
            except Exception as exc:
                self.bus.put(("task_error", (name, exc, traceback.format_exc())))
            else:
                self.bus.put(("task_done", (name, result)))

        threading.Thread(target=worker, daemon=True).start()

    def _task_finished(self, name: str, result, error) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._set_busy(False)
        if error:
            exc, tb = error
            self._append_log(f"操作失败：{exc}")
            self._append_log(tb.strip().splitlines()[-1] if tb else "")
            messagebox.showerror("操作失败", str(exc), parent=self.root)
            self.update_status()
            return
        if name == "load":
            self.ctx = result
            self.games_by_id = {g.appid: g for g in self.ctx.games}
            try:
                service.auto_translate(
                    self.ctx, use_network=False, settings=self.settings
                )
            except Exception as exc:
                self._append_log(f"内置名称填充失败：{exc}")
            self.refresh_table(preserve_scroll=False)
            self.refresh_sidebar()
            filtered_n = len(getattr(self.ctx, "filtered_items", []))
            c = service.counts(self.ctx)
            self._append_log(
                f"扫描完成：{c['total']} 个有效游戏（已拥有 {sum(1 for g in self.ctx.games if g.owned and not self.ctx.state.is_ignored(g.appid))}，"
                f"已安装 {c['installed']}），已自动过滤 {filtered_n} 个系统工具/运行库/非游戏条目"
                + (f"，手动忽略 {c['ignored']} 个" if c["ignored"] else "")
            )
            for warning in getattr(self.ctx, "warnings", [])[:5]:
                self._append_log(f"缓存警告：{warning}")
            self.update_status()
            self._maybe_auto_translate_new()
        elif name == "auto_new_translate":
            service.auto_categorize(self.ctx)
            self.refresh_table()
            self.refresh_sidebar()
            self._append_log("启动自动翻译完成")
            self.update_status()
        elif name in ("translate", "retranslate_sel"):
            service.auto_categorize(self.ctx)
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            c = service.counts(self.ctx)
            summary = (
                f"官方 {c['official']} · 词典 {c['dictionary']} · 维基 {c['wikidata']} · "
                f"商店 {c['steam']} · AI {c['llm']} · 机翻 {c['machine']} · 待翻译 {c['missing']}"
            )
            self._append_log(f"翻译完成：{summary}")
            if name == "translate":
                messagebox.showinfo("翻译完成", summary, parent=self.root)
        elif name == "categorize":
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            self._append_log("自动分类完成（最多 3 个标签）")
        elif name == "apply":
            res, ver = result
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            msg = (
                f"已写入中文名 {res.names_applied} 个，"
                f"更新 ACF {res.acfs_updated} 个，"
                f"更新收藏集 {res.collections_changed} 个。"
            )
            if res.warnings:
                msg += "\n\n提示：\n" + "\n".join(res.warnings[:8])
            if ver:
                msg += (
                    f"\n\n重启后校验：名称 {ver.verified_names} 个正确，"
                    f"{len(ver.verify_failed)} 个异常；"
                    f"收藏集 {'正常' if ver.collections_verified else '异常'}。"
                )
            if ver and (ver.verify_failed or not ver.collections_verified):
                msg += "\n\n如果 Steam 覆盖了数据，可从“更多操作”点击“一键修复”。"
                messagebox.showwarning("应用完成（有异常）", msg, parent=self.root)
            else:
                messagebox.showinfo("应用完成", msg, parent=self.root)
            self._append_log(msg.replace("\n", " "))
        elif name == "undo_all":
            res = result
            self.last_batch_snapshot = None
            self._update_undo_button()
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            msg = (
                f"成功恢复 {res.names_restored} 个游戏名称，"
                f"清空 {res.categories_cleared} 个分类，"
                f"删除 {res.collections_deleted} 个本程序创建的收藏集。"
                "\n\n如需重新汉化/分类，点“自动翻译/自动分类”即可；"
                "只会重置复原记录，手动修改会保留。"
            )
            if res.warning:
                msg += "\n\n提示：\n" + "\n".join(res.warning[:5])
            messagebox.showinfo("复原完成", msg, parent=self.root)
            self._append_log(msg.replace("\n", " "))
        elif name == "autofix":
            res, ver = result
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            self._append_log(f"修复完成：写入 {res.names_applied} 个名称")
            messagebox.showinfo(
                "修复完成",
                f"写入 {res.names_applied} 个名称，"
                f"校验 {ver.verified_names if ver else 0} 个正确。",
                parent=self.root,
            )
        elif name == "restore":
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            self._append_log("还原完成")
            messagebox.showinfo("还原完成", "已从备份恢复原始数据。", parent=self.root)
            if self.ctx is None:
                self.reload()
        elif name == "fetch_desc":
            count = int(result or 0)
            for g in self.ctx.games if self.ctx else []:
                desc = descriptions.get_description(g.appid)
                if desc:
                    g.description = desc
            if self.ctx:
                service.auto_categorize(self.ctx)
            self.refresh_table()
            self.refresh_sidebar()
            self._append_log(f"简介补全完成：当前缓存 {count} 条")
            messagebox.showinfo("简介补全", f"当前共有 {count} 条商店简介缓存，并已重新分类。", parent=self.root)

    # ------------------------------------------------------------- sidebar
    def refresh_sidebar(self) -> None:
        if not hasattr(self, "sidebar"):
            return
        selected = self.sidebar.selection()
        self.sidebar.delete(*self.sidebar.get_children())
        if not self.ctx:
            return
        games = self.ctx.games
        st = self.ctx.state
        active_games = [g for g in games if not st.is_ignored(g.appid)]
        ignored_count = len(games) - len(active_games)

        total = len(active_games)
        translated = sum(
            1 for g in active_games
            if str(st.get_app(g.appid).get("zh_name") or "").strip()
            and str(st.get_app(g.appid).get("source") or "") != "original"
        )
        original = sum(
            1 for g in active_games
            if str(st.get_app(g.appid).get("source") or "") == "original"
        )
        missing = total - translated - original
        review = sum(
            1 for g in active_games
            if str(st.get_app(g.appid).get("source") or "") in ("machine", "llm")
        )
        applied = sum(1 for g in active_games if st.get_app(g.appid).get("applied"))
        installed = sum(1 for g in active_games if g.installed)
        cats: dict[str, int] = {}
        uncategorized = 0
        for g in active_games:
            got = category_state.get_categories(st.get_app(g.appid))
            if not got:
                uncategorized += 1
            for cat in got:
                cats[cat] = cats.get(cat, 0) + 1

        def add(iid: str, text: str, tags: tuple[str, ...] = ()) -> None:
            self.sidebar.insert("", "end", iid=iid, text=text, tags=tags)

        add("all", f"全部游戏   {total}")
        add("translated", f"已翻译   {translated}")
        add("missing", f"待翻译   {missing}")
        add("review", f"需核对 (AI/机翻)   {review}")
        add("applied", f"已应用   {applied}")
        add("installed", f"已安装   {installed}")
        add("uncategorized", f"未分类   {uncategorized}")
        add("original", f"已复原   {original}")
        add("ignored", f"已忽略   {ignored_count}")
        add("section:cat", "── 分类 ──", ("section",))
        for cat in category_model.category_names():
            add(f"cat:{cat}", f"● {cat}   {cats.get(cat, 0)}", (f"cat:{cat}",))
        for cat in category_model.category_names():
            color = CATEGORY_COLORS.get(cat, "#7d8a96")
            try:
                self.sidebar.tag_configure(f"cat:{cat}", foreground=color)
            except tk.TclError:
                pass
        self.sidebar.tag_configure("section", foreground="#5f7180")
        for iid in selected:
            if self.sidebar.exists(iid):
                try:
                    self._sidebar_guard = True
                    self.sidebar.selection_set(iid)
                except tk.TclError:
                    pass
                finally:
                    self._sidebar_guard = False

    def _on_sidebar(self, _event=None) -> None:
        if self._sidebar_guard or not hasattr(self, "sidebar"):
            return
        sel = self.sidebar.selection()
        if not sel:
            return
        iid = str(sel[0])
        if iid.startswith("section:"):
            return
        self._sidebar_guard = True
        try:
            # Always reset cross-filters cleanly so switching from "已安装" doesn't leak.
            self.installed_var.set(False)
            self.category_var.set("全部分类")
            mapping = {
                "all": "全部",
                "translated": "已翻译",
                "missing": "待翻译",
                "review": "需核对",
                "original": "已复原",
                "applied": "已应用",
                "uncategorized": "未分类",
                "ignored": "已忽略",
            }
            if iid == "installed":
                self.filter_var.set("已安装")
                self.installed_var.set(True)
            elif iid in mapping:
                self.filter_var.set(mapping[iid])
            elif iid.startswith("cat:"):
                self.filter_var.set("全部")
                self.category_var.set(iid[4:])
        finally:
            self._sidebar_guard = False
        self.refresh_table(preserve_scroll=False)

    # --------------------------------------------------------------- table
    def _build_row(self, g) -> ui_widgets.TableRow:
        rec = self.ctx.state.get_app(g.appid)
        zh = str(rec.get("zh_name") or "")
        src = str(rec.get("source") or "")
        cats = category_state.get_categories(rec)
        ignored = self.ctx.state.is_ignored(g.appid)
        if ignored:
            status = "已忽略"
        elif src == "original":
            status = "已复原"
        elif not zh:
            status = "待翻译"
        elif rec.get("applied"):
            status = "已应用"
        else:
            status = "可应用"
        return ui_widgets.TableRow(
            appid=g.appid,
            zh=zh,
            orig=str(rec.get("orig_name") or g.name),
            categories=cats,
            source="已忽略" if ignored else SOURCE_LABEL.get(src, src or "待翻译"),
            status=status,
            installed=g.installed,
            locked=bool(rec.get("locked")),
            ignored=ignored,
            raw=g,
        )

    def _update_single_row(self, appid: int) -> None:
        if not self.ctx or not hasattr(self, "table"):
            return
        g = self.games_by_id.get(appid)
        if not g:
            return
        self.table.update_row(self._build_row(g))

    def _update_selection_label(self) -> None:
        if not hasattr(self, "table"):
            return
        chk = len(self.table.checked_ids())
        sel = len(self.table.selected_ids())
        if chk:
            self.sel_var.set(f"已勾选 {chk} 项")
        elif sel:
            self.sel_var.set(f"已选中 {sel} 项")
        else:
            self.sel_var.set("未勾选")
        is_ignored_view = self.filter_var.get() == "已忽略"
        if hasattr(self, "batch_ignore_btn"):
            self.batch_ignore_btn.configure(text="恢复管理" if is_ignored_view else "忽略选中")

    def refresh_table(self, *, preserve_scroll: bool = True) -> None:
        if not self.ctx or not hasattr(self, "table"):
            return
        q = self.search_var.get().strip().lower()
        flt = self.filter_var.get()
        cat_filter = self.category_var.get()
        only_installed = bool(self.installed_var.get())
        show_ignored = flt == "已忽略"

        rows: list[ui_widgets.TableRow] = []
        for g in self.ctx.games:
            ignored = self.ctx.state.is_ignored(g.appid)
            if show_ignored:
                if not ignored:
                    continue
            else:
                if ignored:
                    continue

            rec = self.ctx.state.get_app(g.appid)
            zh = str(rec.get("zh_name") or "")
            src = str(rec.get("source") or "")
            cats = category_state.get_categories(rec)
            orig = str(rec.get("orig_name") or g.name)

            hay = " ".join([orig, g.name, zh, str(g.appid), " ".join(cats)]).lower()
            if q and q not in hay:
                continue
            if not show_ignored:
                if flt == "已翻译" and (not zh or src == "original"):
                    continue
                if flt == "待翻译" and (zh or src == "original"):
                    continue
                if flt == "需核对" and src not in ("machine", "llm"):
                    continue
                if flt == "已复原" and src != "original":
                    continue
                if flt == "未分类" and cats:
                    continue
                if flt == "机翻" and src != "machine":
                    continue
                if flt == "AI" and src != "llm":
                    continue
                if flt == "维基" and src != "wikidata":
                    continue
                if flt == "官方/词典" and src not in ("official", "dictionary", "steam", "already"):
                    continue
                if flt == "手动" and src != "manual":
                    continue
                if flt == "已应用" and not rec.get("applied"):
                    continue
                if flt == "已安装" and not g.installed:
                    continue
                if flt == "已拥有" and not g.owned:
                    continue
            if cat_filter != "全部分类" and cat_filter not in cats:
                continue
            if only_installed and not g.installed:
                continue
            rows.append(self._build_row(g))

        if self.sort_key:
            def key(row: ui_widgets.TableRow):
                if self.sort_key == "appid":
                    return row.appid
                if self.sort_key == "categories":
                    return " / ".join(row.categories).lower()
                if self.sort_key == "game":
                    return (row.zh or row.orig).lower()
                return str(getattr(row, self.sort_key, "")).lower()
            rows.sort(key=key, reverse=self.sort_desc)

        self.table.sort_key = self.sort_key
        self.table.sort_desc = self.sort_desc
        self.table.set_rows(rows, preserve_scroll=preserve_scroll)
        c = service.counts(self.ctx)
        self.count_var.set(
            f"显示 {len(rows)} / 有效 {c['total']} · 已应用 {c['applied']}"
            + (f" · 已忽略 {c['ignored']}" if c["ignored"] else "")
        )
        self._update_selection_label()

    def on_sort(self, column: str) -> None:
        if column == self.sort_key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key = column
            self.sort_desc = False if column in ("appid", "game") else True
        self.refresh_table(preserve_scroll=False)

    def selected_appids(self) -> set[int]:
        if not hasattr(self, "table"):
            return set()
        return self.table.active_ids()

    def on_table_double(self, appid: int, column: str) -> None:
        if not self.ctx:
            return
        game = self.games_by_id.get(appid)
        if not game:
            return
        rec = self.ctx.state.get_app(appid)
        if column == "categories":
            CategoryPickerDialog(self, game, rec)
        else:
            NameEditDialog(self, game, rec)

    def on_table_context(self, event: tk.Event, appid: int) -> None:
        if not self.ctx:
            return
        ignored = self.ctx.state.is_ignored(appid)
        rec = self.ctx.state.get_app(appid)
        locked = bool(rec.get("locked"))
        menu = tk.Menu(
            self.root, tearoff=0, bg=PANEL2, fg=FG,
            activebackground="#284c6b", activeforeground="#ffffff",
            font=("Microsoft YaHei UI", 9),
        )
        menu.add_command(label="编辑名称 (Enter)", command=lambda: self._open_name(appid))
        menu.add_command(label="编辑分类", command=lambda: self._open_category(appid))
        menu.add_command(label="重新翻译选中", command=self.on_retranslate_selected)
        menu.add_separator()
        menu.add_command(
            label="打开 Steam 商店页",
            command=lambda: webbrowser.open(f"https://store.steampowered.com/app/{appid}/"),
        )
        menu.add_command(
            label="打开 SteamDB",
            command=lambda: webbrowser.open(f"https://steamdb.info/app/{appid}/"),
        )
        menu.add_separator()
        menu.add_command(label="批量追加分类…", command=lambda: self.on_batch("append"))
        menu.add_command(label="批量替换分类…", command=lambda: self.on_batch("replace"))
        menu.add_command(label="批量移除分类…", command=lambda: self.on_batch("remove"))
        menu.add_command(label="重新自动分类选中", command=self.on_auto_selected)
        menu.add_command(label="清除选中分类", command=self.on_clear_selected)
        menu.add_separator()
        menu.add_command(
            label="解锁选中名称" if locked else "锁定选中名称",
            command=lambda: self._toggle_lock_selected(not locked),
        )
        if ignored:
            menu.add_command(label="取消忽略（恢复管理）", command=self.on_toggle_ignore_selected)
        else:
            menu.add_command(label="忽略选中条目（不汉化、不入收藏集）", command=self.on_toggle_ignore_selected)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_name(self, appid: int) -> None:
        g = self.games_by_id.get(appid)
        if g and self.ctx:
            NameEditDialog(self, g, self.ctx.state.get_app(appid))

    def _open_category(self, appid: int) -> None:
        g = self.games_by_id.get(appid)
        if g and self.ctx:
            CategoryPickerDialog(self, g, self.ctx.state.get_app(appid))

    def _toggle_lock_selected(self, lock: bool) -> None:
        if not self.ctx:
            return
        ids = self.selected_appids()
        if not ids:
            return
        for aid in ids:
            self.ctx.state.get_app(aid)["locked"] = lock
        self.ctx.state.save()
        self.refresh_table()
        self._append_log(f"已{'锁定' if lock else '解锁'} {len(ids)} 个游戏名称")

    # ------------------------------------------------------------- batch
    def on_batch(self, mode: str = "append") -> None:
        if not self.ctx:
            return
        ids = self.selected_appids()
        if not ids:
            messagebox.showinfo("提示", "先勾选或选中若干游戏。", parent=self.root)
            return
        BatchCategoryDialog(self, ids, mode)

    def on_auto_selected(self) -> None:
        if not self.ctx:
            return
        ids = self.selected_appids()
        if not ids:
            messagebox.showinfo("提示", "先选择要重新分类的游戏。", parent=self.root)
            return
        for appid in ids:
            game = self.games_by_id.get(appid)
            if not game:
                continue
            rec = self.ctx.state.get_app(appid)
            res = scoring.classify(
                game,
                description=getattr(game, "description", "") or "",
                extra_names=[str(rec.get("orig_name") or "")],
            )
            category_state.set_categories(
                rec, res.categories, source="auto", scores=res.scores,
                reasons=res.reasons, locked=False,
            )
        self.ctx.state.save()
        self.refresh_table()
        self.refresh_sidebar()
        self.log(f"已重新自动分类 {len(ids)} 个游戏")

    def on_clear_selected(self) -> None:
        if not self.ctx:
            return
        ids = self.selected_appids()
        if not ids:
            messagebox.showinfo("提示", "先选择要清除分类的游戏。", parent=self.root)
            return
        if not messagebox.askyesno("清除分类", f"清除 {len(ids)} 个游戏的分类？", parent=self.root):
            return
        for appid in ids:
            rec = self.ctx.state.get_app(appid)
            category_state.set_categories(rec, [], source="manual", locked=True)
        self.ctx.state.save()
        self.refresh_table()
        self.refresh_sidebar()
        self.log(f"已清除 {len(ids)} 个游戏的分类")

    def on_toggle_ignore_selected(self) -> None:
        if not self.ctx:
            return
        ids = self.selected_appids()
        if not ids:
            messagebox.showinfo("提示", "请先勾选或选中条目。", parent=self.root)
            return
        if self.filter_var.get() == "已忽略":
            n = service.unignore_games(self.ctx, ids)
            self._append_log(f"已恢复管理 {n} 个条目")
        else:
            if len(ids) > 5 and not messagebox.askyesno(
                "确认忽略",
                f"将忽略 {len(ids)} 个条目（后续不会汉化也不会写入收藏集，可随时在“已忽略”中恢复）。继续？",
                parent=self.root,
            ):
                return
            n = service.ignore_games(self.ctx, ids)
            self._append_log(f"已忽略 {n} 个条目（可在左侧“已忽略”中恢复）")
        self.refresh_table()
        self.refresh_sidebar()
        self.update_status()

    def on_undo_batch(self) -> None:
        if not self.ctx or not self.last_batch_snapshot:
            return
        service.batch_undo(self.ctx, self.last_batch_snapshot)
        self.last_batch_snapshot = None
        self._update_undo_button()
        self.refresh_table()
        self.refresh_sidebar()
        self.log("已撤销上次批量分类")

    # ------------------------------------------------------------- actions
    def reload(self) -> None:
        if self.busy:
            return
        self._append_log("正在扫描 Steam 库 ...")
        settings = dict(self.settings)
        self.run_task("load", lambda: service.load_context(settings))

    def _maybe_auto_translate_new(self) -> None:
        if not self.ctx:
            return
        if not bool(self.settings.get("auto_translate_new", True)):
            return
        if not bool(self.settings.get("use_network", True)):
            self._append_log("已关闭联网翻译：新增游戏只使用官方名/内置词典")
            return
        missing = [
            g for g in self.ctx.games
            if not self.ctx.state.is_ignored(g.appid)
            and not str(self.ctx.state.get_app(g.appid).get("zh_name") or "").strip()
        ]
        new_count = len(self.ctx.new_appids)
        if not missing:
            if new_count:
                self._append_log(f"新增 {new_count} 个游戏，已由官方名/内置词典覆盖")
            return
        self._append_log(
            f"发现 {len(missing)} 个待翻译游戏，开始并发多源自动翻译..."
        )
        settings = dict(self.settings)
        self.cancel_flag = False

        def work():
            return service.auto_translate(
                self.ctx,
                use_network=True,
                games=missing,
                settings=settings,
                progress=lambda i, n, msg: self.bus.put(("progress", (i, n, msg))),
                should_stop=lambda: self.cancel_flag,
                on_item=lambda aid, _rec: self.bus.put(("item_updated", aid)),
                log=lambda m: self.bus.put(("log", m)),
            )

        self.run_task("auto_new_translate", work)

    def on_translate(self) -> None:
        if not self.ctx or self.busy:
            return
        c = service.counts(self.ctx)
        use_network = bool(self.settings.get("use_network", True))
        opts = translate.TranslateOptions.from_settings(self.settings)
        eff = opts.effective_sources(allow_network=use_network)
        src_hint = " → ".join(translate.SOURCE_META[s][0] for s in eff) or "无可用翻译源"
        if not messagebox.askyesno(
            "自动翻译",
            f"将为 {c['total']} 个有效游戏补齐中文名（当前待翻译 {c['missing']} 个）。\n"
            f"当前优先级：{src_hint}\n\n是否开始？",
            parent=self.root,
        ):
            return
        settings = dict(self.settings)
        self.cancel_flag = False
        self._append_log(f"开始并发翻译（{src_hint}）...")

        def work():
            service.reset_undo_names(
                self.ctx, log=lambda m: self.bus.put(("log", m))
            )
            return service.auto_translate(
                self.ctx,
                use_network=use_network,
                settings=settings,
                progress=lambda i, n, msg: self.bus.put(("progress", (i, n, msg))),
                should_stop=lambda: self.cancel_flag,
                on_item=lambda aid, _rec: self.bus.put(("item_updated", aid)),
                log=lambda m: self.bus.put(("log", m)),
            )

        self.run_task("translate", work)

    def on_retranslate_selected(self) -> None:
        if not self.ctx or self.busy:
            return
        ids = self.selected_appids()
        if not ids:
            messagebox.showinfo("提示", "请先勾选或选中要重新翻译的游戏。", parent=self.root)
            return
        targets = [
            self.games_by_id[aid]
            for aid in ids
            if aid in self.games_by_id and not self.ctx.state.is_ignored(aid)
        ]
        if not targets:
            return
        use_network = bool(self.settings.get("use_network", True))
        settings = dict(self.settings)
        self.cancel_flag = False
        self._append_log(f"正在重新翻译选中的 {len(targets)} 个游戏...")

        def work():
            return service.auto_translate(
                self.ctx,
                use_network=use_network,
                games=targets,
                settings=settings,
                force=True,
                progress=lambda i, n, msg: self.bus.put(("progress", (i, n, msg))),
                should_stop=lambda: self.cancel_flag,
                on_item=lambda aid, _rec: self.bus.put(("item_updated", aid)),
                log=lambda m: self.bus.put(("log", m)),
            )

        self.run_task("retranslate_sel", work)

    def on_categorize(self) -> None:
        if not self.ctx:
            return
        service.reset_undo_categories(self.ctx, log=self._append_log)
        service.auto_categorize(self.ctx)
        self.refresh_table()
        self.refresh_sidebar()
        self.update_status()
        self._append_log("已按类型/标签/简介关键词重新分类（最多 3 个，手动锁定不覆盖）")

    def on_fetch_descriptions(self) -> None:
        if not self.ctx or self.busy:
            return
        if not bool(self.settings.get("use_network", True)):
            messagebox.showinfo("提示", "设置中未启用联网。", parent=self.root)
            return
        missing = [
            g.appid for g in self.ctx.games
            if not self.ctx.state.is_ignored(g.appid) and not getattr(g, "description", "")
        ]
        if not missing:
            messagebox.showinfo("提示", "当前有效游戏都已有商店简介缓存。", parent=self.root)
            return
        if not messagebox.askyesno(
            "补全简介",
            f"将并发获取 {len(missing)} 个游戏的 Steam 商店简介以提升分类准确度。\n是否继续？",
            parent=self.root,
        ):
            return
        proxy = str(self.settings.get("proxy") or "").strip() or None
        sys_proxy = bool(self.settings.get("use_system_proxy", True))
        conc = int(self.settings.get("concurrency", 6))
        self.cancel_flag = False

        def work():
            cache = descriptions.fetch_many(
                missing,
                proxy=proxy,
                use_system_proxy=sys_proxy,
                concurrency=conc,
                progress=lambda i, n, msg: self.bus.put(("progress", (i, n, msg))),
                should_stop=lambda: self.cancel_flag,
            )
            for g in self.ctx.games:
                text = cache.get(str(g.appid))
                if text:
                    g.description = text
            return len(cache)

        self.run_task("fetch_desc", work)

    def on_apply(self, selected: bool, *, force_collections: bool | None = None) -> None:
        if not self.ctx or self.busy:
            return
        sel_ids = self.selected_appids()
        if selected and not sel_ids:
            messagebox.showinfo("提示", "先在表格中勾选或选中要应用的游戏。", parent=self.root)
            return
        all_count = sum(
            1 for g in self.ctx.games
            if not self.ctx.state.is_ignored(g.appid)
            and str(self.ctx.state.get_app(g.appid).get("zh_name") or "").strip()
        )
        if all_count == 0 and force_collections is False:
            messagebox.showinfo("提示", "没有可应用的中文名。", parent=self.root)
            return

        def _run_apply(*, selected: set[int] | None, apply_names: bool, apply_collections: bool, restart: bool):
            lock = bool(self.settings.get("lock_appinfo", False))
            bk_keep = int(self.settings.get("backup_keep", 10))

            def work():
                res = service.do_apply(
                    self.ctx,
                    selected=selected,
                    restart=restart,
                    lock_appinfo=lock,
                    apply_names=apply_names,
                    apply_collections=apply_collections,
                    backup_keep=bk_keep,
                    progress=lambda m: self.bus.put(("progress", (0, 0, m))),
                    log=lambda m: self.bus.put(("log", m)),
                )
                ver = None
                if restart:
                    self.bus.put(("log", "等待 Steam 启动后校验（约 12 秒）..."))
                    try:
                        ver = service.do_verify(
                            self.ctx,
                            wait=12,
                            selected=selected,
                            apply_names=apply_names,
                            apply_collections=apply_collections,
                            log=lambda m: self.bus.put(("log", m)),
                        )
                    except Exception as exc:
                        self.bus.put(("log", f"写入已完成，但重启后校验跳过：{exc}"))
                return res, ver

            self.run_task("apply", work)

        ApplyDialog(
            self,
            all_count=all_count,
            selected_ids=sel_ids,
            default_selected=selected,
            default_collections=force_collections,
            on_confirm=_run_apply,
        )

    def on_import_dict(self) -> None:
        if not self.ctx:
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title="导入自定义词典 (JSON 或 CSV)",
            filetypes=[("词典文件", "*.json *.csv"), ("JSON", "*.json"), ("CSV", "*.csv")],
        )
        if not path:
            return
        try:
            total, matched = service.import_dictionary(self.ctx, Path(path), apply_to_state=True)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self.root)
            return
        self.refresh_table()
        self.refresh_sidebar()
        self.update_status()
        msg = f"已导入 {total} 条词典记录到用户词典，其中命中当前库 {matched} 个游戏。"
        self._append_log(msg)
        messagebox.showinfo("导入完成", msg, parent=self.root)

    def on_export_dict(self) -> None:
        if not self.ctx:
            return
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出自定义中文词典",
            defaultextension=".json",
            initialfile="steam_zh_dictionary.json",
            filetypes=[("JSON 词典", "*.json"), ("CSV 表格", "*.csv")],
        )
        if not path:
            return
        try:
            count = service.export_dictionary(self.ctx, Path(path), only_manual=False)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        msg = f"已导出 {count} 条中文词典到：\n{path}"
        self._append_log(msg.replace("\n", " "))
        messagebox.showinfo("导出完成", msg, parent=self.root)

    def on_undo_all(self) -> None:
        if not self.ctx or self.busy:
            return
        names = 0
        categories = 0
        for rec in self.ctx.state.apps.values():
            zh = str(rec.get("zh_name") or "").strip()
            orig = str(rec.get("orig_name") or "").strip()
            if zh and (zh != orig or rec.get("applied")):
                names += 1
            if category_state.get_categories(rec):
                categories += 1
        collections_count = len(self.ctx.state.collections)
        if names == 0 and categories == 0 and collections_count == 0:
            messagebox.showinfo("无需复原", "当前没有检测到已汉化/已分类的游戏。", parent=self.root)
            return
        restart = bool(self.settings.get("restart_steam", True))
        text = (
            "这会执行“后悔药”全量撤销：\n\n"
            f"• 恢复 {names} 个游戏名称为原始名称\n"
            f"• 清空 {categories} 个游戏的分类（变为未分类）\n"
            f"• 软删除本程序创建的 {collections_count} 个收藏集\n"
            "• Steam 会关闭后重新启动\n"
            "• 操作前会自动完整备份\n\n"
            "注意：手动修改过的名称/分类也会被一起撤销。\n\n"
            "确定要继续吗？"
        )
        if not messagebox.askyesno(
            "一键复原（后悔药）", text, parent=self.root, icon="warning", default="no",
        ):
            return

        def work():
            return service.do_undo_all(
                self.ctx,
                restart=restart,
                progress=lambda m: self.bus.put(("progress", (0, 0, m))),
                log=lambda m: self.bus.put(("log", m)),
            )

        self.run_task("undo_all", work)

    def on_autofix(self) -> None:
        if not self.ctx or self.busy:
            return
        if not messagebox.askyesno(
            "一键修复",
            "将关闭 Steam（如正在运行），重新写入中文名，然后启动 Steam。\n是否继续？",
            parent=self.root,
        ):
            return
        restart = bool(self.settings.get("restart_steam", True))
        lock = bool(self.settings.get("lock_appinfo", False))
        apply_cols = bool(self.settings.get("apply_collections", True))
        bk_keep = int(self.settings.get("backup_keep", 10))

        def work():
            res = service.do_autofix(
                self.ctx,
                restart=restart,
                lock_appinfo=lock,
                apply_collections=apply_cols,
                backup_keep=bk_keep,
                log=lambda m: self.bus.put(("log", m)),
            )
            ver = None
            if restart:
                try:
                    ver = service.do_verify(
                        self.ctx,
                        wait=12,
                        apply_collections=apply_cols,
                        log=lambda m: self.bus.put(("log", m)),
                    )
                except Exception as exc:
                    self.bus.put(("log", f"修复写入已完成，但校验跳过：{exc}"))
            return res, ver

        self.run_task("autofix", work)

    def on_restore(self) -> None:
        if self.busy:
            return
        backups = service.latest_backups(15)
        if not backups:
            messagebox.showinfo("提示", "还没有备份。", parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("选择备份")
        win.configure(bg=BG)
        win.transient(self.root)
        win.grab_set()
        win.bind("<Escape>", lambda _e: win.destroy())
        tk.Label(win, text="选择要还原的备份（会关闭 Steam）", bg=BG, fg=FG).pack(padx=12, pady=8)
        lb = tk.Listbox(win, width=56, height=10, bg=PANEL2, fg=FG, relief="flat")
        lb.pack(padx=12, pady=6)
        for b in backups:
            lb.insert("end", b.name)
        lb.selection_set(0)
        restart_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            win, text="还原后启动 Steam", variable=restart_var, bg=BG, fg=FG,
            selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
        ).pack(anchor="w", padx=12)

        def do_restore():
            sel = lb.curselection()
            if not sel:
                return
            b = backups[sel[0]]
            restart = bool(restart_var.get())
            win.destroy()
            if not messagebox.askyesno(
                "确认还原",
                f"将用备份 {b.name} 覆盖当前的 appinfo 和收藏集。\n会关闭 Steam。是否继续？",
                parent=self.root,
            ):
                return

            settings = dict(self.settings)

            def work():
                service.do_restore(
                    self.ctx,
                    b,
                    restart=restart,
                    log=lambda m: self.bus.put(("log", m)),
                    settings=settings,
                )
                return str(b)

            self.run_task("restore", work)

        btn_row = tk.Frame(win, bg=BG)
        btn_row.pack(fill="x", padx=12, pady=(8, 12))
        ttk.Button(btn_row, text="取消", command=win.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btn_row, text="还原", style="Accent.TButton", command=do_restore).pack(side="right")

    def on_export(self) -> None:
        if not self.ctx:
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出清单", defaultextension=".csv",
            initialfile="steam_zh_report.csv", filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        service.export_report(self.ctx, Path(path))
        self._append_log(f"已导出到 {path}")
        messagebox.showinfo("导出完成", path, parent=self.root)

    def on_settings(self) -> None:
        SettingsDialog(self)

    def on_cancel(self) -> None:
        self.cancel_flag = True
        self._append_log("已请求取消（等待当前进行中的请求结束）")

    def update_status(self, *, check_steam: bool = True) -> None:
        if not self.ctx:
            self.status_var.set("未加载")
            self.header_status.set("未加载")
            return
        c = service.counts(self.ctx)
        now = time.monotonic()
        if check_steam and (now - self._steam_running_ts >= 4.0 or self._steam_running_ts == 0.0):
            self._steam_running_val = steamenv.steam_running()
            self._steam_running_ts = now
        running = self._steam_running_val
        cache = "分片缓存" if self.ctx.env.has_shards else "单文件缓存"
        filtered_n = len(getattr(self.ctx, "filtered_items", []))
        text = (
            f"{'Steam 运行中' if running else 'Steam 已关闭'} · "
            f"{self.ctx.env.persona or self.ctx.env.account} · {cache} · "
            f"有效 {c['total']} · 已应用 {c['applied']} · 待翻译 {c['missing']}"
            + (f" · 已过滤工具 {filtered_n}" if filtered_n else "")
        )
        self.status_var.set(text)
        self.header_status.set(
            f"{self.ctx.env.persona or self.ctx.env.account} · "
            f"{'Steam 运行中' if running else 'Steam 已关闭'}"
        )


def main() -> None:
    from . import paths

    _enable_dpi_awareness()
    paths.ensure_data()
    root = tk.Tk()
    SteamZhApp(root)
    root.mainloop()
