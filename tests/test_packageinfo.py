from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from steam_zh import packageinfo


def _encode_bin_vdf_package(pkg_id: int, appids: list[int]) -> bytes:
    buf = bytearray()
    # outer key = str(pkg_id)
    buf.append(packageinfo.T_OBJECT)
    buf += str(pkg_id).encode("utf-8") + b"\x00"
    # appids sub-object
    buf.append(packageinfo.T_OBJECT)
    buf += b"appids\x00"
    for idx, aid in enumerate(appids):
        buf.append(packageinfo.T_INT32)
        buf += str(idx).encode("utf-8") + b"\x00"
        buf += struct.pack("<i", aid)
    buf.append(packageinfo.T_END)  # end appids
    buf.append(packageinfo.T_END)  # end pkg_id
    buf.append(packageinfo.T_END)  # end root
    return bytes(buf)


def _build_packageinfo_bytes(magic: int, packages: dict[int, list[int]]) -> bytes:
    out = bytearray(struct.pack("<II", magic, 1))
    for pkg_id, appids in packages.items():
        vdf = _encode_bin_vdf_package(pkg_id, appids)
        sha = hashlib.sha1(vdf).digest()
        out += struct.pack("<I", pkg_id)
        out += sha
        out += struct.pack("<I", 123456)  # change_number
        if magic >= packageinfo.MAGIC_V40:
            out += struct.pack("<Q", 0)  # pics_token
        out += vdf
    out += struct.pack("<I", packageinfo.FOOTER_EOF)
    return bytes(out)


def _varint(val: int) -> bytes:
    buf = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            buf.append(b | 0x80)
        else:
            buf.append(b)
            break
    return bytes(buf)


def _build_licensecache_bytes(pkg_ids: list[int]) -> bytes:
    out = bytearray()
    # field 1: eresult = 1 (ok)
    out += b"\x08" + _varint(1)
    for pid in pkg_ids:
        # License message: field 1 (varint) = package_id, field 2 (fixed32) = time_created
        lic = b"\x08" + _varint(pid) + b"\x15\x01\x02\x03\x04"
        # repeated License = field 2, wire type 2 -> tag 0x12
        out += b"\x12" + _varint(len(lic)) + lic
    return bytes(out)


class PackageInfoTests(unittest.TestCase):
    def test_parse_v39_and_v40(self):
        with tempfile.TemporaryDirectory() as td:
            for magic in (packageinfo.MAGIC_V39, packageinfo.MAGIC_V40):
                p = Path(td) / f"pkg_{magic:x}.vdf"
                p.write_bytes(
                    _build_packageinfo_bytes(
                        magic,
                        {0: [61800, 92500], 12345: [594570, 1142710], 54321: [1238810]},
                    )
                )
                parsed = packageinfo.parse_packageinfo(p)
                self.assertEqual(parsed[0], {61800, 92500})
                self.assertEqual(parsed[12345], {594570, 1142710})
                self.assertEqual(parsed[54321], {1238810})

    def test_parse_licensecache_and_multi_account_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "appcache").mkdir(parents=True)
            (root / "appcache" / "packageinfo.vdf").write_bytes(
                _build_packageinfo_bytes(
                    packageinfo.MAGIC_V40,
                    {0: [61800], 1001: [594570], 2002: [1142710]},
                )
            )
            acc_cfg = root / "userdata" / "12345678" / "config"
            acc_cfg.mkdir(parents=True)
            (acc_cfg / "licensecache").write_bytes(_build_licensecache_bytes([0, 1001]))

            # Single-account PC: keeps all non-zero packages (excludes Sub 0)
            single = packageinfo.licensed_appids(root, "12345678", active_account_count=1)
            self.assertEqual(single, {594570, 1142710})
            self.assertNotIn(61800, single)

            # Multi-account PC: filters to packages in active user's licensecache (still excludes Sub 0)
            multi = packageinfo.licensed_appids(root, "12345678", active_account_count=2)
            self.assertEqual(multi, {594570})
            self.assertNotIn(61800, multi)
            self.assertNotIn(1142710, multi)


if __name__ == "__main__":
    unittest.main()
