from __future__ import annotations

import os
import queue
import threading
import time
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import (
    apply,
    category_model,
    category_state,
    descriptions,
    scoring,
    service,
    steamenv,
    ui_widgets,
)
from .gui_dialogs import (
    APP_TITLE,
    BG,
    CATEGORY_COLORS,
    FG,
    GREEN,
    MUTED,
    ORANGE,
    PANEL,
    PANEL2,
    RED,
    SOURCE_LABEL,
    STATUS_FILTERS,
    ACCENT,
    BatchCategoryDialog,
    CategoryPickerDialog,
    NameEditDialog,
    SettingsDialog,
)
from .state import State


class SteamZhApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        try:
            scaling = float(root.tk.call("tk", "scaling")) or 1.0
        except Exception:
            scaling = 1.0
        # Geometry is in screen pixels; Tk widgets are additionally scaled by
        # the system DPI factor.  Use the physical screen here so a 125%/150%
        # display does not push the right-hand table columns off-screen.
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w = min(sw, 1460)
        h = sh
        self.root.geometry(f"{w}x{h}+0+0")
        self.root.minsize(1000, 640)
        self.root.configure(bg=BG)
        self.root.option_add("*TCombobox*Listbox.background", PANEL2)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#2a475e")
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
        self.buttons: dict[str, ttk.Button] = {}
        self._build_style()
        self._build_ui()
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
        style.configure("TButton", background=PANEL2, foreground=FG, padding=(8, 4))
        style.map("TButton", background=[("active", "#2a475e")])
        style.configure(
            "Accent.TButton", background="#2a6f97", foreground="white", padding=(9, 4)
        )
        style.map("Accent.TButton", background=[("active", "#3a8fbd")])
        style.configure(
            "Danger.TButton", background="#7d2b2b", foreground="#ffffff", padding=(9, 4)
        )
        style.map("Danger.TButton", background=[("active", "#a33a3a")])
        style.configure("Small.TButton", padding=(3, 1),
                        font=("Microsoft YaHei UI", 8))
        style.configure("Group.TLabel", background=BG, foreground=MUTED,
                        font=("Microsoft YaHei UI", 8))
        style.configure("Sidebar.Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, rowheight=27, borderwidth=0)
        style.map("Sidebar.Treeview", background=[("selected", "#2a475e")])
        style.configure("TEntry", fieldbackground=PANEL2, foreground=FG)
        style.configure(
            "TCombobox", fieldbackground=PANEL2, background=PANEL2,
            foreground=FG, arrowcolor=FG, selectbackground=PANEL2,
            selectforeground=FG,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", PANEL2)],
            foreground=[("readonly", FG)],
            selectbackground=[("readonly", PANEL2)],
            selectforeground=[("readonly", FG)],
        )
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.configure("TProgressbar", background=ACCENT, troughcolor=PANEL2)
        style.configure("Horizontal.TScrollbar", background=PANEL2)

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        self.status_var = tk.StringVar(value="正在加载 ...")
        self.count_var = tk.StringVar(value="")
        self.sel_var = tk.StringVar(value="")
        header = tk.Frame(self.root, bg=PANEL, height=64)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        tk.Label(
            header, text="Steam 统译 (Steam Unify)", bg=PANEL, fg="#ffffff",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(side="left", padx=18, pady=10)
        tk.Label(
            header, text="多标签分类 · 批量整理 · 重启保留", bg=PANEL, fg=MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", pady=10)
        self.header_status = tk.StringVar(value="正在加载 ...")
        tk.Label(
            header, textvariable=self.header_status, bg=PANEL, fg=ACCENT,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right", padx=(6, 18))
        self.progress = ttk.Progressbar(header, mode="determinate", maximum=100, length=130)
        self.progress.pack(side="right", padx=6)
        self.log_toggle_btn = ttk.Button(
            header, text="日志", style="Small.TButton", command=self._toggle_log
        )
        self.log_toggle_btn.pack(side="right", padx=4)
        self.cancel_btn = ttk.Button(
            header, text="取消", style="Small.TButton",
            command=self.on_cancel, state="disabled",
        )
        self.cancel_btn.pack(side="right", padx=4)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        sidebar = tk.Frame(body, bg=PANEL, width=195)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(
            sidebar, text="导航", bg=PANEL, fg=MUTED,
            font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 4))
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

        main = tk.Frame(body, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        main.grid_rowconfigure(3, weight=1)
        main.grid_columnconfigure(0, weight=1)

        toolbar = tk.Frame(main, bg=BG)
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        self._build_toolbar(toolbar)

        filters = tk.Frame(main, bg=BG)
        filters.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        self._build_filters(filters)

        table_frame = tk.Frame(main, bg=BG)
        table_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=4)
        self.table = ui_widgets.TagTable(
            table_frame,
            on_selection=lambda _ids: self._update_selection_label(),
            on_check=lambda _ids: self._update_selection_label(),
            on_double=self.on_table_double,
            on_context=self.on_table_context,
            on_sort=self.on_sort,
        )
        self.table.pack(fill="both", expand=True)

        self.log_frame = tk.Frame(main, bg="#0e141b", highlightthickness=1,
                                  highlightbackground="#2a3b4d")
        log_head = tk.Frame(self.log_frame, bg=PANEL)
        log_head.pack(fill="x")
        self.log_last_var = tk.StringVar(value="日志")
        tk.Label(
            log_head, textvariable=self.log_last_var, bg=PANEL, fg=MUTED,
            anchor="w", font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=8, pady=3, fill="x", expand=True)
        ttk.Button(log_head, text="收起", style="Small.TButton",
                   command=self._toggle_log).pack(side="right", padx=4, pady=2)
        self.log_body = tk.Frame(self.log_frame, bg=BG)
        self.log_body.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            self.log_body, height=6, bg="#0e141b", fg=MUTED, insertbackground=FG,
            relief="flat", wrap="word", font=("Microsoft YaHei UI", 9),
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        log_sb = ttk.Scrollbar(self.log_body, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        log_sb.pack(side="right", fill="y")

        # A compact status bar at the bottom; the log is an overlay drawer so
        # the table can use the whole window height.
        status_bar = tk.Frame(main, bg=PANEL)
        status_bar.place(relx=0.0, rely=1.0, anchor="sw", relwidth=1.0, height=26)
        status_bar.lift()
        tk.Label(
            status_bar, textvariable=self.status_var, bg=PANEL, fg=MUTED,
            anchor="w", font=("Microsoft YaHei UI", 8),
        ).pack(side="left", padx=8, pady=3)
        tk.Label(
            status_bar, textvariable=self.sel_var, bg=PANEL, fg=ACCENT,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right", padx=5)
        tk.Label(
            status_bar, textvariable=self.count_var, bg=PANEL, fg=MUTED,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right", padx=5)

    def _build_toolbar(self, parent: tk.Frame) -> None:
        row1 = tk.Frame(parent, bg=BG)
        row1.pack(fill="x")
        row2 = tk.Frame(parent, bg=BG)
        row2.pack(fill="x", pady=(5, 0))
        row3 = tk.Frame(parent, bg=BG)
        row3.pack(fill="x", pady=(5, 0))

        def make_group(host: tk.Frame, title: str, specs: list[tuple[str, str, object, str]]) -> None:
            group = tk.Frame(host, bg=BG)
            group.pack(side="left", padx=(0, 10))
            ttk.Label(group, text=title, style="Group.TLabel", anchor="w").pack(
                fill="x", pady=(0, 2)
            )
            row = tk.Frame(group, bg=BG)
            row.pack()
            for key, text, command, style_name in specs:
                b = ttk.Button(
                    row, text=text, command=command,
                    style=style_name or "Small.TButton",
                )
                b.pack(side="left", padx=(0, 4))
                self.buttons[key] = b

        make_group(row1, "数据", [
            ("scan", "扫描库", self.reload, ""),
            ("fetch_desc", "补全简介", self.on_fetch_descriptions, ""),
            ("log", "日志", self._toggle_log, ""),
            ("cancel", "取消", self.on_cancel, ""),
        ])
        make_group(row1, "翻译 / 分类", [
            ("translate", "自动翻译", self.on_translate, "Accent.TButton"),
            ("categorize", "自动分类", self.on_categorize, ""),
            ("batch", "批量分类", self.on_batch, ""),
        ])
        make_group(row2, "写入 Steam", [
            ("apply_sel", "应用选中", lambda: self.on_apply(True), ""),
            ("apply_all", "应用全部", lambda: self.on_apply(False), "Accent.TButton"),
            ("autofix", "一键修复", self.on_autofix, ""),
            ("restore", "还原", self.on_restore, ""),
        ])
        make_group(row2, "工具", [
            ("export", "导出", self.on_export, ""),
            ("settings", "设置", self.on_settings, ""),
            ("undo_batch", "撤销批量", self.on_undo_batch, ""),
        ])
        make_group(row3, "危险操作", [
            ("undo_all", "一键复原（后悔药）", self.on_undo_all, "Danger.TButton"),
        ])

    def _build_filters(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="搜索", bg=BG, fg=MUTED).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_table())
        tk.Entry(
            parent, textvariable=self.search_var, width=24, bg=PANEL2, fg=FG,
            insertbackground=FG, relief="flat",
        ).pack(side="left", padx=(6, 12), ipady=4)

        tk.Label(parent, text="状态", bg=BG, fg=MUTED).pack(side="left")
        self.filter_var = tk.StringVar(value="全部")
        status_combo = ttk.Combobox(
            parent, textvariable=self.filter_var, state="readonly", width=9,
            values=STATUS_FILTERS,
        )
        status_combo.pack(side="left", padx=(4, 8))
        status_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_table())

        tk.Label(parent, text="分类", bg=BG, fg=MUTED).pack(side="left")
        self.category_var = tk.StringVar(value="全部分类")
        cat_values = ["全部分类"] + category_model.category_names()
        cat_combo = ttk.Combobox(
            parent, textvariable=self.category_var, state="readonly", width=10,
            values=cat_values,
        )
        cat_combo.pack(side="left", padx=(4, 8))
        cat_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_table())

        self.installed_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            parent, text="仅已安装", variable=self.installed_var,
            bg=BG, fg=MUTED, selectcolor=PANEL2, activebackground=BG,
            activeforeground=FG, command=self.refresh_table,
        ).pack(side="left", padx=(0, 6))

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
        self.log_frame.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0, height=178)
        self.log_frame.lift()
        self.log_visible = True
        self.log_toggle_btn.configure(text="收起日志")

    # --------------------------------------------------------------- tasks
    def _poll_bus(self) -> None:
        try:
            while True:
                kind, payload = self.bus.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    cur, total, msg = payload
                    if total:
                        self.progress.configure(mode="determinate", maximum=total, value=cur)
                    else:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(12)
                    if msg:
                        self.header_status.set(msg)
                        self.status_var.set(msg)
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
        self.root.after(120, self._poll_bus)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in self.buttons.values():
            b.configure(state=state)
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        if hasattr(self, "cancel_btn2"):
            self.cancel_btn2.configure(state="normal" if busy else "disabled")
        self._update_undo_button()

    def _update_undo_button(self) -> None:
        if "undo_batch" not in self.buttons:
            return
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
                service.auto_translate(self.ctx, use_network=False)
            except Exception as exc:
                self._append_log(f"内置名称填充失败：{exc}")
            self.refresh_table()
            self.refresh_sidebar()
            self._append_log(
                f"扫描完成：{len(self.ctx.games)} 个条目，"
                f"已拥有 {sum(1 for g in self.ctx.games if g.owned)}，"
                f"已安装 {sum(1 for g in self.ctx.games if g.installed)}"
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
        elif name == "translate":
            self.refresh_table()
            self.refresh_sidebar()
            self.update_status()
            c = service.counts(self.ctx)
            self._append_log(
                f"翻译完成：官方 {c['official']}，词典 {c['dictionary']}，"
                f"机翻 {c['machine']}，待翻译 {c['missing']}"
            )
            messagebox.showinfo(
                "翻译完成",
                f"官方 {c['official']} · 词典 {c['dictionary']} · "
                f"机翻 {c['machine']} · 待翻译 {c['missing']}",
                parent=self.root,
            )
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
                f"收藏集 {res.collections_changed} 个。"
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
                msg += "\n\n如果 Steam 覆盖了数据，可点击“一键修复”。"
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
        elif name == "export":
            self._append_log(f"已导出：{result}")
            messagebox.showinfo("导出完成", f"已导出到：\n{result}", parent=self.root)
        elif name == "fetch_desc":
            count = int(result or 0)
            for g in self.ctx.games if self.ctx else []:
                desc = descriptions.get_description(g.appid)
                if desc:
                    g.description = desc
            service.auto_categorize(self.ctx) if self.ctx else None
            self.refresh_table()
            self.refresh_sidebar()
            self._append_log(f"简介补全完成：缓存 {count} 条")
            messagebox.showinfo("简介补全", f"已获取 {count} 条简介缓存。", parent=self.root)

    # ------------------------------------------------------------- sidebar
    def refresh_sidebar(self) -> None:
        if not hasattr(self, "sidebar"):
            return
        selected = self.sidebar.selection()
        self.sidebar.delete(*self.sidebar.get_children())
        if not self.ctx:
            return
        games = self.ctx.games
        recs = self.ctx.state
        total = len(games)
        translated = sum(
            1 for g in games
            if str(recs.get_app(g.appid).get("zh_name") or "").strip()
            and str(recs.get_app(g.appid).get("source") or "") != "original"
        )
        original = sum(
            1 for g in games
            if str(recs.get_app(g.appid).get("source") or "") == "original"
        )
        missing = total - translated - original
        applied = sum(1 for g in games if recs.get_app(g.appid).get("applied"))
        installed = sum(1 for g in games if g.installed)
        cats: dict[str, int] = {}
        uncategorized = 0
        for g in games:
            got = category_state.get_categories(recs.get_app(g.appid))
            if not got:
                uncategorized += 1
            for cat in got:
                cats[cat] = cats.get(cat, 0) + 1
        self._sidebar_rows = []
        def add(iid: str, text: str, tags: tuple[str, ...] = ()) -> None:
            self.sidebar.insert("", "end", iid=iid, text=text, tags=tags)
            self._sidebar_rows.append(iid)
        add("all", f"全部游戏   {total}")
        add("translated", f"已翻译   {translated}")
        add("missing", f"待翻译   {missing}")
        add("original", f"已复原   {original}")
        add("applied", f"已应用   {applied}")
        add("installed", f"已安装   {installed}")
        add("uncategorized", f"未分类   {uncategorized}")
        add("section:cat", "分类", ("section",))
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
                    self.sidebar.selection_set(iid)
                except tk.TclError:
                    pass

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
            if iid == "all":
                self.filter_var.set("全部")
                self.category_var.set("全部分类")
                self.installed_var.set(False)
            elif iid == "translated":
                self.filter_var.set("已翻译")
                self.category_var.set("全部分类")
            elif iid == "missing":
                self.filter_var.set("待翻译")
                self.category_var.set("全部分类")
            elif iid == "original":
                self.filter_var.set("已复原")
                self.category_var.set("全部分类")
            elif iid == "applied":
                self.filter_var.set("已应用")
                self.category_var.set("全部分类")
            elif iid == "installed":
                self.filter_var.set("已安装")
                self.category_var.set("全部分类")
                self.installed_var.set(True)
            elif iid == "uncategorized":
                self.filter_var.set("未分类")
                self.category_var.set("全部分类")
            elif iid.startswith("cat:"):
                self.category_var.set(iid[4:])
                self.filter_var.set("全部")
        finally:
            self._sidebar_guard = False
        self.refresh_table()

    # --------------------------------------------------------------- table
    def _update_selection_label(self) -> None:
        if not hasattr(self, "table"):
            return
        active = self.table.active_ids()
        self.sel_var.set(f"已选 {len(active)}" if active else "")

    def refresh_table(self) -> None:
        if not self.ctx or not hasattr(self, "table"):
            return
        q = self.search_var.get().strip().lower()
        flt = self.filter_var.get()
        cat_filter = self.category_var.get()
        only_installed = bool(self.installed_var.get())
        rows: list[ui_widgets.TableRow] = []
        for g in self.ctx.games:
            rec = self.ctx.state.get_app(g.appid)
            zh = str(rec.get("zh_name") or "")
            src = str(rec.get("source") or "")
            cats = category_state.get_categories(rec)
            if src == "original":
                status = "已复原"
            elif not zh:
                status = "待翻译"
            elif rec.get("applied"):
                status = "已应用"
            else:
                status = "可应用"
            hay = " ".join([g.name, zh, str(g.appid), " ".join(cats)]).lower()
            if q and q not in hay:
                continue
            if flt == "已翻译" and (not zh or src == "original"):
                continue
            if flt == "待翻译" and (zh or src == "original"):
                continue
            if flt == "已复原" and src != "original":
                continue
            if flt == "未分类" and cats:
                continue
            if flt == "机翻" and src != "machine":
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
            rows.append(ui_widgets.TableRow(
                appid=g.appid,
                zh=zh,
                orig=str(rec.get("orig_name") or g.name),
                categories=cats,
                source=SOURCE_LABEL.get(src, src or "待翻译"),
                status=status,
                installed=g.installed,
                raw=g,
            ))
        if self.sort_key:
            def key(row: ui_widgets.TableRow):
                if self.sort_key == "categories":
                    return " / ".join(row.categories).lower()
                if self.sort_key == "game":
                    return (row.zh + " " + row.orig).lower()
                return str(getattr(row, self.sort_key, "")).lower()
            rows.sort(key=key, reverse=self.sort_desc)
        self.table.sort_key = self.sort_key
        self.table.sort_desc = self.sort_desc
        self.table.set_rows(rows)
        self.count_var.set(
            f"显示 {len(rows)} / {len(self.ctx.games)} · 已应用 "
            f"{sum(1 for r in self.ctx.state.apps.values() if r.get('applied'))}"
        )
        self._update_selection_label()

    def on_sort(self, column: str) -> None:
        if column == self.sort_key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key = column
            self.sort_desc = True
        self.refresh_table()

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
        menu = tk.Menu(self.root, tearoff=0, bg=PANEL2, fg=FG,
                       activebackground="#2a475e", activeforeground=FG)
        menu.add_command(label="编辑名称", command=lambda: self._open_name(appid))
        menu.add_command(label="编辑分类", command=lambda: self._open_category(appid))
        menu.add_separator()
        menu.add_command(label="批量追加分类…", command=lambda: self.on_batch("append"))
        menu.add_command(label="批量替换分类…", command=lambda: self.on_batch("replace"))
        menu.add_command(label="批量移除分类…", command=lambda: self.on_batch("remove"))
        menu.add_separator()
        menu.add_command(label="重新自动分类选中", command=self.on_auto_selected)
        menu.add_command(label="清除选中分类", command=self.on_clear_selected)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_name(self, appid: int) -> None:
        g = self.games_by_id.get(appid)
        if g:
            NameEditDialog(self, g, self.ctx.state.get_app(appid))

    def _open_category(self, appid: int) -> None:
        g = self.games_by_id.get(appid)
        if g:
            CategoryPickerDialog(self, g, self.ctx.state.get_app(appid))

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
            if not str(self.ctx.state.get_app(g.appid).get("zh_name") or "").strip()
        ]
        new_count = len(self.ctx.new_appids)
        if not missing:
            if new_count:
                self._append_log(f"新增 {new_count} 个游戏，已由官方名/内置词典覆盖")
            return
        self._append_log(
            f"发现 {new_count} 个新增、{len(missing)} 个待翻译游戏，开始自动联网翻译..."
        )
        proxy = str(self.settings.get("proxy") or "").strip() or None
        self.cancel_flag = False

        def work():
            return service.auto_translate(
                self.ctx, use_network=True, proxy=proxy, games=missing,
                progress=lambda i, n, name: self.bus.put(
                    ("progress", (i, n, f"自动翻译 {i}/{n}: {name}"))
                ),
                should_stop=lambda: self.cancel_flag,
            )

        self.run_task("auto_new_translate", work)

    def on_translate(self) -> None:
        if not self.ctx or self.busy:
            return
        if not messagebox.askyesno(
            "自动翻译",
            f"将为 {len(self.ctx.games)} 个条目补齐中文名。\n"
            "离线词典/官方名优先；联网源可能较慢。是否继续？",
            parent=self.root,
        ):
            return
        use_network = bool(self.settings.get("use_network", True))
        proxy = str(self.settings.get("proxy") or "").strip() or None
        self.cancel_flag = False
        self._append_log(f"开始翻译（联网：{'是' if use_network else '否'}）...")

        def work():
            service.reset_undo_names(
                self.ctx, log=lambda m: self.bus.put(("log", m))
            )
            return service.auto_translate(
                self.ctx, use_network=use_network, proxy=proxy,
                progress=lambda i, n, name: self.bus.put(
                    ("progress", (i, n, f"翻译 {i}/{n}: {name}"))
                ),
                should_stop=lambda: self.cancel_flag,
            )

        self.run_task("translate", work)

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
        if not self.ctx:
            return
        if not bool(self.settings.get("use_network", True)):
            messagebox.showinfo("提示", "设置中未启用联网。", parent=self.root)
            return
        missing = [g.appid for g in self.ctx.games if not getattr(g, "description", "")]
        if not missing:
            messagebox.showinfo("提示", "当前显示的游戏都已有简介缓存。", parent=self.root)
            return
        if not messagebox.askyesno(
            "补全简介",
            f"将为 {len(missing)} 个游戏尝试获取 Steam 商店简介。\n"
            "Steam 商店不可达时会跳过；每条约 0.7 秒，可能需要几分钟。\n"
            "继续？",
            parent=self.root,
        ):
            return
        proxy = str(self.settings.get("proxy") or "").strip() or None

        def work():
            cache = descriptions.fetch_many(missing, proxy=proxy)
            for g in self.ctx.games:
                text = cache.get(str(g.appid))
                if text:
                    g.description = text
            return len(cache)

        self.run_task("fetch_desc", work)

    def on_apply(self, selected: bool) -> None:
        if not self.ctx:
            return
        appids = self.selected_appids() if selected else None
        if selected and not appids:
            messagebox.showinfo("提示", "先在表格中勾选/选中要应用的游戏。", parent=self.root)
            return
        targets = [
            g for g in self.ctx.games
            if (appids is None or g.appid in appids)
            and str(self.ctx.state.get_app(g.appid).get("zh_name") or "").strip()
        ]
        if not targets:
            messagebox.showinfo("提示", "没有可应用的中文名。", parent=self.root)
            return
        if steamenv.steam_running():
            text = (
                f"将写入 {len(targets)} 个中文名和分类。\n\n"
                "Steam 正在运行，程序会先关闭 Steam 再写入，然后重新启动。\n"
                "如果有游戏正在运行，请先退出游戏。\n\n是否继续？"
            )
        else:
            text = f"将写入 {len(targets)} 个中文名和分类。\n\n是否继续？"
        if not messagebox.askyesno("应用更改", text, parent=self.root):
            return
        restart = bool(self.settings.get("restart_steam", True))
        lock = bool(self.settings.get("lock_appinfo", False))

        def work():
            res = service.do_apply(
                self.ctx, selected=appids, restart=restart, lock_appinfo=lock,
                progress=lambda m: self.bus.put(("progress", (0, 0, m))),
                log=lambda m: self.bus.put(("log", m)),
            )
            ver = None
            if restart:
                self.bus.put(("log", "等待 Steam 启动后校验（约 12 秒）..."))
                ver = service.do_verify(self.ctx, wait=12, log=lambda m: self.bus.put(("log", m)))
            return res, ver

        self.run_task("apply", work)

    def on_undo_all(self) -> None:
        if not self.ctx:
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
            "一键复原（后悔药）",
            text,
            parent=self.root,
            icon="warning",
            default="no",
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
        if not self.ctx:
            return
        if not messagebox.askyesno(
            "一键修复",
            "将关闭 Steam（如正在运行），重新写入中文名，然后启动 Steam。\n是否继续？",
            parent=self.root,
        ):
            return
        restart = bool(self.settings.get("restart_steam", True))
        lock = bool(self.settings.get("lock_appinfo", False))

        def work():
            res = service.do_autofix(
                self.ctx, restart=restart, lock_appinfo=lock,
                log=lambda m: self.bus.put(("log", m)),
            )
            ver = service.do_verify(self.ctx, wait=12, log=lambda m: self.bus.put(("log", m))) if restart else None
            return res, ver

        self.run_task("autofix", work)

    def on_restore(self) -> None:
        if not self.ctx:
            return
        backups = service.latest_backups(10)
        if not backups:
            messagebox.showinfo("提示", "还没有备份。", parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("选择备份")
        win.configure(bg=BG)
        win.transient(self.root)
        win.grab_set()
        tk.Label(win, text="选择要还原的备份（会关闭 Steam）", bg=BG, fg=FG).pack(padx=12, pady=8)
        lb = tk.Listbox(win, width=60, height=10, bg=PANEL2, fg=FG, relief="flat")
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
                f"将用备份 {b.name} 覆盖当前的 appinfo.vdf 和收藏集。\n"
                "会关闭 Steam。是否继续？",
                parent=self.root,
            ):
                return

            def work():
                service.do_restore(self.ctx, b, restart=restart,
                                   log=lambda m: self.bus.put(("log", m)))
                return str(b)

            self.run_task("restore", work)

        ttk.Button(win, text="还原", style="Accent.TButton", command=do_restore).pack(
            side="right", padx=12, pady=(0, 12)
        )
        ttk.Button(win, text="取消", command=win.destroy).pack(side="right", pady=(0, 12))

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
        self._append_log("已请求取消（当前网络请求完成后停止）")

    def update_status(self) -> None:
        if not self.ctx:
            self.status_var.set("未加载")
            self.header_status.set("未加载")
            return
        c = service.counts(self.ctx)
        running = steamenv.steam_running()
        cache = "分片缓存" if self.ctx.env.has_shards else "单文件缓存"
        text = (
            f"{'Steam 运行中' if running else 'Steam 已关闭'} · "
            f"{self.ctx.env.persona or self.ctx.env.account} · {cache} · "
            f"共 {c['total']} · 已应用 {c['applied']} · 待翻译 {c['missing']}"
        )
        self.status_var.set(text)
        self.header_status.set(
            f"{self.ctx.env.persona or self.ctx.env.account} · "
            f"{'Steam 运行中' if running else 'Steam 已关闭'}"
        )


def main() -> None:
    from . import paths

    paths.ensure_data()
    root = tk.Tk()
    SteamZhApp(root)
    root.mainloop()
