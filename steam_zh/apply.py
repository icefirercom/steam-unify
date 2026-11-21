"""Apply names/categories to Steam, verify, roll back, and auto-fix."""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import appinfo, category_state, collections, filters, paths, steamenv, vdftext
from .appinfo_shards import ShardError, ShardSet
from .metadata import GameInfo
from .state import State, now_ts

BASE_DIR = paths.app_dir()
BACKUP_ROOT = BASE_DIR / "backups"

SKIP_CATEGORIES = {"", "不分类", "未分类", "none", "null"}


class ApplyError(Exception):
    pass


@dataclass
class ApplyResult:
    backup: Path | None = None
    names_applied: int = 0
    acfs_updated: int = 0
    collections_changed: int = 0
    collection_skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    verified_names: int = 0
    verify_failed: list[int] = field(default_factory=list)
    collections_verified: bool = False
    steam_restarted: bool = False


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _probe_writable(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    probe = directory / ".steam_zh_write_probe"
    try:
        with open(probe, "wb") as f:
            f.write(b"ok")
            f.flush()
            os.fsync(f.fileno())
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def preflight_writable(env: steamenv.SteamEnv) -> None:
    """Fail before closing Steam if any target directory is not writable."""
    targets: list[Path] = []
    if env.has_shards:
        targets.append(env.shard_dir)
    else:
        targets.append(env.appinfo.parent)
    targets += [env.cloudstorage]
    for info in steamenv.installed_apps(env.root).values():
        acf = info.get("acf")
        if isinstance(acf, Path):
            targets.append(acf.parent)
    for d in targets:
        try:
            _probe_writable(d)
        except OSError as exc:
            raise ApplyError(
                f"没有写入权限：{d}\n{exc}\n\n"
                "请把 Steam/本程序放在可写目录，或以管理员身份运行；"
                "也可以在设置中切换到正确的 Steam 账号。"
            ) from exc


def _retry_write(func, *args, attempts: int = 4, delay: float = 0.8):
    last: Exception | None = None
    for i in range(attempts):
        try:
            return func(*args)
        except (PermissionError, OSError) as exc:
            last = exc
            if i + 1 < attempts:
                time.sleep(delay * (i + 1))
    assert last is not None
    raise last


def _set_readonly(path: Path, readonly: bool) -> None:
    if not path.exists():
        return
    mode = os.stat(path).st_mode
    if readonly:
        os.chmod(path, mode & ~stat.S_IWRITE)
    else:
        os.chmod(path, mode | stat.S_IWRITE)


def is_readonly(path: Path) -> bool:
    try:
        return not bool(os.stat(path).st_mode & stat.S_IWRITE)
    except OSError:
        return False


def backup(
    env: steamenv.SteamEnv,
    state: State,
    installed: dict[int, dict[str, Any]],
    base: Path | None = None,
) -> Path:
    root = base or BACKUP_ROOT
    root.mkdir(parents=True, exist_ok=True)
    dest = steamenv.backup_dir(root)
    files: list[str] = []
    if env.has_shards:
        shutil.copytree(env.shard_dir, dest / "appinfo")
        files.append("appinfo/ (%d shards)" % len(list(env.shard_dir.glob("appinfo_*.vdf"))))
    targets = [
        env.appinfo,
        env.namespace_file,
        env.namespaces_file,
        env.localconfig,
    ]
    for p in targets:
        if p.is_file():
            rel = p.relative_to(env.root) if str(p).lower().startswith(str(env.root).lower()) else Path(p.name)
            out = dest / rel
            _copy(p, out)
            files.append(str(rel))
    acf_dir = dest / "acf"
    for appid, info in installed.items():
        acf = info.get("acf")
        if isinstance(acf, Path) and acf.is_file():
            _copy(acf, acf_dir / acf.name)
            files.append(f"acf/{acf.name}")
    manifest = {
        "created": now_ts(),
        "account": env.account,
        "steam_root": str(env.root),
        "files": files,
        "managed_collections": dict(state.collections),
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest


def prune_backups(keep: int = 10, root: Path | None = None) -> None:
    base = root or BACKUP_ROOT
    if not base.is_dir():
        return
    dirs = sorted(
        [p for p in base.iterdir() if p.is_dir() and (p / "manifest.json").is_file()],
        key=lambda p: p.name,
        reverse=True,
    )
    for old in dirs[max(1, keep):]:
        shutil.rmtree(old, ignore_errors=True)


def _plan_names(
    state: State, games: list[GameInfo], selected: set[int] | None
) -> tuple[list[GameInfo], dict[int, str]]:
    planned: list[GameInfo] = []
    names: dict[int, str] = {}
    for g in games:
        if state.is_ignored(g.appid) or g.appid in filters.CORE_SYSTEM_APPIDS:
            continue
        if selected is not None and g.appid not in selected:
            continue
        rec = state.get_app(g.appid)
        zh = str(rec.get("zh_name") or "").strip()
        if not zh:
            continue
        planned.append(g)
        names[g.appid] = zh
    return planned, names


def _build_computed_collections(
    state: State,
    games: list[GameInfo],
    selected: set[int] | None = None,
) -> dict[str, list[int]]:
    computed: dict[str, list[int]] = {}
    for g in games:
        if state.is_ignored(g.appid):
            continue
        if g.appid in filters.CORE_SYSTEM_APPIDS:
            continue
        rec = state.get_app(g.appid)
        if selected is not None and g.appid not in selected and not rec.get("applied"):
            continue
        for cat in category_state.get_categories(rec):
            cat = str(cat).strip()
            if not cat or cat.lower() in SKIP_CATEGORIES:
                continue
            bucket = computed.setdefault(cat, [])
            if g.appid not in bucket:
                bucket.append(g.appid)
    return computed


def apply_changes(
    env: steamenv.SteamEnv,
    state: State,
    games: list[GameInfo],
    selected: set[int] | None = None,
    restart: bool = True,
    lock_appinfo: bool = False,
    progress: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
    *,
    apply_names: bool = True,
    apply_collections: bool = True,
    backup_keep: int | None = None,
) -> ApplyResult:
    result = ApplyResult()
    preflight_writable(env)
    was_running = steamenv.steam_running()
    if was_running:
        if log:
            log("Steam 正在运行，正在自动关闭 ...")
        steamenv.close_steam(env, log=log)
        # Give Steam a moment to release its handles on cloudstorage files.
        time.sleep(2.0)
    installed = steamenv.installed_apps(env.root)
    result.backup = backup(env, state, installed)
    if backup_keep is not None:
        prune_backups(keep=int(backup_keep))
    if log:
        log(f"已备份到 {result.backup}")
    if progress:
        progress("已备份，正在写入 Steam 本地缓存 ...")

    # Make sure appinfo is writable (we may have locked it previously).
    if apply_names and not env.has_shards and is_readonly(env.appinfo):
        _set_readonly(env.appinfo, False)

    try:
        changed_ids: list[int] = []
        if apply_names:
            planned, names = _plan_names(state, games, selected)
            if env.has_shards:
                ss = ShardSet.load(env.shard_dir)
                for g in planned:
                    zh = names[g.appid]
                    try:
                        current = ss.get_name(g.appid) or ""
                        localized = ss.get_localized(g.appid)
                        if current == zh and not localized:
                            continue
                        orig_localized = ss.remove_localized(g.appid)
                        rec = state.get_app(g.appid)
                        if orig_localized and not rec.get("orig_localized"):
                            rec["orig_localized"] = orig_localized
                        ss.set_name(g.appid, zh)
                        changed_ids.append(g.appid)
                    except Exception as exc:
                        result.warnings.append(f"{g.appid} {g.name}: {exc}")
                if changed_ids:
                    _retry_write(ss.save)
                if lock_appinfo:
                    result.warnings.append("分片 appinfo 模式不支持只读锁定，已跳过")
            else:
                ai = appinfo.AppInfo.load(env.appinfo)
                for g in planned:
                    zh = names[g.appid]
                    try:
                        current = ai.get_name(g.appid) or ""
                        localized = ai.get_localized(g.appid)
                        if current == zh and not localized:
                            continue
                        orig_localized = ai.remove_localized(g.appid)
                        rec = state.get_app(g.appid)
                        if orig_localized and not rec.get("orig_localized"):
                            rec["orig_localized"] = orig_localized
                        ai.set_name(g.appid, zh)
                        changed_ids.append(g.appid)
                    except Exception as exc:
                        result.warnings.append(f"{g.appid} {g.name}: {exc}")
                if changed_ids:
                    data = ai.build()
                    tmp = env.appinfo.with_name(env.appinfo.name + ".tmp")

                    def write_appinfo() -> None:
                        with open(tmp, "wb") as f:
                            f.write(data)
                            f.flush()
                            os.fsync(f.fileno())
                        os.replace(tmp, env.appinfo)

                    _retry_write(write_appinfo)
                    if lock_appinfo:
                        _set_readonly(env.appinfo, True)

            result.names_applied = len(changed_ids)
            if log:
                log(
                    f"已写入 {len(changed_ids)} 个中文名"
                    if changed_ids
                    else "appinfo 中没有需要改写的名称"
                )

            # Update installed games' ACF name (cosmetic, using cached installed dict).
            for appid in changed_ids:
                info = installed.get(appid)
                if not info:
                    continue
                acf = info.get("acf")
                if not isinstance(acf, Path) or not acf.is_file():
                    continue
                try:
                    raw = acf.read_text(encoding="utf-8")
                    new = vdftext.replace_appmanifest_name(raw, names[appid])
                    if new != raw:
                        acf.write_text(new, encoding="utf-8")
                        result.acfs_updated += 1
                except Exception as exc:
                    result.warnings.append(f"更新 {acf.name} 失败: {exc}")
        elif log:
            log("已跳过中文名写入（按本次选项）")

        # Categories -> Steam collections (optional, excludes ignored/system apps).
        if apply_collections:
            computed = _build_computed_collections(state, games, selected=selected)
            if computed:
                if progress:
                    progress("正在写入 Steam 收藏集 ...")
                ns = collections.load_json_array(env.namespace_file)
                if not state.collections:
                    # Account switch / restored state: adopt collections that carry
                    # our category names instead of creating duplicates.
                    existing = collections.existing_user_names(ns)
                    for cat in computed:
                        key = existing.get(cat.lower())
                        if key and key.startswith(collections.COLLECTION_PREFIX):
                            state.collections[cat] = key[len(collections.COLLECTION_PREFIX):]
                    if state.collections and log:
                        log(f"已接管 {len(state.collections)} 个同名收藏集")
                changed, version, skipped = collections.sync_collections(
                    ns, state.collections, computed
                )
                result.collections_changed = changed
                result.collection_skipped = skipped
                if changed:
                    def write_collections() -> None:
                        collections.write_json_array(env.namespace_file, ns)
                        collections.update_namespaces_version(env.namespaces_file, 1, version)

                    _retry_write(write_collections)
                    if log:
                        log(f"已更新 {changed} 个收藏集")
                if skipped:
                    result.warnings.append(
                        "以下分类已存在手工收藏集，已跳过: " + "、".join(skipped)
                    )
        elif log:
            log("已跳过收藏集同步（仅应用名称模式）")

        for appid in changed_ids:
            state.set_app(appid, applied=True, applied_at=now_ts())
        state.save()
        if log:
            log("数据文件写入完成")
    except Exception:
        # Roll back the files we may have already changed.
        if result.backup:
            try:
                _restore_files(env, state, result.backup, restore_state=False)
                if log:
                    log("写入失败，已自动回滚到本次备份")
            except Exception as exc:
                if log:
                    log(f"自动回滚失败: {exc}")
        # Never leave Steam closed after a failed apply.
        if (was_running or restart) and not steamenv.steam_running():
            try:
                steamenv.start_steam(env)
                if log:
                    log("写入失败后已重新启动 Steam")
            except Exception as exc:
                if log:
                    log(f"重新启动 Steam 失败: {exc}")
        raise

    if restart:
        if progress:
            progress("正在启动 Steam ...")
        steamenv.start_steam(env)
        result.steam_restarted = True
        if log:
            log("已启动 Steam，等待库加载后校验 ...")

    return result


def verify(
    env: steamenv.SteamEnv,
    state: State,
    games: list[GameInfo],
    wait: float = 12.0,
    log: Callable[[str], None] | None = None,
    *,
    selected: set[int] | None = None,
    apply_names: bool = True,
    apply_collections: bool = True,
) -> ApplyResult:
    result = ApplyResult()
    if wait:
        time.sleep(wait)
    if not steamenv.steam_running():
        result.warnings.append("校验时 Steam 未运行")

    if apply_names:
        ss = None
        ai = None
        load_err: Exception | None = None
        for attempt in range(3):
            try:
                if env.has_shards:
                    ss = ShardSet.load(env.shard_dir)
                else:
                    ai = appinfo.AppInfo.load(env.appinfo, verify=False)
                load_err = None
                break
            except (OSError, PermissionError, appinfo.AppInfoError, ShardError) as exc:
                load_err = exc
                if attempt < 2:
                    time.sleep(2.0)

        if load_err is not None:
            msg = f"校验读取 appinfo 失败（Steam 可能正在占用或更新缓存，写入已完成）: {load_err}"
            result.warnings.append(msg)
            if log:
                log(msg)
        else:
            for g in games:
                if state.is_ignored(g.appid) or g.appid in filters.CORE_SYSTEM_APPIDS:
                    continue
                if selected is not None and g.appid not in selected:
                    continue
                rec = state.get_app(g.appid)
                zh = str(rec.get("zh_name") or "").strip()
                if not zh:
                    continue
                current = (ss.get_name(g.appid) if ss else ai.get_name(g.appid)) or ""
                if current == zh:
                    result.verified_names += 1
                else:
                    result.verify_failed.append(g.appid)

    if apply_collections:
        try:
            ns = collections.load_json_array(env.namespace_file)
            live_ids = set()
            for item in ns:
                if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str):
                    if item[0].startswith(collections.COLLECTION_PREFIX) and not item[1].get("is_deleted"):
                        live_ids.add(item[0])
            result.collections_verified = all(
                collections.COLLECTION_PREFIX + cid in live_ids
                for cid in state.collections.values()
                if cid
            )
        except Exception as exc:
            result.warnings.append(f"校验收藏集失败: {exc}")
    else:
        result.collections_verified = True

    if log:
        log(
            f"校验：名称 {result.verified_names} 个正确，"
            f"{len(result.verify_failed)} 个异常；收藏集 "
            f"{'正常' if result.collections_verified else '异常'}"
        )
    return result


def _find_backup_file(backup_dir: Path, env: steamenv.SteamEnv, name: str, subdirs: list[str]) -> Path | None:
    candidates = [backup_dir / name]
    for sub in subdirs:
        candidates.append(backup_dir / sub / name)
    candidates.append(backup_dir / "userdata" / env.account / "config" / "cloudstorage" / name)
    for c in candidates:
        if c.is_file():
            return c
    return None


def _restore_files(
    env: steamenv.SteamEnv,
    state: State,
    backup_dir: Path,
    restore_state: bool = True,
) -> int:
    if not env.has_shards and is_readonly(env.appinfo):
        _set_readonly(env.appinfo, False)
    restored = 0
    shard_backup = backup_dir / "appinfo"
    if shard_backup.is_dir():
        if env.appinfo.is_file():
            env.appinfo.unlink()
        if env.shard_dir.exists():
            shutil.rmtree(env.shard_dir)
        shutil.copytree(shard_backup, env.shard_dir)
        restored += len(list(env.shard_dir.glob("appinfo_*.vdf")))
    else:
        appinfo_candidates = [
            backup_dir / "appinfo.vdf",
            backup_dir / "appcache" / "appinfo.vdf",
        ]
        for c in appinfo_candidates:
            if c.is_file():
                _copy(c, env.appinfo)
                restored += 1
                # A monolithic file and the shard directory must not both be
                # present; Steam may prefer either one depending on version.
                if env.shard_dir.exists():
                    shutil.rmtree(env.shard_dir)
                break
    for name, dst in (
        ("cloud-storage-namespace-1.json", env.namespace_file),
        ("cloud-storage-namespaces.json", env.namespaces_file),
    ):
        src = _find_backup_file(
            backup_dir,
            env,
            name,
            ["config/cloudstorage", f"userdata/{env.account}/config/cloudstorage"],
        )
        if src:
            _copy(src, dst)
            restored += 1
    acf_dir = backup_dir / "acf"
    if acf_dir.is_dir():
        for acf in acf_dir.glob("appmanifest_*.acf"):
            for lib in steamenv.library_folders(env.root):
                dst = lib / "steamapps" / acf.name
                if dst.is_file():
                    _copy(acf, dst)
                    restored += 1
                    break
    if restore_state:
        managed: dict[str, str] = {}
        manifest_path = backup_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                raw = manifest.get("managed_collections")
                if isinstance(raw, dict):
                    managed = {str(k): str(v) for k, v in raw.items()}
            except (OSError, json.JSONDecodeError):
                managed = {}
        state.data["collections"] = managed
        for rec in state.apps.values():
            rec["applied"] = False
        state.save()
    return restored


def restore_backup(
    env: steamenv.SteamEnv,
    state: State,
    backup_dir: Path,
    restart: bool = False,
    log: Callable[[str], None] | None = None,
) -> None:
    if steamenv.steam_running():
        steamenv.close_steam(env, log=log)
    count = _restore_files(env, state, backup_dir, restore_state=True)
    if log:
        log(f"已从 {backup_dir} 还原 {count} 个文件")
    if restart:
        steamenv.start_steam(env)


def autofix(
    env: steamenv.SteamEnv,
    state: State,
    games: list[GameInfo],
    restart: bool = True,
    lock_appinfo: bool = False,
    log: Callable[[str], None] | None = None,
    *,
    apply_collections: bool = True,
    backup_keep: int | None = None,
) -> ApplyResult:
    """Re-apply names whose current appinfo value no longer matches state."""
    if steamenv.steam_running():
        if log:
            log("检测到 Steam 正在运行，自动关闭以修复 ...")
        steamenv.close_steam(env, log=log)
    return apply_changes(
        env,
        state,
        games,
        selected=None,
        restart=restart,
        lock_appinfo=lock_appinfo,
        log=log,
        apply_collections=apply_collections,
        backup_keep=backup_keep,
    )
