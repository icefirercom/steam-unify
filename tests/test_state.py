from __future__ import annotations

import unittest

from steam_zh.metadata import GameInfo
from steam_zh.state import State


class StateTests(unittest.TestCase):
    def test_merge_keeps_original_when_live_name_is_translation(self):
        st = State()
        st.set_app(
            1,
            orig_name="Stardew Valley",
            zh_name="星露谷物语",
            source="dictionary",
            applied=False,
        )
        g = GameInfo(appid=1, name="星露谷物语", type="Game")
        st.merge_games([g])
        self.assertEqual(st.get_app(1)["orig_name"], "Stardew Valley")
        self.assertEqual(st.get_app(1)["zh_name"], "星露谷物语")

    def test_merge_updates_original_when_steam_refreshes(self):
        st = State()
        st.set_app(2, orig_name="Old Name", zh_name="中文名", source="manual", applied=True)
        g = GameInfo(appid=2, name="New English Name", type="Game")
        st.merge_games([g])
        self.assertEqual(st.get_app(2)["orig_name"], "New English Name")


if __name__ == "__main__":
    unittest.main()
