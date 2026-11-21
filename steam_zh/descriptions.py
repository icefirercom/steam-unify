"""Optional Steam store short-description cache for keyword classification.

Local appinfo does not contain store descriptions, so this module is strictly
optional: scanning never blocks on it.  A caller can use `fetch_many()` to
populate the cache concurrently when Steam Store/API is reachable, and
`translate.py` also merges descriptions it fetches along the way.
"""

from __future__ import annotations

import concurrent.futures
import html
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import paths

_CACHE_LOCK = threading.Lock()


def cache_path() -> Path:
    return paths.data_dir() / "descriptions.json"


def load_cache() -> dict[str, str]:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for appid, value in data.items():
            if isinstance(value, str):
                out[str(appid)] = value
            elif isinstance(value, dict) and isinstance(value.get("text"), str):
                out[str(appid)] = value["text"]
    return out


def get_description(appid: int) -> str | None:
    return load_cache().get(str(appid))


def save_cache(cache: dict[str, str]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(cache, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def merge_cache(new_entries: dict[str, str]) -> int:
    """Thread-safely merge new descriptions into the cache file. Returns added count."""
    cleaned = {str(k): _clean(v) for k, v in new_entries.items() if v and _clean(v)}
    if not cleaned:
        return 0
    with _CACHE_LOCK:
        cache = load_cache()
        added = 0
        for k, v in cleaned.items():
            if cache.get(k) != v:
                cache[k] = v
                added += 1
        if added:
            save_cache(cache)
        return added


def _clean(text: str) -> str:
    text = html.unescape(str(text or "")).strip()
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _opener(proxy: str | None = None, use_system_proxy: bool = True):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    if use_system_proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch_one(
    appid: int,
    proxy: str | None = None,
    timeout: float = 8.0,
    use_system_proxy: bool = True,
) -> str | None:
    url = (
        "https://store.steampowered.com/api/appdetails"
        f"?appids={appid}&cc=cn&l=schinese"
    )
    opener = _opener(proxy, use_system_proxy=use_system_proxy)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SteamUnify/2.0 (local category metadata)",
            "Accept": "application/json",
        },
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None
    item = data.get(str(appid)) if isinstance(data, dict) else None
    if not isinstance(item, dict) or not item.get("success"):
        return None
    info = item.get("data", {})
    text = info.get("short_description") or info.get("detailed_description") or ""
    cleaned = _clean(text)
    return cleaned or None


def fetch_many(
    appids: list[int],
    proxy: str | None = None,
    delay: float = 0.0,
    *,
    use_system_proxy: bool = True,
    concurrency: int = 5,
    progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, str]:
    """Concurrently fetch missing Steam store descriptions with cancellation."""
    cache = load_cache()
    missing = [int(a) for a in appids if str(a) not in cache]
    if not missing:
        return cache

    workers = max(1, min(int(concurrency or 5), 10))
    total = len(missing)
    done = 0
    changed = False
    consecutive_errors = 0
    stop_flag = False
    lock = threading.Lock()

    def worker(appid: int) -> tuple[int, str | None]:
        nonlocal stop_flag
        if stop_flag or (should_stop and should_stop()):
            return appid, None
        text = fetch_one(appid, proxy=proxy, use_system_proxy=use_system_proxy)
        return appid, text

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(worker, aid): aid for aid in missing}
        for fut in concurrent.futures.as_completed(futures):
            aid = futures[fut]
            try:
                _, text = fut.result()
            except Exception:
                text = None
            with lock:
                done += 1
                if text:
                    cache[str(aid)] = text
                    changed = True
                    consecutive_errors = 0
                else:
                    consecutive_errors += 1
                    if consecutive_errors >= 12:
                        stop_flag = True
                if done % 20 == 0 and changed:
                    save_cache(cache)
            if progress:
                progress(done, total, f"补全简介 {done}/{total}")
            if stop_flag or (should_stop and should_stop()):
                for pending in futures:
                    pending.cancel()
                break

    if changed:
        save_cache(cache)
    return cache
