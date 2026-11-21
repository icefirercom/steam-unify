"""Pure-Python parser for Steam's packageinfo.vdf and userdata licensecache.

Used to discover all games owned/licensed by the active account (including
games that have never been launched or installed on this PC and therefore lack
entries in ``localconfig.vdf``), while excluding Steam's public Sub 0 package.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

MAGIC_V39 = 0x06565527
MAGIC_V40 = 0x06565528
FOOTER_EOF = 0xFFFFFFFF

T_OBJECT = 0x00
T_STRING = 0x01
T_INT32 = 0x02
T_FLOAT32 = 0x03
T_POINTER = 0x04
T_WSTRING = 0x05
T_COLOR = 0x06
T_UINT64 = 0x07
T_END = 0x08
T_INT64 = 0x0A


class PackageInfoError(Exception):
    pass


def _read_cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.find(b"\x00", pos)
    if end < 0:
        raise PackageInfoError("unterminated binary VDF string")
    return data[pos:end].decode("utf-8", "replace"), end + 1


def _parse_bin_vdf_map(data: bytes, pos: int) -> tuple[dict[str, Any], int]:
    """Parse an inline-key binary VDF map up to its terminating T_END (0x08)."""
    out: dict[str, Any] = {}
    n = len(data)
    while pos < n:
        t = data[pos]
        pos += 1
        if t == T_END:
            return out, pos
        key, pos = _read_cstr(data, pos)
        if t == T_OBJECT:
            val, pos = _parse_bin_vdf_map(data, pos)
        elif t == T_STRING:
            val, pos = _read_cstr(data, pos)
        elif t in (T_INT32, T_POINTER):
            if pos + 4 > n:
                raise PackageInfoError("truncated VDF int32")
            val = struct.unpack_from("<i", data, pos)[0]
            pos += 4
        elif t == T_FLOAT32:
            if pos + 4 > n:
                raise PackageInfoError("truncated VDF float32")
            val = struct.unpack_from("<f", data, pos)[0]
            pos += 4
        elif t == T_WSTRING:
            start = pos
            while pos + 1 < n:
                if data[pos] == 0 and data[pos + 1] == 0:
                    val = data[start:pos].decode("utf-16-le", "replace")
                    pos += 2
                    break
                pos += 2
            else:
                raise PackageInfoError("unterminated VDF wstring")
        elif t == T_COLOR:
            if pos + 4 > n:
                raise PackageInfoError("truncated VDF color")
            val = data[pos : pos + 4]
            pos += 4
        elif t == T_UINT64:
            if pos + 8 > n:
                raise PackageInfoError("truncated VDF uint64")
            val = struct.unpack_from("<Q", data, pos)[0]
            pos += 8
        elif t == T_INT64:
            if pos + 8 > n:
                raise PackageInfoError("truncated VDF int64")
            val = struct.unpack_from("<q", data, pos)[0]
            pos += 8
        else:
            raise PackageInfoError(f"unknown binary VDF type 0x{t:02x} at {pos - 1}")
        out[key] = val
    raise PackageInfoError("unterminated binary VDF map")


def _extract_appids(vdf_map: dict[str, Any], pkg_id: int) -> set[int]:
    node = vdf_map.get(str(pkg_id))
    if not isinstance(node, dict):
        # Fallback to first nested dict if key differs from pkg_id string.
        for v in vdf_map.values():
            if isinstance(v, dict):
                node = v
                break
    if not isinstance(node, dict):
        return set()
    appids_node = node.get("appids")
    if not isinstance(appids_node, dict):
        return set()
    out: set[int] = set()
    for val in appids_node.values():
        try:
            aid = int(val)
        except (TypeError, ValueError):
            continue
        if aid > 0:
            out.add(aid)
    return out


def parse_packageinfo(path: Path) -> dict[int, set[int]]:
    """Parse ``appcache/packageinfo.vdf`` and return ``{package_id: {appid, ...}}``."""
    data = Path(path).read_bytes()
    if len(data) < 8:
        raise PackageInfoError("packageinfo.vdf is too small")
    magic, _universe = struct.unpack_from("<II", data, 0)
    if magic not in (MAGIC_V39, MAGIC_V40):
        raise PackageInfoError(f"unsupported packageinfo magic 0x{magic:08x}")

    pos = 8
    n = len(data)
    packages: dict[int, set[int]] = {}
    meta_tail = 24 if magic == MAGIC_V39 else 32  # 20B sha1 + 4B change (+ 8B pics_token)

    while pos + 4 <= n:
        pkg_id = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if pkg_id == FOOTER_EOF:
            break
        if pos + meta_tail > n:
            raise PackageInfoError(f"truncated metadata for package {pkg_id}")
        pos += meta_tail
        vdf_map, pos = _parse_bin_vdf_map(data, pos)
        packages[pkg_id] = _extract_appids(vdf_map, pkg_id)

    return packages


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    n = len(data)
    while pos < n:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")
    raise ValueError("truncated varint")


def _skip_wire_value(data: bytes, pos: int, wire_type: int) -> int:
    if wire_type == 0:
        _, pos = _read_varint(data, pos)
        return pos
    if wire_type == 1:
        if pos + 8 > len(data):
            raise ValueError("truncated 64-bit field")
        return pos + 8
    if wire_type == 2:
        length, pos = _read_varint(data, pos)
        if pos + length > len(data):
            raise ValueError("truncated length-delimited field")
        return pos + length
    if wire_type == 5:
        if pos + 4 > len(data):
            raise ValueError("truncated 32-bit field")
        return pos + 4
    raise ValueError(f"unsupported protobuf wire type {wire_type}")


def _decode_license_msg(blob: bytes) -> int | None:
    pos = 0
    n = len(blob)
    while pos < n:
        tag, pos = _read_varint(blob, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07
        if field_num == 1 and wire_type == 0:
            val, pos = _read_varint(blob, pos)
            return int(val)
        pos = _skip_wire_value(blob, pos, wire_type)
    return None


def parse_licensecache(path: Path) -> set[int]:
    """Extract package IDs from Steam's protobuf ``userdata/<account>/config/licensecache``."""
    p = Path(path)
    if not p.is_file():
        return set()
    try:
        data = p.read_bytes()
    except OSError:
        return set()
    if not data:
        return set()

    for start_off in (0, 4, 8):
        if start_off >= len(data):
            break
        try:
            pkgs = _decode_license_list(data[start_off:])
            if pkgs:
                return pkgs
        except Exception:
            continue
    return set()


def _decode_license_list(data: bytes) -> set[int]:
    pos = 0
    n = len(data)
    pkgs: set[int] = set()
    while pos < n:
        tag, pos = _read_varint(data, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07
        if field_num in (1, 2) and wire_type == 2:
            length, pos = _read_varint(data, pos)
            if pos + length > n:
                raise ValueError("truncated License message")
            pkg_id = _decode_license_msg(data[pos : pos + length])
            if pkg_id is not None and pkg_id > 0:
                pkgs.add(pkg_id)
            pos += length
        else:
            pos = _skip_wire_value(data, pos, wire_type)
    return pkgs


def licensed_appids(
    root: Path,
    account: str = "",
    *,
    active_account_count: int = 1,
) -> set[int]:
    """Return owned/licensed AppIDs from ``appcache/packageinfo.vdf``.

    Sub 0 (Steam's public free package) is always excluded so unowned public
    promos/drivers do not pollute the library.  When multiple local accounts
    exist, ``licensecache`` narrows packages down to the active account.
    """
    pkg_path = Path(root) / "appcache" / "packageinfo.vdf"
    if not pkg_path.is_file():
        return set()
    try:
        packages = parse_packageinfo(pkg_path)
    except (OSError, PackageInfoError, struct.error):
        return set()

    # Always exclude Steam public Sub 0.
    packages.pop(0, None)

    if account and active_account_count > 1:
        lc_path = Path(root) / "userdata" / str(account) / "config" / "licensecache"
        lc_pkgs = parse_licensecache(lc_path)
        overlap = lc_pkgs & set(packages)
        if overlap:
            packages = {pid: packages[pid] for pid in overlap}

    out: set[int] = set()
    for appids in packages.values():
        out.update(appids)
    return out
