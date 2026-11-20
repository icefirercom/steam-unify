"""Persistent local state: translations, categories, managed collections."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import paths

DATA_DIR = paths.data_dir()
STATE_PATH = DATA_DIR / "state.json"
SETTINGS_PATH = DATA_DIR / "settings.json"


DEFAULT_SETTINGS: dict[str, Any] = {
    "steam_root": "",
    "account": "",
    "use_network": True,
    "auto_translate_new": True,
    "proxy": "",
    "include_software": False,
    "all_known": True,
    "restart_steam": True,
    "lock_appinfo": False,
    "backup_keep": 10,
}


def _load(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


class State:
    def __init__(self, data: dict[str, Any] | None = None):
        self.data = data if isinstance(data, dict) else {}
        self.data.setdefault("version", 2)
        self.data.setdefault("apps", {})
        self.data.setdefault("collections", {})
        self.data.setdefault("settings", {})
        for key, value in DEFAULT_SETTINGS.items():
            self.data["settings"].setdefault(key, value)

    @classmethod
    def load(cls) -> "State":
        return cls(_load(STATE_PATH, {}))

    def save(self) -> None:
        _atomic_write_json(STATE_PATH, self.data)

    @property
    def apps(self) -> dict[str, Any]:
        return self.data["apps"]

    @property
    def collections(self) -> dict[str, str]:
        return self.data["collections"]

    @property
    def settings(self) -> dict[str, Any]:
        return self.data["settings"]

    def get_app(self, appid: int) -> dict[str, Any]:
        return self.apps.setdefault(str(appid), {})

    def set_app(self, appid: int, **values: Any) -> dict[str, Any]:
        rec = self.get_app(appid)
        rec.update(values)
        return rec

    def merge_games(self, games) -> None:
        for g in games:
            rec = self.get_app(g.appid)
            rec.setdefault("orig_name", g.name)
            rec.setdefault("zh_name", "")
            rec.setdefault("source", "")
            rec.setdefault("categories", [])
            rec.setdefault("primary_category", "")
            rec.setdefault("category", "")
            rec.setdefault("category_source", "")
            rec.setdefault("category_locked", False)
            rec.setdefault("locked", False)
            rec.setdefault("applied", False)
            rec.setdefault("orig_localized", g.localized or None)
            rec.setdefault("error", "")
            # If the stored original name does not match appinfo (Steam update),
            # keep the user's translation but refresh the original for undo.
            # Refresh the captured original name only when Steam has actually
            # changed the live name away from both the original and our target.
            # If the live name equals our Chinese target, the English original
            # must be kept for display/revert.
            current = g.name
            zh = str(rec.get("zh_name") or "")
            orig = rec.get("orig_name")
            if not orig:
                rec["orig_name"] = current
                rec["orig_localized"] = g.localized or rec.get("orig_localized")
            elif (
                current != zh
                and current != orig
                and str(rec.get("source") or "") != "original"
            ):
                # Steam refreshed the real English name.  Do not do this for
                # records produced by the undo feature: their live name may
                # still be the old Chinese name for a moment, and treating it
                # as the original would corrupt the next undo.
                rec["orig_name"] = current
                rec["orig_localized"] = g.localized or rec.get("orig_localized")
            elif not rec.get("orig_localized") and g.localized:
                rec["orig_localized"] = g.localized

    def to_settings(self) -> dict[str, Any]:
        return self.settings

    def save_settings(self, settings: dict[str, Any]) -> None:
        self.data["settings"].update(settings)
        _atomic_write_json(SETTINGS_PATH, self.settings)
        self.save()

    @staticmethod
    def load_settings() -> dict[str, Any]:
        data = dict(DEFAULT_SETTINGS)
        data.update(_load(SETTINGS_PATH, {}))
        return data


def now_ts() -> int:
    return int(time.time())
