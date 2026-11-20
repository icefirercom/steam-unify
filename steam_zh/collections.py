"""Read/write Steam's modern library collections (cloudstorage JSON)."""

from __future__ import annotations

import json
import os
import secrets
import string
import time
from pathlib import Path
from typing import Any

COLLECTION_PREFIX = "user-collections."
SYSTEM_IDS = {"favorite", "hidden"}


class CollectionError(Exception):
    pass


def load_json_array(path: Path) -> list:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionError(f"无法读取 {path}: {exc}") from exc
    if not isinstance(data, list):
        raise CollectionError(f"{path} 不是数组格式")
    return data


def write_json_array(path: Path, data: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    last: Exception | None = None
    for attempt in range(4):
        try:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return
        except (PermissionError, OSError) as exc:
            last = exc
            if attempt < 3:
                time.sleep(0.8 * (attempt + 1))
    assert last is not None
    raise last


def _records(namespace: list) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in namespace:
        if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], dict):
            out[item[0]] = item[1]
    return out


def _record_name(record: dict) -> str:
    if record.get("is_deleted"):
        return ""
    raw = record.get("value")
    if not isinstance(raw, str):
        return ""
    try:
        obj = json.loads(raw)
        return str(obj.get("name", "") or "")
    except (json.JSONDecodeError, TypeError):
        return ""


def max_version(namespace: list) -> int:
    best = 0
    for item in namespace:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[1], dict)):
            continue
        try:
            best = max(best, int(item[1].get("version", 0)))
        except (TypeError, ValueError):
            continue
    return best


def new_collection_id(used_keys: set[str]) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        cid = "uc-" + "".join(secrets.choice(alphabet) for _ in range(12))
        if COLLECTION_PREFIX + cid not in used_keys:
            return cid


def _is_static(record: dict) -> bool:
    try:
        obj = json.loads(record.get("value", "{}"))
        return "filterSpec" not in obj
    except (json.JSONDecodeError, TypeError):
        return False


def sync_collections(
    namespace: list,
    managed: dict[str, str],
    computed: dict[str, list[int]],
) -> tuple[int, int, list[str]]:
    """Create/update managed collections. Mutates namespace and managed.

    Returns (changed_count, max_version, skipped_category_names).
    """
    by_key = {
        item[0]: item[1]
        for item in namespace
        if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], dict)
    }
    name_to_key: dict[str, str] = {}
    for key, rec in by_key.items():
        if key.startswith(COLLECTION_PREFIX) and not rec.get("is_deleted"):
            nm = _record_name(rec).strip().lower()
            if nm:
                name_to_key.setdefault(nm, key)

    current_max = max_version(namespace)
    version = current_max
    now = int(time.time())
    changed = 0
    skipped: list[str] = []
    used_keys = set(by_key)

    for category, appids in computed.items():
        cid = managed.get(category)
        key = COLLECTION_PREFIX + cid if cid else ""
        record = by_key.get(key) if key else None
        if record is not None and (record.get("is_deleted") or not _is_static(record)):
            record = None

        if record is None:
            # A collection with this name that we did not create? Never touch it.
            clash = name_to_key.get(category.strip().lower())
            if clash and clash != key:
                skipped.append(category)
                continue
            if not appids:
                continue
            cid = new_collection_id(used_keys)
            key = COLLECTION_PREFIX + cid
            version += 1
            value = {"id": cid, "name": category, "added": sorted(set(appids)), "removed": []}
            rec = {
                "key": key,
                "timestamp": now,
                "value": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                "version": str(version),
                "conflictResolutionMethod": "custom",
                "strMethodId": "union-collections",
            }
            namespace.append([key, rec])
            by_key[key] = rec
            used_keys.add(key)
            managed[category] = cid
            changed += 1
            continue

        try:
            value = json.loads(record.get("value", "{}"))
        except (json.JSONDecodeError, TypeError):
            value = {"id": cid, "name": category}
        current = sorted(int(x) for x in value.get("added", []) if str(x).lstrip("-").isdigit())
        target = sorted(set(appids))
        if current == target and not value.get("removed"):
            continue
        value["added"] = target
        value["removed"] = []
        value["name"] = category
        value["id"] = cid
        record["value"] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        record["timestamp"] = now
        version += 1
        record["version"] = str(version)
        changed += 1

    return changed, version, skipped


def tombstone_collections(namespace: list, managed: dict[str, str]) -> tuple[list[str], int]:
    """Soft-delete all collections we created. Clears `managed`."""
    by_key = {
        item[0]: item[1]
        for item in namespace
        if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], dict)
    }
    version = max_version(namespace)
    now = int(time.time())
    deleted: list[str] = []
    for category, cid in list(managed.items()):
        key = COLLECTION_PREFIX + cid
        rec = by_key.get(key)
        if rec is None:
            managed.pop(category, None)
            continue
        version += 1
        rec.clear()
        rec.update(
            {
                "key": key,
                "timestamp": now,
                "is_deleted": True,
                "version": str(version),
            }
        )
        deleted.append(category)
        managed.pop(category, None)
    return deleted, version


def update_namespaces_version(path: Path, namespace_id: int, version: int) -> None:
    data = load_json_array(path)
    found = False
    for item in data:
        if isinstance(item, list) and len(item) == 2 and item[0] == namespace_id:
            item[1] = str(version)
            found = True
    if not found:
        data.append([namespace_id, str(version)])
    write_json_array(path, data)


def existing_user_names(namespace: list) -> dict[str, str]:
    """Return {lower_name: key} for live user collections."""
    out: dict[str, str] = {}
    for item in namespace:
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], dict)):
            continue
        key, rec = item
        if not key.startswith(COLLECTION_PREFIX) or rec.get("is_deleted"):
            continue
        name = _record_name(rec).strip()
        if name:
            out.setdefault(name.lower(), key)
    return out
