from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from steam_zh import dictionary, service
from steam_zh.metadata import GameInfo
from steam_zh.state import State


class DictionaryTests(unittest.TestCase):
    def test_json_and_csv_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch("steam_zh.paths.data_dir", return_value=root):
                # Export JSON
                json_out = root / "export.json"
                n = dictionary.export_file(json_out, {"413150": "星露谷物语", "367520": "空洞骑士"})
                self.assertEqual(n, 2)

                # Import JSON into user_names.json
                cnt, entries = dictionary.import_file(json_out, merge_into_user=True)
                self.assertEqual(cnt, 2)
                self.assertEqual(entries["367520"], "空洞骑士")
                self.assertEqual(dictionary.load_user()["413150"], "星露谷物语")

                # Export CSV and re-import
                csv_out = root / "export.csv"
                n2 = dictionary.export_file(
                    csv_out,
                    {"105600": "泰拉瑞亚"},
                    orig_names={"105600": "Terraria"},
                )
                self.assertEqual(n2, 1)
                cnt2, entries2 = dictionary.import_file(csv_out, merge_into_user=True)
                self.assertEqual(cnt2, 1)
                self.assertEqual(entries2["105600"], "泰拉瑞亚")
                # Previous entries preserved on merge
                merged_user = dictionary.load_user()
                self.assertEqual(merged_user["413150"], "星露谷物语")
                self.assertEqual(merged_user["105600"], "泰拉瑞亚")

    def test_list_json_format_import(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "list.json"
            p.write_text(
                json.dumps([{"appid": 504230, "中文名": "蔚蓝"}], ensure_ascii=False),
                encoding="utf-8",
            )
            cnt, entries = dictionary.import_file(p, merge_into_user=False)
            self.assertEqual(cnt, 1)
            self.assertEqual(entries["504230"], "蔚蓝")

    def test_service_import_applies_to_non_locked_games(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "dict.json"
            p.write_text(
                json.dumps({"1": "译名一", "2": "译名二"}, ensure_ascii=False),
                encoding="utf-8",
            )
            st = State()
            st.set_app(1, orig_name="Game One", zh_name="", source="", locked=False)
            st.set_app(2, orig_name="Game Two", zh_name="手动锁定的名字", source="manual", locked=True)
            games = [
                GameInfo(appid=1, name="Game One", type="Game"),
                GameInfo(appid=2, name="Game Two", type="Game"),
            ]
            ctx = SimpleNamespace(state=st, games=games)
            with mock.patch("steam_zh.paths.data_dir", return_value=root), \
                 mock.patch.object(State, "save", return_value=None):
                total, matched = service.import_dictionary(ctx, p, apply_to_state=True)
            self.assertEqual(total, 2)
            self.assertEqual(matched, 1)
            self.assertEqual(st.get_app(1)["zh_name"], "译名一")
            self.assertEqual(st.get_app(2)["zh_name"], "手动锁定的名字")


if __name__ == "__main__":
    unittest.main()
