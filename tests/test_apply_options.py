from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from steam_zh import apply, service
from steam_zh.metadata import GameInfo
from steam_zh.state import State


class ApplyOptionsTests(unittest.TestCase):
    def test_plan_names_and_collections_exclude_ignored_and_system_apps(self):
        st = State()
        st.set_app(413150, orig_name="Stardew Valley", zh_name="星露谷物语", categories=["模拟经营"])
        st.set_app(999999, orig_name="Ignored Game", zh_name="被忽略的游戏", categories=["动作"])
        st.set_app(228980, orig_name="Steamworks Common Redistributables", zh_name="运行库", categories=["其他"])
        st.ignore_app(999999, name="Ignored Game")

        games = [
            GameInfo(appid=413150, name="Stardew Valley", type="Game"),
            GameInfo(appid=999999, name="Ignored Game", type="Game"),
            GameInfo(appid=228980, name="Steamworks Common Redistributables", type="Game"),
        ]

        planned, names = apply._plan_names(st, games, selected=None)
        self.assertEqual([g.appid for g in planned], [413150])
        self.assertNotIn(999999, names)
        self.assertNotIn(228980, names)

        computed = apply._build_computed_collections(st, games, selected=None)
        self.assertEqual(computed, {"模拟经营": [413150]})
        self.assertNotIn("动作", computed)
        self.assertNotIn("其他", computed)

    def test_counts_tracks_wikidata_llm_and_ignored(self):
        st = State()
        st.set_app(1, zh_name="维基译名", source="wikidata")
        st.set_app(2, zh_name="大模型译名", source="llm")
        st.set_app(3, zh_name="", source="")
        st.set_app(4, zh_name="忽略项", source="manual")
        st.ignore_app(4, name="Ignored")
        games = [
            GameInfo(appid=1, name="A", type="Game"),
            GameInfo(appid=2, name="B", type="Game"),
            GameInfo(appid=3, name="C", type="Game"),
            GameInfo(appid=4, name="D", type="Game"),
        ]
        ctx = SimpleNamespace(state=st, games=games)
        c = service.counts(ctx)
        self.assertEqual(c["total"], 3)
        self.assertEqual(c["wikidata"], 1)
        self.assertEqual(c["llm"], 1)
        self.assertEqual(c["missing"], 1)
        self.assertEqual(c["ignored"], 1)

    def test_verify_respects_flags_and_tolerates_transient_read_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = SimpleNamespace(
                has_shards=False,
                appinfo=root / "appinfo.vdf",
                shard_dir=root / "appinfo",
                namespace_file=root / "ns.json",
            )
            st = State()
            st.set_app(413150, zh_name="星露谷物语")
            games = [GameInfo(appid=413150, name="Stardew Valley", type="Game")]

            # Skips both when apply_names=False and apply_collections=False
            with mock.patch("steam_zh.steamenv.steam_running", return_value=True):
                res = apply.verify(env, st, games, wait=0, apply_names=False, apply_collections=False)
            self.assertTrue(res.collections_verified)
            self.assertEqual(res.warnings, [])

            # Tolerates repeated PermissionError when reading appinfo after Steam restart
            with mock.patch("steam_zh.steamenv.steam_running", return_value=True), \
                 mock.patch("steam_zh.appinfo.AppInfo.load", side_effect=PermissionError("locked")), \
                 mock.patch("time.sleep", return_value=None):
                res2 = apply.verify(env, st, games, wait=0, apply_names=True, apply_collections=False)
            self.assertTrue(res2.collections_verified)
            self.assertTrue(any("校验读取 appinfo 失败" in w for w in res2.warnings))


if __name__ == "__main__":
    unittest.main()
