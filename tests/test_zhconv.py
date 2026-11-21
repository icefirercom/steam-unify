from __future__ import annotations

import unittest

from steam_zh import zhconv


class ZhConvTests(unittest.TestCase):
    def test_char_pairs_are_single_chars(self):
        for trad, simp in zhconv._CHAR_PAIRS:
            self.assertEqual(len(trad), 1, f"bad trad char: {trad!r}")
            self.assertEqual(len(simp), 1, f"bad simp char: {simp!r}")

    def test_gaming_phrases_and_titles(self):
        self.assertEqual(zhconv.to_simplified("惡靈古堡2 重製版"), "生化危机2 重制版")
        self.assertEqual(zhconv.to_simplified("魔物獵人：崛起"), "怪物猎人：崛起")
        self.assertEqual(zhconv.to_simplified("戴森球計畫"), "戴森球计划")
        self.assertEqual(zhconv.to_simplified("命運石之門"), "命运石之门")
        self.assertEqual(zhconv.to_simplified("最後生還者：重製版"), "最后生还者：重制版")
        self.assertEqual(zhconv.to_simplified("最終幻想7：重製版"), "最终幻想7：重制版")
        self.assertEqual(zhconv.to_simplified("龍與地下城：終極版"), "龙与地下城：终极版")

    def test_empty_and_ascii_passthrough(self):
        self.assertEqual(zhconv.to_simplified(""), "")
        self.assertEqual(zhconv.to_simplified("Hollow Knight"), "Hollow Knight")


if __name__ == "__main__":
    unittest.main()
