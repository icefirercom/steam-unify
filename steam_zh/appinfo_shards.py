"""Steam's sharded appinfo cache (v30, magic 0x0756442A).

Current Steam clients store app metadata in ``appcache/appinfo/appinfo_XX.vdf``
(256 shards) instead of the old monolithic ``appcache/appinfo.vdf``.

Shard layout (all little-endian):

    header (44 bytes)
        magic u32 = 0x0756442A
        universe u32
        shard_id u32
        shard_count u32 (normally 256)
        entry_count u32
        index_size u32          (= entry_count * 80)
        key_count u32
        key_table_size u32
        data_size u32
        8 unknown bytes (preserved)
    entry index (entry_count * 80 bytes)
        appid u32
        data_offset u32         (relative to data area)
        data_size u32           (20-byte blob hash + VDF bytes)
        ... 68 more bytes preserved (flags, change number, hashes)
    key table (key_table_size bytes)
        key_count null-terminated UTF-8 strings, used as VDF key indices
    data area (data_size bytes)
        repeated: sha1(VDF) 20 bytes + binary VDF root + 0x08 trailer
"""

from __future__ import annotations

import hashlib
import os
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .appinfo import (
    Node,
    T_INT32,
    T_OBJECT,
    T_STRING,
    AppInfoError,
    parse_node,
    serialize_node,
)


MAGIC_V30 = 0x0756442A
HEADER_SIZE = 44
RECORD_SIZE = 80


class ShardError(Exception):
    pass


@dataclass
class ShardRecord:
    appid: int
    raw: bytes
    data_offset: int
    data_size: int
    raw_vdf: bytes
    root: Node
    tail: bytes
    dirty: bool = False

    def vdf_bytes(self) -> bytes:
        if self.dirty:
            return serialize_node(self.root) + self.tail
        return self.raw_vdf


class ShardFile:
    def __init__(
        self,
        path: Path,
        raw_header: bytes,
        raw_index: bytes,
        key_raw: bytes,
        keys: list[str],
        records: list[ShardRecord],
        raw_blobs: bytes,
    ):
        self.path = Path(path)
        self.raw_header = raw_header
        self.raw_index = raw_index
        self.key_raw = key_raw
        self.keys = keys
        self.records = records
        self.raw_blobs = raw_blobs
        self.key_index: dict[str, int] = {}
        for i, k in enumerate(keys):
            self.key_index.setdefault(k, i)
        self._by_appid = {r.appid: r for r in records}
        self._original = None

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "ShardFile":
        path = Path(path)
        b = path.read_bytes()
        if len(b) < HEADER_SIZE:
            raise ShardError(f"{path} is too small")
        fields = struct.unpack_from("<9I", b, 0)
        magic, universe, sid, shard_count, ec, idxsz, kc, ktsz, dsz = fields
        if magic != MAGIC_V30:
            raise ShardError(f"{path}: unsupported shard magic 0x{magic:08x}")
        if idxsz != ec * RECORD_SIZE:
            raise ShardError(f"{path}: unexpected index size {idxsz} (ec={ec})")
        key_start = HEADER_SIZE + idxsz
        data_start = key_start + ktsz
        if data_start + dsz != len(b):
            raise ShardError(
                f"{path}: data area ends at {data_start + dsz}, file is {len(b)}"
            )
        raw_header = b[:HEADER_SIZE]
        raw_index = b[HEADER_SIZE:key_start]
        key_raw = b[key_start:data_start]
        keys = key_raw.split(b"\x00")[:kc]
        keys = [k.decode("utf-8", "replace") for k in keys]
        raw_blobs = b[data_start:]

        records: list[ShardRecord] = []
        expected_offset = 0
        for i in range(ec):
            raw = raw_index[i * RECORD_SIZE:(i + 1) * RECORD_SIZE]
            appid, off, size = struct.unpack_from("<III", raw, 0)
            if off != expected_offset:
                raise ShardError(
                    f"{path.name}: record {i} offset {off} != {expected_offset}"
                )
            if off + size > len(raw_blobs):
                raise ShardError(f"{path.name}: record {i} overruns data")
            blob = raw_blobs[off:off + size]
            if size < 21:
                raise ShardError(f"{path.name}: record {i} too small")
            vdf = blob[20:]
            root, used = parse_node(vdf)
            tail = vdf[used:]
            if tail != b"\x08":
                raise ShardError(
                    f"{path.name}: app {appid} unexpected trailer {tail.hex()}"
                )
            records.append(
                ShardRecord(
                    appid=appid,
                    raw=raw,
                    data_offset=off,
                    data_size=size,
                    raw_vdf=vdf,
                    root=root,
                    tail=tail,
                )
            )
            expected_offset += size
        if expected_offset != dsz:
            raise ShardError(
                f"{path.name}: data consumed {expected_offset} of {dsz}"
            )
        obj = cls(path, raw_header, raw_index, key_raw, keys, records, raw_blobs)
        obj._original = b
        return obj

    @property
    def dirty(self) -> bool:
        return any(r.dirty for r in self.records)

    def save(self) -> None:
        if not self.dirty:
            return
        data = self.build()
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        # Reload to verify the new file and clear dirty flags.
        fresh = ShardFile.load(self.path)
        self.__dict__.update(fresh.__dict__)

    def build(self) -> bytes:
        if not self.dirty and self._original is not None:
            return self._original
        index_parts: list[bytes] = []
        blob_parts: list[bytes] = []
        offset = 0
        for rec in self.records:
            if rec.dirty:
                vdf = serialize_node(rec.root) + rec.tail
                blob = hashlib.sha1(vdf).digest() + vdf
            else:
                source = self.raw_blobs[rec.data_offset:rec.data_offset + rec.data_size]
                blob = source
            raw = bytearray(rec.raw)
            struct.pack_into("<I", raw, 4, offset)
            struct.pack_into("<I", raw, 8, len(blob))
            index_parts.append(bytes(raw))
            blob_parts.append(blob)
            offset += len(blob)
        header = bytearray(self.raw_header)
        struct.pack_into("<I", header, 32, offset)
        index_blob = b"".join(index_parts) + self.key_raw
        index_crc = zlib.crc32(index_blob) & 0xFFFFFFFF
        struct.pack_into("<I", header, 36, index_crc)
        header_crc = zlib.crc32(bytes(header[:40])) & 0xFFFFFFFF
        struct.pack_into("<I", header, 40, header_crc)
        out = (
            bytes(header)
            + b"".join(index_parts)
            + self.key_raw
            + b"".join(blob_parts)
        )
        # Self-check: the rebuilt shard must parse and keep the same appids.
        check = ShardFile.from_bytes(out, self.path)
        if [r.appid for r in check.records] != [r.appid for r in self.records]:
            raise ShardError("appid list changed while rebuilding shard")
        return out

    @classmethod
    def from_bytes(cls, data: bytes, path: Path) -> "ShardFile":
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="shard-")) / path.name
        try:
            tmp.write_bytes(data)
            return cls.load(tmp)
        finally:
            try:
                tmp.unlink()
                tmp.parent.rmdir()
            except OSError:
                pass

    # ---------------------------------------------------------------- names
    def _key_text(self, node: Node) -> str:
        if node.key is not None and 0 <= node.key < len(self.keys):
            return self.keys[node.key]
        return ""

    def common(self, rec: ShardRecord) -> Node | None:
        for child in rec.root.children():
            if child.type == T_OBJECT and self._key_text(child) == "common":
                return child
        return None

    def _child(self, obj: Node, name: str) -> Node | None:
        for child in obj.children():
            if self._key_text(child) == name:
                return child
        return None

    def get_name(self, appid: int) -> str | None:
        rec = self._by_appid.get(appid)
        if not rec:
            return None
        common = self.common(rec)
        if common is None:
            return None
        node = self._child(common, "name")
        if node is None or node.type != T_STRING:
            return None
        return node.value.decode("utf-8", "replace")

    def get_localized(self, appid: int) -> dict[str, str] | None:
        rec = self._by_appid.get(appid)
        if not rec:
            return None
        common = self.common(rec)
        if common is None:
            return None
        node = self._child(common, "name_localized")
        if node is None or node.type != T_OBJECT:
            return None
        out: dict[str, str] = {}
        for child in node.children():
            if child.type == T_STRING:
                out[self._key_text(child)] = child.value.decode("utf-8", "replace")
        return out

    def set_name(self, appid: int, name: str) -> bool:
        rec = self._by_appid.get(appid)
        if not rec:
            raise ShardError(f"app {appid} not in {self.path.name}")
        common = self.common(rec)
        if common is None:
            raise ShardError(f"app {appid} has no common section")
        node = self._child(common, "name")
        if node is None or node.type != T_STRING:
            raise ShardError(f"app {appid} has no common.name")
        node.value = name.encode("utf-8")
        rec.dirty = True
        return True

    def remove_localized(self, appid: int) -> dict[str, str] | None:
        rec = self._by_appid.get(appid)
        if not rec:
            return None
        common = self.common(rec)
        if common is None:
            return None
        node = self._child(common, "name_localized")
        if node is None:
            return None
        old = self.get_localized(appid) or {}
        common.value.remove(node)
        rec.dirty = True
        return old

    def restore_localized(self, appid: int, mapping: dict[str, str] | None) -> None:
        """Restore a previously removed common.name_localized object."""
        if not mapping:
            return
        rec = self._by_appid.get(appid)
        if not rec:
            return
        common = self.common(rec)
        if common is None:
            return
        if self._child(common, "name_localized") is not None:
            return
        nodes: list[Node] = []
        for lang, text in mapping.items():
            idx = self.key_index.get(lang)
            if idx is None:
                continue
            nodes.append(Node(T_STRING, idx, str(text).encode("utf-8")))
        if not nodes:
            return
        key = self.key_index.get("name_localized")
        if key is None:
            return
        common.value.append(Node(T_OBJECT, key, nodes))
        rec.dirty = True


class ShardSet:
    def __init__(self, directory: Path, shards: list[ShardFile]):
        self.directory = Path(directory)
        self.shards = shards
        self.by_appid: dict[int, tuple[ShardFile, ShardRecord]] = {}
        for sh in shards:
            for rec in sh.records:
                self.by_appid[rec.appid] = (sh, rec)

    @classmethod
    def load(cls, directory: str | os.PathLike[str]) -> "ShardSet":
        directory = Path(directory)
        files = sorted(directory.glob("appinfo_*.vdf"))
        if not files:
            raise ShardError(f"{directory} has no appinfo shards")
        shards = [ShardFile.load(f) for f in files]
        return cls(directory, shards)

    @classmethod
    def exists(cls, directory: Path) -> bool:
        return directory.is_dir() and any(directory.glob("appinfo_*.vdf"))

    def get_name(self, appid: int) -> str | None:
        pair = self.by_appid.get(appid)
        return pair[0].get_name(appid) if pair else None

    def common_fields(self, appid: int) -> dict[str, Any] | None:
        pair = self.by_appid.get(appid)
        if not pair:
            return None
        sh, rec = pair
        common = sh.common(rec)
        if common is None:
            return None
        name = None
        typ = ""
        genres: list[int] = []
        tags: list[int] = []
        localized: dict[str, str] = {}
        for child in common.children():
            key = sh._key_text(child)
            if key == "name" and child.type == T_STRING:
                name = child.value.decode("utf-8", "replace")
            elif key == "type" and child.type == T_STRING:
                typ = child.value.decode("utf-8", "replace")
            elif key == "genres" and child.type == T_OBJECT:
                for g in child.children():
                    if g.type in (T_INT32,):
                        genres.append(int(g.value))
            elif key == "store_tags" and child.type == T_OBJECT:
                for t in child.children():
                    if t.type in (T_INT32,):
                        tags.append(int(t.value))
            elif key == "name_localized" and child.type == T_OBJECT:
                for lang in child.children():
                    if lang.type == T_STRING:
                        localized[sh._key_text(lang)] = lang.value.decode(
                            "utf-8", "replace"
                        )
        if name is None:
            return None
        return {
            "name": name,
            "type": typ,
            "genres": genres,
            "store_tags": tags,
            "localized": localized,
        }

    @property
    def appids(self) -> list[int]:
        return list(self.by_appid)

    def get_localized(self, appid: int) -> dict[str, str] | None:
        pair = self.by_appid.get(appid)
        return pair[0].get_localized(appid) if pair else None

    def set_name(self, appid: int, name: str) -> bool:
        pair = self.by_appid.get(appid)
        if not pair:
            raise ShardError(f"app {appid} not in shards")
        return pair[0].set_name(appid, name)

    def remove_localized(self, appid: int) -> dict[str, str] | None:
        pair = self.by_appid.get(appid)
        return pair[0].remove_localized(appid) if pair else None

    def restore_localized(self, appid: int, mapping: dict[str, str] | None) -> None:
        pair = self.by_appid.get(appid)
        if pair:
            pair[0].restore_localized(appid, mapping)

    def save(self) -> None:
        for sh in self.shards:
            if sh.dirty:
                sh.save()

    def validate_roundtrip(self) -> None:
        for sh in self.shards:
            if sh._original is not None and sh.build() != sh._original:
                raise ShardError(f"round-trip mismatch in {sh.path.name}")
