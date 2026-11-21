"""Dialogs for Steam Unify (name editor, category picker, apply options, tabbed settings)."""

from __future__ import annotations

import os
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import (
    apply,
    category_model,
    category_state,
    dictionary,
    scoring,
    service,
    steamenv,
    translate,
    ui_widgets,
)
from .state import DEFAULT_SOURCE_ORDER, State

APP_TITLE = "Steam 统译 (Steam Unify)"
BG = ui_widgets.BG
PANEL = ui_widgets.PANEL
PANEL2 = ui_widgets.PANEL2
FG = ui_widgets.FG
MUTED = ui_widgets.MUTED
ACCENT = ui_widgets.ACCENT
GREEN = "#7bd88f"
ORANGE = "#f5b041"
RED = "#e05a47"
CATEGORY_COLORS = ui_widgets.CATEGORY_COLORS

SOURCE_LABEL = {
    "official": "官方",
    "dictionary": "词典",
    "wikidata": "维基",
    "steam": "商店",
    "llm": "AI",
    "machine": "机翻",
    "manual": "手动",
    "already": "已中文",
    "original": "原文",
    "": "待翻译",
}

STATUS_FILTERS = [
    "全部",
    "已翻译",
    "待翻译",
    "需核对",
    "未分类",
    "已复原",
    "官方/词典",
    "维基",
    "AI",
    "机翻",
    "手动",
    "已应用",
    "已安装",
    "已拥有",
    "已忽略",
]


def _center_over(win: tk.Toplevel, parent: tk.Misc) -> None:
    win.update_idletasks()
    try:
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        ww = win.winfo_width()
        wh = win.winfo_height()
        x = max(0, px + (pw - ww) // 2)
        y = max(0, py + (ph - wh) // 2)
        win.geometry(f"+{x}+{y}")
    except Exception:
        pass


class NameEditDialog(tk.Toplevel):
    def __init__(self, app, game, record):
        super().__init__(app.root)
        self.app = app
        self.game = game
        self.record = record
        orig_name = str(record.get("orig_name") or game.name)
        self.title(f"编辑名称 - {orig_name}")
        self.configure(bg=BG)
        self.resizable(True, False)
        self.minsize(560, 320)
        self.transient(app.root)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())

        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        tk.Label(
            head, text=orig_name, bg=PANEL, fg=FG,
            font=("Microsoft YaHei UI", 12, "bold"), wraplength=540, justify="left",
        ).pack(anchor="w", padx=14, pady=(10, 2))
        meta_row = tk.Frame(head, bg=PANEL)
        meta_row.pack(fill="x", padx=14, pady=(0, 10))
        tk.Label(
            meta_row,
            text=f"AppID {game.appid}  ·  类型 {game.type}"
            + (f"  ·  标签：{' / '.join(game.tag_names[:4])}" if game.tag_names else ""),
            bg=PANEL, fg=MUTED, font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        ttk.Button(
            meta_row, text="SteamDB", style="Small.TButton",
            command=lambda: webbrowser.open(f"https://steamdb.info/app/{game.appid}/"),
        ).pack(side="right", padx=(4, 0))
        ttk.Button(
            meta_row, text="Steam 商店", style="Small.TButton",
            command=lambda: webbrowser.open(f"https://store.steampowered.com/app/{game.appid}/"),
        ).pack(side="right", padx=(4, 0))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=10)

        input_row = tk.Frame(body, bg=BG)
        input_row.pack(fill="x", pady=(0, 6))
        tk.Label(input_row, text="中文名", bg=BG, fg=FG, width=7, anchor="w").pack(side="left")
        self.name_var = tk.StringVar(value=str(record.get("zh_name") or ""))
        entry = tk.Entry(
            input_row, textvariable=self.name_var, bg=PANEL2, fg=FG,
            insertbackground=FG, relief="flat", font=("Microsoft YaHei UI", 10),
        )
        entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(4, 6))
        entry.bind("<Return>", lambda _e: self.save())
        entry.focus_set()
        entry.select_range(0, "end")
        ttk.Button(
            input_row, text="填入原名", style="Small.TButton",
            command=lambda: self.name_var.set(orig_name),
        ).pack(side="left")

        opt_row = tk.Frame(body, bg=BG)
        opt_row.pack(fill="x", pady=(2, 6))
        self.lock_var = tk.BooleanVar(value=True if not record.get("zh_name") else bool(record.get("locked", True)))
        tk.Checkbutton(
            opt_row, text="锁定中文名（自动翻译不覆盖）", variable=self.lock_var,
            bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
        ).pack(side="left")
        self.save_dict_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            opt_row, text="同步保存到用户词典", variable=self.save_dict_var,
            bg=BG, fg=MUTED, selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
        ).pack(side="left", padx=(12, 0))

        src = str(record.get("source") or "")
        info = f"当前来源：{SOURCE_LABEL.get(src, src or '待翻译')}"
        if record.get("detail"):
            info += f"  ·  {record['detail']}"
        tk.Label(body, text=info, bg=BG, fg=MUTED, anchor="w").pack(fill="x", pady=(0, 4))
        if record.get("error"):
            tk.Label(body, text=record["error"], bg=BG, fg=ORANGE, anchor="w").pack(fill="x", pady=(0, 4))

        # Live candidate lookup section
        cand_head = tk.Frame(body, bg=BG)
        cand_head.pack(fill="x", pady=(6, 4))
        tk.Label(cand_head, text="候选译名（点击填入）", bg=BG, fg=FG, font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        self.lookup_btn = ttk.Button(
            cand_head, text="联网查候选", style="Small.TButton", command=self._lookup
        )
        self.lookup_btn.pack(side="right")

        self.cand_frame = tk.Frame(body, bg=PANEL, padx=8, pady=6)
        self.cand_frame.pack(fill="x", pady=(0, 6))
        self.cand_hint = tk.Label(
            self.cand_frame, text="点击右上角“联网查候选”可并行检索维基百科、Steam 商店、AI 与机翻结果",
            bg=PANEL, fg=MUTED, font=("Microsoft YaHei UI", 8), anchor="w", justify="left", wraplength=520,
        )
        self.cand_hint.pack(anchor="w")

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=14, pady=(4, 12))
        ttk.Button(btns, text="取消 (Esc)", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="保存 (Enter)", style="Accent.TButton", command=self.save).pack(side="right")
        _center_over(self, app.root)

    def _lookup(self) -> None:
        self.lookup_btn.configure(state="disabled", text="查询中…")
        for w in self.cand_frame.winfo_children():
            w.destroy()
        tk.Label(self.cand_frame, text="正在并行查询多个译名源…", bg=PANEL, fg=ACCENT).pack(anchor="w")

        settings = dict(self.app.settings)

        def work():
            cands = translate.lookup_candidates(self.game, settings)
            try:
                self.after(0, lambda: self._show_candidates(cands))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _show_candidates(self, cands: list[translate.Translation]) -> None:
        if not self.winfo_exists():
            return
        self.lookup_btn.configure(state="normal", text="联网查候选")
        for w in self.cand_frame.winfo_children():
            w.destroy()
        if not cands:
            tk.Label(
                self.cand_frame, text="未查到中文候选译名（可检查代理或启用 AI 翻译）",
                bg=PANEL, fg=ORANGE,
            ).pack(anchor="w")
            return
        for tr in cands[:6]:
            row = tk.Frame(self.cand_frame, bg=PANEL)
            row.pack(fill="x", pady=2)
            tag = SOURCE_LABEL.get(tr.source, tr.source)
            tk.Label(
                row, text=f"[{tag}]", bg=PANEL, fg=ACCENT, width=6, anchor="w",
                font=("Microsoft YaHei UI", 8, "bold"),
            ).pack(side="left")
            tk.Label(
                row, text=tr.name, bg=PANEL, fg=FG, anchor="w",
                font=("Microsoft YaHei UI", 9),
            ).pack(side="left", fill="x", expand=True, padx=(2, 6))
            ttk.Button(
                row, text="使用", style="Small.TButton",
                command=lambda n=tr.name: self.name_var.set(n),
            ).pack(side="right")

    def save(self) -> None:
        rec = self.record
        name = self.name_var.get().strip()
        old_name = str(rec.get("zh_name") or "")
        rec["zh_name"] = name
        if name and name != old_name:
            rec["source"] = "manual"
            rec["detail"] = "手动修改"
            rec["translated_at"] = int(time.time())
            if self.save_dict_var.get():
                user_dict = dictionary.load_user()
                user_dict[str(self.game.appid)] = name
                dictionary.save_user(user_dict)
        elif not name:
            rec["source"] = ""
            rec["detail"] = ""
        rec["locked"] = bool(self.lock_var.get()) if name else False
        rec["error"] = ""
        if self.app.ctx:
            self.app.ctx.state.save()
        self.app.refresh_table()
        self.app.refresh_sidebar()
        self.app.update_status()
        self.app.log(f"已修改 {self.game.name} → {name or '(空)'}")
        self.destroy()


class CategoryPickerDialog(tk.Toplevel):
    def __init__(self, app, game, record):
        super().__init__(app.root)
        self.app = app
        self.game = game
        self.record = record
        self.categories = category_model.category_names()
        self.selected = category_state.get_categories(record)
        self.vars: dict[str, tk.BooleanVar] = {}
        self.check_buttons: list[tuple[str, tk.Checkbutton]] = []
        self.title(f"分类 - {record.get('orig_name') or game.name}")
        self.configure(bg=BG)
        self.geometry("640x660")
        self.minsize(540, 520)
        self.transient(app.root)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())

        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        tk.Label(
            head, text=record.get("orig_name") or game.name, bg=PANEL, fg=FG,
            font=("Microsoft YaHei UI", 12, "bold"), anchor="w",
        ).pack(side="left", padx=14, pady=10)
        tk.Label(
            head, text="最多 3 个分类，第一个为主分类", bg=PANEL, fg=MUTED,
        ).pack(side="right", padx=14)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=10)
        tk.Label(body, text="已选（可上下调整顺序）", bg=BG, fg=FG).pack(anchor="w")
        sel_row = tk.Frame(body, bg=BG)
        sel_row.pack(fill="x", pady=(4, 8))
        self.sel_list = tk.Listbox(
            sel_row, height=4, bg=PANEL2, fg=FG, relief="flat",
            selectbackground="#284c6b", activestyle="none", exportselection=False,
        )
        self.sel_list.pack(side="left", fill="x", expand=True)
        sel_btns = tk.Frame(sel_row, bg=BG)
        sel_btns.pack(side="left", padx=(8, 0))
        ttk.Button(sel_btns, text="↑", width=3, command=lambda: self._move(-1)).pack(pady=1)
        ttk.Button(sel_btns, text="↓", width=3, command=lambda: self._move(1)).pack(pady=1)
        ttk.Button(sel_btns, text="移除", width=5, command=self._remove_sel).pack(pady=1)

        tk.Label(body, text="搜索分类", bg=BG, fg=MUTED).pack(anchor="w", pady=(4, 0))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._filter_checks())
        tk.Entry(
            body, textvariable=self.search_var, bg=PANEL2, fg=FG,
            insertbackground=FG, relief="flat",
        ).pack(fill="x", pady=(3, 8), ipady=4)

        checks = tk.Frame(body, bg=BG)
        checks.pack(fill="both", expand=True)
        for i, cat in enumerate(self.categories):
            var = tk.BooleanVar(value=cat in self.selected)
            self.vars[cat] = var
            cb = tk.Checkbutton(
                checks, text=cat, variable=var, command=lambda c=cat: self._on_check(c),
                bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG,
                activeforeground=FG, anchor="w",
            )
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=6, pady=2)
            self.check_buttons.append((cat, cb))

        rec_row = tk.Frame(body, bg=BG)
        rec_row.pack(fill="x", pady=(8, 4))
        self.rec_var = tk.StringVar(value="正在计算推荐 ...")
        tk.Label(
            rec_row, textvariable=self.rec_var, bg=BG, fg=ACCENT,
            wraplength=500, justify="left",
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(rec_row, text="填入推荐", command=self._use_recommendation).pack(side="right")

        self.lock_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            body, text="锁定分类（自动分类不覆盖）", variable=self.lock_var,
            bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
        ).pack(anchor="w", pady=(4, 0))

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="保存", style="Accent.TButton", command=self.save).pack(
            side="right", padx=4
        )
        self._refresh_sel()
        self._recommend()
        _center_over(self, app.root)

    def _refresh_sel(self) -> None:
        self.sel_list.delete(0, "end")
        for i, cat in enumerate(self.selected[:3]):
            self.sel_list.insert("end", f"{i + 1}. {cat}")
        for cat, var in self.vars.items():
            var.set(cat in self.selected)

    def _on_check(self, cat: str) -> None:
        if self.vars[cat].get():
            if cat not in self.selected:
                if len(self.selected) >= 3:
                    self.vars[cat].set(False)
                    messagebox.showinfo("最多 3 个", "每个游戏最多 3 个分类。", parent=self)
                    return
                self.selected.append(cat)
        else:
            if cat in self.selected:
                self.selected.remove(cat)
        self._refresh_sel()

    def _remove_sel(self) -> None:
        sel = self.sel_list.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.selected):
            self.selected.pop(idx)
            self._refresh_sel()

    def _move(self, delta: int) -> None:
        sel = self.sel_list.curselection()
        if not sel:
            return
        i = int(sel[0])
        j = i + delta
        if 0 <= j < len(self.selected):
            self.selected[i], self.selected[j] = self.selected[j], self.selected[i]
            self._refresh_sel()
            self.sel_list.selection_set(j)

    def _filter_checks(self) -> None:
        q = self.search_var.get().strip().lower()
        for cat, cb in self.check_buttons:
            if not q or q in cat.lower():
                cb.grid()
            else:
                cb.grid_remove()

    def _recommend(self) -> None:
        try:
            res = scoring.classify(
                self.game,
                description=getattr(self.game, "description", "") or "",
                extra_names=[str(self.record.get("orig_name") or "")],
            )
        except Exception as exc:
            self.rec_var.set(f"推荐失败：{exc}")
            return
        self.recommendation = res
        bits = [f"{cat}({res.scores.get(cat, 0):.1f})" for cat in res.categories]
        reasons = "；".join(
            f"{c}: {'/'.join(r[:2])}" for c, r in list(res.reasons.items())[:3]
        )
        self.rec_var.set("推荐：" + "、".join(bits) + ("\n" + reasons if reasons else ""))

    def _use_recommendation(self) -> None:
        res = getattr(self, "recommendation", None)
        if not res:
            return
        self.selected = list(res.categories[:3])
        self._refresh_sel()

    def save(self) -> None:
        cats = category_state.normalize_categories(self.selected)
        category_state.set_categories(
            self.record, cats, source="manual", locked=bool(self.lock_var.get())
        )
        if self.app.ctx:
            self.app.ctx.state.save()
        self.app.refresh_table()
        self.app.refresh_sidebar()
        self.app.log(f"已设置 {self.game.name} 分类：{' / '.join(cats) or '未分类'}")
        self.destroy()


class BatchCategoryDialog(tk.Toplevel):
    def __init__(self, app, appids: set[int], mode: str = "append"):
        super().__init__(app.root)
        self.app = app
        self.appids = sorted(appids)
        self.categories = category_model.category_names()
        self.targets: list[str] = []
        self.vars: dict[str, tk.BooleanVar] = {}
        self.title(f"批量分类 - 已选 {len(self.appids)} 个游戏")
        self.configure(bg=BG)
        self.geometry("680x620")
        self.minsize(540, 480)
        self.transient(app.root)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())

        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        tk.Label(
            head, text=f"批量分类 · {len(self.appids)} 个游戏", bg=PANEL, fg=FG,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left", padx=14, pady=10)
        tk.Label(head, text="最多 3 个目标分类", bg=PANEL, fg=MUTED).pack(
            side="right", padx=14
        )

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=10)
        mode_row = tk.Frame(body, bg=BG)
        mode_row.pack(fill="x", pady=(0, 8))
        tk.Label(mode_row, text="模式", bg=BG, fg=FG).pack(side="left")
        self.mode_var = tk.StringVar(value=mode)
        for value, label in (("append", "追加"), ("replace", "替换"), ("remove", "移除")):
            tk.Radiobutton(
                mode_row, text=label, value=value, variable=self.mode_var,
                bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG,
                activeforeground=FG, command=self._refresh,
            ).pack(side="left", padx=(10, 2))

        tk.Label(body, text="目标分类", bg=BG, fg=FG).pack(anchor="w")
        checks = tk.Frame(body, bg=BG)
        checks.pack(fill="x", pady=(4, 8))
        for i, cat in enumerate(self.categories):
            var = tk.BooleanVar(value=False)
            self.vars[cat] = var
            tk.Checkbutton(
                checks, text=cat, variable=var, command=lambda c=cat: self._on_check(c),
                bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG,
                activeforeground=FG, anchor="w",
            ).grid(row=i // 4, column=i % 4, sticky="w", padx=4, pady=2)

        tk.Label(body, text="预览", bg=BG, fg=FG).pack(anchor="w", pady=(6, 0))
        self.preview_var = tk.StringVar(value="")
        tk.Label(
            body, textvariable=self.preview_var, bg=PANEL, fg=MUTED,
            justify="left", anchor="nw", wraplength=620, padx=10, pady=8,
        ).pack(fill="x", pady=4)

        self.trunc_var = tk.StringVar(value="")
        tk.Label(
            body, textvariable=self.trunc_var, bg=BG, fg=ORANGE,
            justify="left", anchor="nw", wraplength=620,
        ).pack(fill="x")

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="应用批量分类", style="Accent.TButton",
                   command=self.apply).pack(side="right", padx=4)
        self._refresh()
        _center_over(self, app.root)

    def _on_check(self, cat: str) -> None:
        if self.vars[cat].get():
            if len(self.targets) >= 3:
                self.vars[cat].set(False)
                messagebox.showinfo("最多 3 个", "目标分类最多选择 3 个。", parent=self)
                return
            if cat not in self.targets:
                self.targets.append(cat)
        else:
            if cat in self.targets:
                self.targets.remove(cat)
        self._refresh()

    def _refresh(self) -> None:
        mode = self.mode_var.get()
        if mode != "remove" and not self.targets:
            self.preview_var.set("请选择 1-3 个目标分类。")
            self.trunc_var.set("")
            return
        try:
            preview = service.batch_preview(self.app.ctx, self.appids, self.targets, mode)
        except Exception as exc:
            self.preview_var.set(f"无法预览：{exc}")
            self.trunc_var.set("")
            return
        mode_label = {
            "append": "追加（保留原分类）",
            "replace": "替换（只保留目标）",
            "remove": "移除目标分类",
        }.get(mode, mode)
        self.preview_var.set(
            f"选中 {len(self.appids)} 个；将修改 {len(preview.changed)} 个；"
            f"超限截断 {len(preview.truncated)} 个；其中原本锁定 {len(preview.locked)} 个。\n"
            f"模式：{mode_label}"
        )
        if preview.truncated:
            lines = [
                f"{self.app.games_by_id[p.appid].name}: 丢弃 {' / '.join(p.dropped)}"
                for p in preview.truncated[:8]
                if p.appid in self.app.games_by_id
            ]
            self.trunc_var.set("将丢弃的分类：\n" + "\n".join(lines))
        else:
            self.trunc_var.set("")

    def apply(self) -> None:
        mode = self.mode_var.get()
        if mode != "remove" and not self.targets:
            messagebox.showinfo("提示", "请先选择目标分类。", parent=self)
            return
        try:
            preview = service.batch_preview(self.app.ctx, self.appids, self.targets, mode)
        except Exception as exc:
            messagebox.showerror("批量分类", str(exc), parent=self)
            return
        if not preview.changed:
            messagebox.showinfo("提示", "没有需要修改的游戏。", parent=self)
            return
        if not messagebox.askyesno(
            "确认批量分类",
            f"将修改 {len(preview.changed)} 个游戏（共选中 {len(self.appids)} 个）。\n"
            + (f"有 {len(preview.truncated)} 个游戏超过 3 个分类，将截断。\n" if preview.truncated else "")
            + "继续？",
            parent=self,
        ):
            return
        snapshot = service.batch_apply(self.app.ctx, preview)
        self.app.last_batch_snapshot = snapshot
        self.app._update_undo_button()
        self.app.refresh_table()
        self.app.refresh_sidebar()
        self.app.log(
            f"批量分类完成：修改 {len(preview.changed)} 个，截断 {len(preview.truncated)} 个"
        )
        self.destroy()


class ApplyDialog(tk.Toplevel):
    """Explicit confirmation dialog letting user choose names vs collections & scope."""

    def __init__(
        self,
        app,
        *,
        all_count: int,
        selected_ids: set[int],
        default_selected: bool = False,
        default_collections: bool | None = None,
        on_confirm,
    ):
        super().__init__(app.root)
        self.app = app
        self.selected_ids = set(selected_ids)
        self.on_confirm = on_confirm
        self.title("应用更改到 Steam")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app.root)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())

        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        tk.Label(
            head, text="应用更改到 Steam 本地缓存", bg=PANEL, fg=FG,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", padx=14, pady=10)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        self.scope_var = tk.StringVar(
            value="selected" if (default_selected and selected_ids) else "all"
        )
        tk.Label(body, text="应用范围", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        tk.Radiobutton(
            body, text=f"全部有效游戏（共 {all_count} 个已翻译，自动排除已忽略条目）",
            value="all", variable=self.scope_var,
            bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
        ).pack(anchor="w", pady=(2, 0))
        sel_rb = tk.Radiobutton(
            body, text=f"仅当前勾选/选中游戏（已选 {len(selected_ids)} 个）",
            value="selected", variable=self.scope_var,
            bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
            state="normal" if selected_ids else "disabled",
        )
        sel_rb.pack(anchor="w", pady=(0, 8))

        tk.Label(body, text="写入内容", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        self.names_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            body, text="写入中文游戏名到 Steam appinfo 缓存与 ACF",
            variable=self.names_var,
            bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
        ).pack(anchor="w", pady=(2, 0))

        use_col = (
            bool(default_collections)
            if default_collections is not None
            else bool(app.settings.get("apply_collections", True))
        )
        self.cols_var = tk.BooleanVar(value=use_col)
        tk.Checkbutton(
            body, text="同步多标签分类到 Steam 收藏集（取消勾选则不改动收藏集）",
            variable=self.cols_var,
            bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
        ).pack(anchor="w")

        self.restart_var = tk.BooleanVar(value=bool(app.settings.get("restart_steam", True)))
        tk.Checkbutton(
            body, text="写入完成后自动重启 Steam 并校验",
            variable=self.restart_var,
            bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
        ).pack(anchor="w", pady=(4, 8))

        if steamenv.steam_running():
            tk.Label(
                body,
                text="⚠ 检测到 Steam 正在运行：应用前会自动备份并关闭 Steam，请先保存并退出正在运行的游戏。",
                bg="#2d2417", fg=ORANGE, wraplength=440, justify="left", padx=10, pady=8,
            ).pack(fill="x", pady=(2, 4))
        else:
            tk.Label(
                body, text="写入前将自动备份到 backups/ 目录，失败可自动回滚。",
                bg=PANEL, fg=MUTED, wraplength=440, justify="left", padx=10, pady=6,
            ).pack(fill="x", pady=(2, 4))

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=16, pady=(6, 12))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="开始写入", style="Accent.TButton", command=self._confirm).pack(side="right")
        _center_over(self, app.root)

    def _confirm(self) -> None:
        do_names = bool(self.names_var.get())
        do_cols = bool(self.cols_var.get())
        if not do_names and not do_cols:
            messagebox.showinfo("提示", "请至少勾选一项写入内容。", parent=self)
            return
        selected = self.selected_ids if self.scope_var.get() == "selected" else None
        restart = bool(self.restart_var.get())
        self.destroy()
        self.on_confirm(
            selected=selected,
            apply_names=do_names,
            apply_collections=do_cols,
            restart=restart,
        )


class SettingsDialog(tk.Toplevel):
    """Tabbed settings dialog covering Steam/Filtering, Translation Sources, and LLM API."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("设置")
        self.configure(bg=BG)
        self.geometry("710x620")
        self.minsize(640, 540)
        self.transient(app.root)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())

        s = app.settings
        self.vars: dict[str, tk.Variable] = {}
        opts = translate.TranslateOptions.from_settings(s)
        self.source_order: list[str] = list(opts.source_order)
        self.disabled_sources: set[str] = set(opts.disabled_sources)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=(10, 6))

        tab_steam = tk.Frame(nb, bg=BG, padx=14, pady=12)
        tab_trans = tk.Frame(nb, bg=BG, padx=14, pady=12)
        tab_llm = tk.Frame(nb, bg=BG, padx=14, pady=12)
        nb.add(tab_steam, text="  Steam 与过滤  ")
        nb.add(tab_trans, text="  翻译源与网络  ")
        nb.add(tab_llm, text="  AI 大模型翻译  ")

        self._build_steam_tab(tab_steam, s)
        self._build_trans_tab(tab_trans, s)
        self._build_llm_tab(tab_llm, s)

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=12, pady=(4, 12))
        ttk.Button(btns, text="打开备份目录", command=self.open_backups).pack(side="left")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="保存设置", style="Accent.TButton", command=self.save).pack(side="right")
        _center_over(self, app.root)

    # -------------------------------------------------------- Tab 1: Steam
    def _build_steam_tab(self, parent: tk.Frame, s: dict) -> None:
        pad = {"pady": 4}
        row0 = tk.Frame(parent, bg=BG)
        row0.pack(fill="x", **pad)
        tk.Label(row0, text="Steam 目录", bg=BG, fg=FG, width=14, anchor="w").pack(side="left")
        self.vars["steam_root"] = tk.StringVar(value=str(s.get("steam_root") or ""))
        tk.Entry(
            row0, textvariable=self.vars["steam_root"], bg=PANEL2, fg=FG,
            insertbackground=FG, relief="flat",
        ).pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
        ttk.Button(row0, text="浏览…", style="Small.TButton", command=self._browse_steam).pack(side="left")

        row1 = tk.Frame(parent, bg=BG)
        row1.pack(fill="x", **pad)
        tk.Label(row1, text="Steam 账号", bg=BG, fg=FG, width=14, anchor="w").pack(side="left")
        self._account_map: dict[str, str] = {"自动检测（默认）": ""}
        account_labels = ["自动检测（默认）"]
        try:
            root_path = steamenv.detect_root(str(s.get("steam_root") or "") or None)
            for acc in steamenv.list_accounts(root_path):
                persona = steamenv.account_persona(root_path, acc)
                label = f"{persona} ({acc})" if persona else acc
                self._account_map[label] = acc
                account_labels.append(label)
        except Exception:
            pass
        cur_acc = str(s.get("account") or "")
        cur_label = next((lbl for lbl, aid in self._account_map.items() if aid == cur_acc), cur_acc or "自动检测（默认）")
        self.vars["account_display"] = tk.StringVar(value=cur_label)
        ttk.Combobox(
            row1, textvariable=self.vars["account_display"], values=account_labels, width=38
        ).pack(side="left", fill="x", expand=True)

        tk.Frame(parent, bg=ui_widgets.BORDER, height=1).pack(fill="x", pady=8)

        checks = [
            ("filter_non_games", "自动过滤系统工具、运行库（Steamworks/Proton）、专属服务器、测试服与原声带"),
            ("include_software", "包含软件与实用工具（如 Wallpaper Engine 等，不含系统运行库）"),
            ("all_known", "扫描 appinfo 中全部已知游戏（含家庭共享/未拥有缓存，数量较多）"),
            ("apply_collections", "应用时默认同步分类到 Steam 收藏集（取消则只汉化游戏名）"),
            ("restart_steam", "应用后自动重启 Steam 并执行校验"),
            ("lock_appinfo", "应用后将旧版单文件 appinfo.vdf 设为只读"),
        ]
        for key, label in checks:
            var = tk.BooleanVar(value=bool(s.get(key, State().settings.get(key, False))))
            self.vars[key] = var
            tk.Checkbutton(
                parent, text=label, variable=var, bg=BG, fg=FG,
                selectcolor=PANEL2, activebackground=BG, activeforeground=FG,
                anchor="w",
            ).pack(fill="x", pady=2)

        row_bk = tk.Frame(parent, bg=BG)
        row_bk.pack(fill="x", pady=(10, 2))
        tk.Label(row_bk, text="备份保留份数", bg=BG, fg=FG, width=14, anchor="w").pack(side="left")
        self.vars["backup_keep"] = tk.IntVar(value=int(s.get("backup_keep", 10)))
        tk.Spinbox(
            row_bk, from_=1, to=50, textvariable=self.vars["backup_keep"], width=6,
            bg=PANEL2, fg=FG, relief="flat",
        ).pack(side="left")

    def _browse_steam(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="选择 Steam 安装目录（包含 steam.exe）")
        if folder:
            self.vars["steam_root"].set(folder)

    # ---------------------------------------------------- Tab 2: Translate
    def _build_trans_tab(self, parent: tk.Frame, s: dict) -> None:
        row_p = tk.Frame(parent, bg=BG)
        row_p.pack(fill="x", pady=4)
        tk.Label(row_p, text="HTTP 代理", bg=BG, fg=FG, width=14, anchor="w").pack(side="left")
        self.vars["proxy"] = tk.StringVar(value=str(s.get("proxy") or ""))
        tk.Entry(
            row_p, textvariable=self.vars["proxy"], bg=PANEL2, fg=FG,
            insertbackground=FG, relief="flat",
        ).pack(side="left", fill="x", expand=True, ipady=4)
        tk.Label(parent, text="示例：http://127.0.0.1:7890；留空时按下方选项自动使用系统代理或直连",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8), anchor="w").pack(fill="x", padx=(104, 0))

        self.vars["use_system_proxy"] = tk.BooleanVar(value=bool(s.get("use_system_proxy", True)))
        tk.Checkbutton(
            parent, text="代理留空时自动跟随 Windows 系统代理（推荐）",
            variable=self.vars["use_system_proxy"],
            bg=BG, fg=FG, selectcolor=PANEL2, activebackground=BG, activeforeground=FG, anchor="w",
        ).pack(fill="x", pady=(4, 2))

        row_c = tk.Frame(parent, bg=BG)
        row_c.pack(fill="x", pady=4)
        tk.Label(row_c, text="并发线程数", bg=BG, fg=FG, width=14, anchor="w").pack(side="left")
        self.vars["concurrency"] = tk.IntVar(value=int(s.get("concurrency", 6)))
        tk.Spinbox(
            row_c, from_=1, to=12, textvariable=self.vars["concurrency"], width=6,
            bg=PANEL2, fg=FG, relief="flat",
        ).pack(side="left")
        tk.Label(row_c, text="（建议 4–8，过高可能触发商店限流）", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=6)

        tk.Frame(parent, bg=ui_widgets.BORDER, height=1).pack(fill="x", pady=8)

        checks = [
            ("use_network", "允许联网翻译 / 补全商店简介"),
            ("auto_translate_new", "启动扫描时自动联网翻译新增/待翻译游戏"),
        ]
        for key, label in checks:
            var = tk.BooleanVar(value=bool(s.get(key, True)))
            self.vars[key] = var
            tk.Checkbutton(
                parent, text=label, variable=var, bg=BG, fg=FG,
                selectcolor=PANEL2, activebackground=BG, activeforeground=FG, anchor="w",
            ).pack(fill="x", pady=2)

        tk.Label(
            parent,
            text="翻译源优先级与开关（从上到下依次尝试；双击或空格切换启用/停用）：",
            bg=BG, fg=FG, font=("Microsoft YaHei UI", 9, "bold"), anchor="w",
        ).pack(fill="x", pady=(8, 4))

        prio_row = tk.Frame(parent, bg=BG)
        prio_row.pack(fill="both", expand=True, pady=(0, 6))

        self.source_list = tk.Listbox(
            prio_row, height=9, bg=PANEL2, fg=FG, relief="flat",
            selectbackground="#284c6b", activestyle="none", exportselection=False,
            font=("Microsoft YaHei UI", 9),
        )
        self.source_list.pack(side="left", fill="both", expand=True)
        self.source_list.bind("<Double-Button-1>", lambda _e: self._toggle_source())
        self.source_list.bind("<space>", lambda _e: self._toggle_source())

        pbtns = tk.Frame(prio_row, bg=BG)
        pbtns.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(pbtns, text="↑ 上移", width=10, command=lambda: self._move_source(-1)).pack(pady=2)
        ttk.Button(pbtns, text="↓ 下移", width=10, command=lambda: self._move_source(1)).pack(pady=2)
        ttk.Button(pbtns, text="置顶", width=10, command=self._top_source).pack(pady=2)
        ttk.Button(pbtns, text="启用/停用", width=10, command=self._toggle_source).pack(pady=2)
        ttk.Button(pbtns, text="恢复默认", width=10, command=self._reset_sources).pack(pady=(6, 2))

        self._refresh_source_list()

        test_row = tk.Frame(parent, bg=BG)
        test_row.pack(fill="x", pady=(6, 2))
        self.probe_btn = ttk.Button(test_row, text="测试全部翻译源", command=self.test_network)
        self.probe_btn.pack(side="left")
        self.probe_var = tk.StringVar(value="")
        tk.Label(
            test_row, textvariable=self.probe_var, bg=BG, fg=ACCENT,
            font=("Microsoft YaHei UI", 9), wraplength=460, justify="left",
        ).pack(side="left", padx=10)

    def _refresh_source_list(self, select_idx: int | None = None) -> None:
        if not hasattr(self, "source_list"):
            return
        cur_sel = self.source_list.curselection()
        if select_idx is None and cur_sel:
            select_idx = int(cur_sel[0])
        self.source_list.delete(0, "end")
        for idx, sid in enumerate(self.source_order, 1):
            label, desc, _is_net = translate.SOURCE_META[sid]
            enabled = sid not in self.disabled_sources
            mark = "✓" if enabled else "✗"
            self.source_list.insert("end", f"{idx}. [{mark}] {label} — {desc}")
            if not enabled:
                self.source_list.itemconfig(idx - 1, fg=MUTED)
        if select_idx is not None and 0 <= select_idx < len(self.source_order):
            self.source_list.selection_set(select_idx)
            self.source_list.see(select_idx)

    def _move_source(self, delta: int) -> None:
        sel = self.source_list.curselection()
        if not sel:
            return
        i = int(sel[0])
        j = i + delta
        if 0 <= j < len(self.source_order):
            self.source_order[i], self.source_order[j] = self.source_order[j], self.source_order[i]
            self._refresh_source_list(j)

    def _top_source(self) -> None:
        sel = self.source_list.curselection()
        if not sel:
            return
        i = int(sel[0])
        if i > 0:
            sid = self.source_order.pop(i)
            self.source_order.insert(0, sid)
            self._refresh_source_list(0)

    def _toggle_source(self) -> None:
        sel = self.source_list.curselection()
        if not sel:
            return
        i = int(sel[0])
        sid = self.source_order[i]
        if sid in self.disabled_sources:
            self.disabled_sources.remove(sid)
        else:
            self.disabled_sources.add(sid)
        if sid == "llm" and "llm_enabled" in self.vars:
            self.vars["llm_enabled"].set(sid not in self.disabled_sources)
        self._refresh_source_list(i)

    def _reset_sources(self) -> None:
        self.source_order = list(DEFAULT_SOURCE_ORDER)
        self.disabled_sources = set() if ("llm_enabled" in self.vars and self.vars["llm_enabled"].get()) else {"llm"}
        self._refresh_source_list(0)

    def _on_llm_check_toggled(self) -> None:
        if "llm_enabled" not in self.vars:
            return
        if self.vars["llm_enabled"].get():
            self.disabled_sources.discard("llm")
        else:
            self.disabled_sources.add("llm")
        self._refresh_source_list()

    # ---------------------------------------------------------- Tab 3: LLM
    def _build_llm_tab(self, parent: tk.Frame, s: dict) -> None:
        self.vars["llm_enabled"] = tk.BooleanVar(value="llm" not in self.disabled_sources)
        tk.Checkbutton(
            parent, text="启用 OpenAI 兼容大模型批量翻译（支持 DeepSeek / OpenAI / 硅基流动 / 本地 Ollama）",
            variable=self.vars["llm_enabled"], bg=BG, fg=FG,
            selectcolor=PANEL2, activebackground=BG, activeforeground=FG, anchor="w",
            command=self._on_llm_check_toggled,
        ).pack(fill="x", pady=(0, 6))

        presets = tk.Frame(parent, bg=BG)
        presets.pack(fill="x", pady=(0, 8))
        tk.Label(presets, text="快速预设：", bg=BG, fg=MUTED).pack(side="left")
        for label, url, model in (
            ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
            ("硅基流动", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct"),
            ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
            ("本地 Ollama", "http://127.0.0.1:11434/v1", "qwen2.5:7b"),
        ):
            ttk.Button(
                presets, text=label, style="Small.TButton",
                command=lambda u=url, m=model: self._apply_llm_preset(u, m),
            ).pack(side="left", padx=3)

        def add_field(label: str, key: str, default: str, secret: bool = False):
            row = tk.Frame(parent, bg=BG)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label, bg=BG, fg=FG, width=14, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(s.get(key) or default))
            self.vars[key] = var
            ent = tk.Entry(
                row, textvariable=var, bg=PANEL2, fg=FG,
                insertbackground=FG, relief="flat", show="*" if secret else "",
            )
            ent.pack(side="left", fill="x", expand=True, ipady=4)
            if secret:
                shown = tk.BooleanVar(value=False)
                def toggle():
                    shown.set(not shown.get())
                    ent.configure(show="" if shown.get() else "*")
                ttk.Button(row, text="显示", style="Small.TButton", command=toggle).pack(side="left", padx=(6, 0))

        add_field("API Base URL", "llm_base_url", "https://api.deepseek.com/v1")
        add_field("API Key", "llm_api_key", "", secret=True)
        add_field("模型名称", "llm_model", "deepseek-chat")

        row_b = tk.Frame(parent, bg=BG)
        row_b.pack(fill="x", pady=4)
        tk.Label(row_b, text="每批游戏数", bg=BG, fg=FG, width=14, anchor="w").pack(side="left")
        self.vars["llm_batch_size"] = tk.IntVar(value=int(s.get("llm_batch_size", 30)))
        tk.Spinbox(
            row_b, from_=5, to=60, textvariable=self.vars["llm_batch_size"], width=6,
            bg=PANEL2, fg=FG, relief="flat",
        ).pack(side="left")
        tk.Label(row_b, text="（建议 20–40，一次批量翻完速度快、费用极低）",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side="left", padx=6)

        tk.Label(
            parent,
            text="提示：API Key 仅保存在本机 data/settings.json，不会上传到任何第三方；本地 Ollama 可留空 Key。",
            bg=PANEL, fg=MUTED, font=("Microsoft YaHei UI", 8),
            wraplength=600, justify="left", padx=10, pady=6,
        ).pack(fill="x", pady=(8, 8))

        test_row = tk.Frame(parent, bg=BG)
        test_row.pack(fill="x", pady=4)
        self.llm_test_btn = ttk.Button(test_row, text="测试大模型连接", command=self.test_llm)
        self.llm_test_btn.pack(side="left")
        self.llm_test_var = tk.StringVar(value="")
        tk.Label(
            test_row, textvariable=self.llm_test_var, bg=BG, fg=ACCENT,
            font=("Microsoft YaHei UI", 9), wraplength=440, justify="left",
        ).pack(side="left", padx=10)

    def _apply_llm_preset(self, url: str, model: str) -> None:
        self.vars["llm_base_url"].set(url)
        self.vars["llm_model"].set(model)
        self.vars["llm_enabled"].set(True)
        self.disabled_sources.discard("llm")
        self._refresh_source_list()

    # ------------------------------------------------------------ actions
    def open_backups(self) -> None:
        apply.BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(apply.BACKUP_ROOT))
        except Exception as exc:
            messagebox.showerror("错误", str(exc), parent=self)

    def test_network(self) -> None:
        proxy = str(self.vars["proxy"].get() or "").strip() or None
        sys_proxy = bool(self.vars["use_system_proxy"].get())
        self.probe_btn.configure(state="disabled")
        self.probe_var.set("正在并发测试 5 个翻译源…")

        def work():
            res = translate.probe(proxy, use_system_proxy=sys_proxy)
            text = (
                f"Wikidata={'✓' if res.get('wikidata') else '✗'} · "
                f"Steam商店={'✓' if res.get('steam') else '✗'} · "
                f"Google={'✓' if res.get('google') else '✗'} · "
                f"有道={'✓' if res.get('youdao') else '✗'} · "
                f"MyMemory={'✓' if res.get('mymemory') else '✗'}"
            )
            self.app.bus.put(("log", "联网测试：" + text))
            try:
                self.after(0, lambda: (self.probe_var.set(text), self.probe_btn.configure(state="normal")))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def test_llm(self) -> None:
        url = str(self.vars["llm_base_url"].get() or "").strip()
        key = str(self.vars["llm_api_key"].get() or "").strip()
        model = str(self.vars["llm_model"].get() or "").strip()
        proxy = str(self.vars["proxy"].get() or "").strip() or None
        sys_proxy = bool(self.vars["use_system_proxy"].get())
        self.llm_test_btn.configure(state="disabled")
        self.llm_test_var.set("正在测试大模型翻译…")

        def work():
            ok, msg, ms = translate.probe_llm(
                url, key, model, proxy=proxy, use_system_proxy=sys_proxy
            )
            text = f"{'✓ 成功' if ok else '✗ 失败'} ({ms}ms): {msg}"
            self.app.bus.put(("log", "大模型测试：" + text))
            try:
                self.after(0, lambda: (self.llm_test_var.set(text), self.llm_test_btn.configure(state="normal")))
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def save(self) -> None:
        acc_disp = str(self.vars["account_display"].get()).strip()
        account_val = self._account_map.get(acc_disp, acc_disp)
        if account_val == "自动检测（默认）":
            account_val = ""
        llm_on = bool(self.vars["llm_enabled"].get())
        if llm_on:
            self.disabled_sources.discard("llm")
        else:
            self.disabled_sources.add("llm")
        order = translate.normalize_source_order(self.source_order)
        disabled = sorted(s for s in self.disabled_sources if s in translate.SOURCE_META)
        new = {
            "steam_root": str(self.vars["steam_root"].get()).strip(),
            "account": account_val,
            "proxy": str(self.vars["proxy"].get()).strip(),
            "use_system_proxy": bool(self.vars["use_system_proxy"].get()),
            "include_software": bool(self.vars["include_software"].get()),
            "filter_non_games": bool(self.vars["filter_non_games"].get()),
            "all_known": bool(self.vars["all_known"].get()),
            "apply_collections": bool(self.vars["apply_collections"].get()),
            "auto_translate_new": bool(self.vars["auto_translate_new"].get()),
            "use_network": bool(self.vars["use_network"].get()),
            "restart_steam": bool(self.vars["restart_steam"].get()),
            "lock_appinfo": bool(self.vars["lock_appinfo"].get()),
            "backup_keep": max(1, min(50, int(self.vars["backup_keep"].get() or 10))),
            "concurrency": max(1, min(12, int(self.vars["concurrency"].get() or 6))),
            "enable_wikidata": "wikidata" not in self.disabled_sources,
            "enable_steam_store": "steam_store" not in self.disabled_sources,
            "enable_free_mt": any(m not in self.disabled_sources for m in translate.MT_SOURCES),
            "llm_enabled": llm_on,
            "llm_base_url": str(self.vars["llm_base_url"].get()).strip(),
            "llm_api_key": str(self.vars["llm_api_key"].get()).strip(),
            "llm_model": str(self.vars["llm_model"].get()).strip(),
            "llm_batch_size": max(5, min(60, int(self.vars["llm_batch_size"].get() or 30))),
            "source_order": order,
            "disabled_sources": disabled,
        }
        old = dict(self.app.settings)
        self.app.settings.update(new)
        if self.app.ctx:
            self.app.ctx.state.save_settings(self.app.settings)
        else:
            State.load().save_settings(self.app.settings)
        self.destroy()
        need_reload = any(
            old.get(k) != new.get(k)
            for k in ("steam_root", "account", "include_software", "filter_non_games", "all_known")
        )
        if need_reload:
            self.app.reload()
        else:
            self.app.update_status()
