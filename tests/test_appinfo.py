from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

from steam_zh import appinfo, steamenv


class AppInfoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            root = steamenv.detect_root()
            cls.src = steamenv.SteamEnv(root, steamenv.detect_account(root)).appinfo
        except steamenv.SteamError as exc:
            raise unittest.SkipTest(f"Steam not available: {exc}")
        if not cls.src.is_file():
            # Newer Steam clients use shards; fall back to a monolithic file
            # preserved in our own backups for the v29 tests.
            from steam_zh.apply import BACKUP_ROOT

            candidates = sorted(BACKUP_ROOT.glob("*/appcache/appinfo.vdf"), reverse=True)
            if candidates:
                cls.src = candidates[0]
        if not cls.src.is_file():
            raise unittest.SkipTest("no monolithic appinfo.vdf for v29 tests")
        cls.tmp = Path(tempfile.mkdtemp(prefix="appinfo-test-"))

    def setUp(self):
        self.copy = Path(tempfile.mkdtemp(prefix="appinfo-case-", dir=self.tmp)) / "appinfo.vdf"
        self.copy.write_bytes(self.src.read_bytes())
        self.ai = appinfo.AppInfo.load(self.copy)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            import shutil

            shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_roundtrip_all_entries(self):
        for e in self.ai.entries:
            self.assertEqual(
                appinfo.serialize_node(e.root) + e.tail,
                e.raw_vdf,
                f"app {e.appid}",
            )

    def test_build_is_stable(self):
        data = self.ai.build()
        self.assertEqual(data, self.copy.read_bytes())
        magic, universe, stroff = struct.unpack_from("<IIQ", data, 0)
        self.assertEqual(magic, appinfo.MAGIC_V29)
        self.assertEqual(data[stroff - 4 : stroff], b"\x00\x00\x00\x00")
        again = appinfo.AppInfo.from_bytes(data, self.copy)
        self.assertEqual(len(self.ai.entries), len(again.entries))
        self.assertEqual([e.appid for e in self.ai.entries], [e.appid for e in again.entries])

    def test_rename_and_remove_localized(self):
        target = None
        for e in self.ai.entries:
            if self.ai.get_localized(e.appid):
                target = e
                break
        if target is None:
            self.skipTest("no localized entry")
        old_name = self.ai.get_name(target.appid)
        old_loc = self.ai.get_localized(target.appid)
        self.ai.remove_localized(target.appid)
        self.ai.set_name(target.appid, "中文测试名称")
        data = self.ai.build()
        parsed = appinfo.AppInfo.from_bytes(data, self.copy)
        self.assertEqual(parsed.get_name(target.appid), "中文测试名称")
        self.assertIsNone(parsed.get_localized(target.appid))
        # Restore the original localized mapping and ensure it serializes.
        parsed.restore_localized(target.appid, old_loc)
        parsed.set_name(target.appid, old_name)
        again = appinfo.AppInfo.from_bytes(parsed.build(), self.copy)
        self.assertEqual(again.get_name(target.appid), old_name)
        self.assertEqual(again.get_localized(target.appid), old_loc)

    def test_hashes_and_sizes(self):
        target = next(e for e in self.ai.entries if self.ai.get_name(e.appid))
        self.ai.set_name(target.appid, "哈希测试")
        data = self.ai.build()
        magic, universe, stroff = struct.unpack_from("<IIQ", data, 0)
        self.assertEqual(magic, appinfo.MAGIC_V29)
        self.assertLess(stroff, len(data))
        parsed = appinfo.AppInfo.from_bytes(data, self.copy)
        e = parsed.entry(target.appid)
        self.assertEqual(e.size, 60 + len(e.raw_vdf))
        import hashlib

        self.assertEqual(e.vdf_sha1, hashlib.sha1(e.raw_vdf).digest())


def _build_synthetic_v29_appinfo(appid: int = 1091500, name: str = "Cyberpunk 2077") -> bytes:
    """Construct a minimal valid Steam v29 appinfo.vdf blob in memory for CI tests."""
    import hashlib

    keys = ["appinfo", "appid", "common", "name"]
    string_table = struct.pack("<I", len(keys)) + b"".join(k.encode("utf-8") + b"\x00" for k in keys)

    root = appinfo.Node(
        appinfo.T_OBJECT,
        0,  # "appinfo"
        [
            appinfo.Node(appinfo.T_INT32, 1, appid),  # "appid"
            appinfo.Node(
                appinfo.T_OBJECT,
                2,  # "common"
                [appinfo.Node(appinfo.T_STRING, 3, name.encode("utf-8"))],  # "name"
            ),
        ],
    )
    raw_vdf = appinfo.serialize_node(root) + b"\x08"
    size = appinfo.ENTRY_META_SIZE + len(raw_vdf)
    vdf_sha1 = hashlib.sha1(raw_vdf).digest()
    app_sha1 = b"\x11" * 20
    token = b"\x00" * 8
    entry_blob = (
        struct.pack("<IIII", appid, size, 2, 1700000000)
        + token
        + app_sha1
        + struct.pack("<I", 100)
        + vdf_sha1
        + raw_vdf
    )
    trailer = b"\x00\x00\x00\x00"
    stroff = appinfo.HEADER_SIZE + len(entry_blob) + len(trailer)
    header = struct.pack("<IIQ", appinfo.MAGIC_V29, 1, stroff)
    return header + entry_blob + trailer + string_table


class AppInfoV29SyntheticTests(unittest.TestCase):
    def test_synthetic_v29_preserves_4byte_sentinel_and_updates_sha1(self):
        import hashlib

        raw = _build_synthetic_v29_appinfo(1091500, "Cyberpunk 2077")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "appinfo.vdf"
            p.write_bytes(raw)

            ai = appinfo.AppInfo.load(p)
            self.assertEqual(ai.get_name(1091500), "Cyberpunk 2077")

            rebuilt = ai.build()
            self.assertEqual(rebuilt, raw)
            _, _, stroff = struct.unpack_from("<IIQ", rebuilt, 0)
            self.assertEqual(rebuilt[stroff - 4 : stroff], b"\x00\x00\x00\x00")

            ai.set_name(1091500, "赛博朋克 2077")
            updated = ai.build()
            _, _, stroff2 = struct.unpack_from("<IIQ", updated, 0)
            self.assertEqual(updated[stroff2 - 4 : stroff2], b"\x00\x00\x00\x00")

            reloaded = appinfo.AppInfo.from_bytes(updated, p)
            self.assertEqual(reloaded.get_name(1091500), "赛博朋克 2077")
            entry = reloaded.entry(1091500)
            self.assertEqual(entry.size, appinfo.ENTRY_META_SIZE + len(entry.raw_vdf))
            self.assertEqual(entry.vdf_sha1, hashlib.sha1(entry.raw_vdf).digest())


if __name__ == "__main__":
    unittest.main()
