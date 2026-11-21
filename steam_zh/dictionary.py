"""Layered Chinese name dictionaries with JSON/CSV import & export.

Priority order (highest first):
1. User custom dictionary (``data/user_names.json``) - preserved across upgrades
2. Bundled dictionary (``data/official_names.json``)
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

from . import paths


def bundled_path() -> Path:
    return paths.data_dir() / "official_names.json"


def user_path() -> Path:
    return paths.data_dir() / "user_names.json"


def _read_mapping(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _normalize_json_data(data)


def _normalize_json_data(data: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = str(k).strip()
            if not key.isdigit():
                continue
            if isinstance(v, str) and v.strip():
                out[key] = v.strip()
            elif isinstance(v, dict):
                val = (
                    v.get("zh_name")
                    or v.get("中文名")
                    or v.get("name_zh")
                    or v.get("name")
                    or ""
                )
                if isinstance(val, str) and val.strip():
                    out[key] = val.strip()
    elif isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            key = str(
                item.get("appid")
                or item.get("app_id")
                or item.get("id")
                or ""
            ).strip()
            if not key.isdigit():
                continue
            val = (
                item.get("zh_name")
                or item.get("中文名")
                or item.get("name_zh")
                or item.get("translation")
                or item.get("name")
                or ""
            )
            if isinstance(val, str) and val.strip():
                out[key] = val.strip()
    return out


def _atomic_write_json(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    ordered = dict(sorted(data.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0))
    tmp.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_bundled() -> dict[str, str]:
    return _read_mapping(bundled_path())


def load_user() -> dict[str, str]:
    return _read_mapping(user_path())


def save_user(mapping: dict[str, str]) -> None:
    clean = {
        str(k).strip(): str(v).strip()
        for k, v in mapping.items()
        if str(k).strip().isdigit() and str(v).strip()
    }
    _atomic_write_json(user_path(), clean)


def load_merged() -> tuple[dict[str, str], dict[str, str]]:
    """Return (user_dict, bundled_dict)."""
    return load_user(), load_bundled()


def parse_csv(path: Path) -> dict[str, str]:
    """Parse a CSV file containing appid and Chinese name columns."""
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "replace")

    lines = text.splitlines()
    if not lines:
        return {}

    reader = csv.DictReader(lines)
    if not reader.fieldnames:
        return {}

    fields = [f.strip() for f in reader.fieldnames if f is not None]
    low_map = {f.lower(): f for f in fields}

    id_col = None
    for cand in ("appid", "app_id", "id", "steam_appid"):
        if cand in low_map:
            id_col = low_map[cand]
            break

    name_col = None
    for cand in ("中文名", "zh_name", "name_zh", "chinese_name", "translation", "译名", "zh"):
        if cand in low_map:
            name_col = low_map[cand]
            break

    out: dict[str, str] = {}
    if id_col and name_col:
        for row in reader:
            aid = str(row.get(id_col) or "").strip()
            zh = str(row.get(name_col) or "").strip()
            if aid.isdigit() and zh:
                out[aid] = zh
        return out

    # Fallback: headerless 2+ column CSV (col 0 = appid, last non-empty or col 1 = zh_name)
    plain_reader = csv.reader(lines)
    for row in plain_reader:
        if len(row) < 2:
            continue
        aid = row[0].strip()
        if not aid.isdigit():
            continue
        zh = row[2].strip() if len(row) >= 3 and row[2].strip() else row[1].strip()
        if zh:
            out[aid] = zh
    return out


def import_file(path: Path, *, merge_into_user: bool = True) -> tuple[int, dict[str, str]]:
    """Import entries from JSON or CSV.

    Returns ``(imported_count, imported_entries)``.
    When ``merge_into_user`` is True, persists into ``data/user_names.json``.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        entries = parse_csv(path)
    else:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        entries = _normalize_json_data(data)

    if merge_into_user and entries:
        current = load_user()
        current.update(entries)
        save_user(current)

    return len(entries), entries


def export_file(
    path: Path,
    mapping: dict[str, str],
    *,
    orig_names: dict[str, str] | None = None,
) -> int:
    """Export ``{appid: zh_name}`` to JSON or CSV. Returns entry count."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        str(k).strip(): str(v).strip()
        for k, v in mapping.items()
        if str(k).strip().isdigit() and str(v).strip()
    }
    ordered = sorted(clean.items(), key=lambda kv: int(kv[0]))
    if path.suffix.lower() == ".csv":
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["appid", "原名", "中文名"])
            for aid, zh in ordered:
                orig = (orig_names or {}).get(aid, "")
                w.writerow([aid, orig, zh])
    else:
        _atomic_write_json(path, dict(ordered))
    return len(ordered)
