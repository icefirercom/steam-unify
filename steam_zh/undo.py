"""One-click “undo everything” for names and categories.

The original English names/localizations live in state.json (``orig_name`` and
``orig_localized``).  Categories can always be reset to an empty list.  Before
touching Steam this module creates a full safety backup through apply.backup().
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import appinfo, apply, category_state, collections, steamenv, vdftext
from .appinfo_shards import ShardSet
from .state import State


@dataclass
class UndoResult:
    backup: Path | None = None
    names_restored: int = 0
    categories_cleared: int = 0
    collections_deleted: int = 0
    warning: list[str] = field(default_factory=list)
    steam_restarted: bool = False


def undo_all(
    env: steamenv.SteamEnv,
    state: State,
    games: list,
    *,
    restart: bool = True,
    progress: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> UndoResult:
    result = UndoResult()
    apply.preflight_writable(env)
    was_running = steamenv.steam_running()
    if was_running:
        if log:
            log("Steam 正在运行，正在自动关闭 ...")
        steamenv.close_steam(env, log=log)
        time.sleep(2.0)

    result.backup = apply.backup(env, state, steamenv.installed_apps(env.root))
    if log:
        log(f"复原前已备份到 {result.backup}")

    try:
        appids = sorted(int(a) for a in state.apps.keys())
        total = max(1, len(appids))
        ss = ShardSet.load(env.shard_dir) if env.has_shards else None
        ai = appinfo.AppInfo.load(env.appinfo) if not env.has_shards else None
        changed_appinfo = False

        for index, appid in enumerate(appids, 1):
            rec = state.get_app(appid)
            orig = str(rec.get("orig_name") or "").strip()
            zh = str(rec.get("zh_name") or "").strip()
            cats = category_state.get_categories(rec)
            if cats:
                result.categories_cleared += 1

            if orig:
                exists = (appid in ss.by_appid) if ss is not None else bool(ai.entry(appid))
                if exists:
                    current = (ss.get_name(appid) if ss else ai.get_name(appid)) or ""
                    modified = bool(rec.get("applied") or zh or (current and current != orig))
                    if modified:
                        if current != orig:
                            if ss:
                                ss.set_name(appid, orig)
                            else:
                                ai.set_name(appid, orig)
                            changed_appinfo = True
                            result.names_restored += 1
                        mapping = rec.get("orig_localized")
                        if isinstance(mapping, dict) and mapping:
                            if ss:
                                ss.restore_localized(appid, mapping)
                            else:
                                ai.restore_localized(appid, mapping)
                            changed_appinfo = True
                        # State now represents the original English name.  Keeping
                        # zh_name non-empty and locked prevents auto translation from
                        # immediately filling it again.
                        rec["zh_name"] = orig
                        rec["source"] = "original"
                        rec["detail"] = "已一键复原"
                        rec["locked"] = True
                        rec["applied"] = False
                        rec.pop("translated_at", None)
                        rec.pop("applied_at", None)

            category_state.set_categories(rec, [], source="undo", locked=True)
            if progress:
                progress(f"恢复 {index}/{total}")
            if log and index % 40 == 0:
                log(f"恢复进度 {index}/{total}")

        if changed_appinfo:
            def write_appinfo() -> None:
                if ss:
                    ss.save()
                else:
                    data = ai.build()
                    tmp = env.appinfo.with_name(env.appinfo.name + ".tmp")
                    with open(tmp, "wb") as f:
                        f.write(data)
                        f.flush()
                        import os
                        os.fsync(f.fileno())
                    os.replace(tmp, env.appinfo)

            apply._retry_write(write_appinfo)

        # Restore ACF names as well (cosmetic, but keeps library consistent).
        installed = steamenv.installed_apps(env.root)
        for appid in appids:
            rec = state.get_app(appid)
            orig = str(rec.get("orig_name") or "").strip()
            info = installed.get(appid)
            acf = info.get("acf") if info else None
            if not orig or not isinstance(acf, Path) or not acf.is_file():
                continue
            try:
                raw = acf.read_text(encoding="utf-8")
                new = vdftext.replace_appmanifest_name(raw, orig)
                if new != raw:
                    acf.write_text(new, encoding="utf-8")
            except OSError as exc:
                result.warning.append(f"{acf.name}: {exc}")

        # Remove all program-created Steam collections.  User-created
        # collections are not in state.collections and are never touched.
        if state.collections:
            ns = collections.load_json_array(env.namespace_file)
            deleted, version = collections.tombstone_collections(ns, state.collections)
            if deleted:
                def write_collections() -> None:
                    collections.write_json_array(env.namespace_file, ns)
                    collections.update_namespaces_version(
                        env.namespaces_file, 1, version
                    )

                apply._retry_write(write_collections)
                result.collections_deleted = len(deleted)
            state.collections.clear()

        state.save()
        if log:
            log(
                f"复原完成：名称 {result.names_restored}，"
                f"分类 {result.categories_cleared}，收藏集 {result.collections_deleted}"
            )
    except Exception:
        if result.backup:
            try:
                apply._restore_files(env, state, result.backup, restore_state=False)
                if log:
                    log("复原失败，已自动回滚到复原前备份")
            except Exception as exc:
                if log:
                    log(f"复原失败且回滚失败：{exc}")
        if (was_running or restart) and not steamenv.steam_running():
            try:
                steamenv.start_steam(env)
            except Exception:
                pass
        raise

    if restart:
        steamenv.start_steam(env)
        result.steam_restarted = True
        if log:
            log("已重新启动 Steam")
    return result
