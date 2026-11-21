from __future__ import annotations

import json
import unittest
from unittest import mock

from steam_zh import translate
from steam_zh.metadata import GameInfo
from steam_zh.state import State


class TranslateTests(unittest.TestCase):
    def test_is_chinese_name_rejects_english_and_japanese_kana(self):
        self.assertTrue(translate.is_chinese_name("空洞骑士"))
        self.assertTrue(translate.is_chinese_name("赛博朋克 2077"))
        self.assertFalse(translate.is_chinese_name("Celeste"))
        self.assertFalse(translate.is_chinese_name("Hades II"))
        self.assertFalse(translate.is_chinese_name("魔法使いの夜"))
        self.assertFalse(translate.is_chinese_name("ダンガンロンパ"))

    def test_tchinese_local_conversion(self):
        g = GameInfo(
            appid=883710,
            name="RESIDENT EVIL 2",
            type="Game",
            localized={"tchinese": "惡靈古堡2 重製版"},
        )
        tr = translate._resolve_local(g, {}, {})
        self.assertIsNotNone(tr)
        self.assertEqual(tr.name, "生化危机2 重制版")
        self.assertEqual(tr.source, "official")

    def test_steam_store_ignores_english_and_caches_description(self):
        def fake_http(url, *args, **kwargs):
            if "l=schinese" in url:
                return {
                    "504230": {
                        "success": True,
                        "data": {
                            "name": "Celeste",
                            "short_description": "<p>Climb Celeste Mountain.</p>",
                        },
                    }
                }
            if "l=tchinese" in url:
                return {
                    "504230": {
                        "success": True,
                        "data": {"name": "Celeste"},
                    }
                }
            return {}

        with mock.patch.object(translate, "http_json", side_effect=fake_http):
            zh, _detail, desc, ok = translate.steam_store_details(504230)
        self.assertIsNone(zh)  # English title MUST NOT be accepted as Chinese!
        self.assertTrue(ok)
        self.assertIn("Climb Celeste Mountain", desc)

    def test_steam_store_falls_back_to_tchinese_and_converts(self):
        def fake_http(url, *args, **kwargs):
            if "l=schinese" in url:
                return {"100": {"success": True, "data": {"name": "Monster Hunter: Rise"}}}
            if "l=tchinese" in url:
                return {"100": {"success": True, "data": {"name": "魔物獵人：崛起"}}}
            return {}

        with mock.patch.object(translate, "http_json", side_effect=fake_http):
            zh, detail, _desc, ok = translate.steam_store_details(100)
        self.assertTrue(ok)
        self.assertEqual(zh, "怪物猎人：崛起")
        self.assertIn("繁", detail)

    def test_wikidata_batch_parsing_and_disambiguation(self):
        fake_sparql = {
            "results": {
                "bindings": [
                    {
                        "appid": {"value": "504230"},
                        "zhcn": {"value": "蔚蓝 (电子游戏)"},
                    },
                    {
                        "appid": {"value": "367520"},
                        "zhwiki": {"value": "空洞騎士 (電子遊戲)"},
                    },
                ]
            }
        }
        with mock.patch.object(translate, "http_json", return_value=fake_sparql):
            res, ok = translate.wikidata_batch([504230, 367520])
        self.assertTrue(ok)
        self.assertEqual(res[504230], "蔚蓝")
        self.assertEqual(res[367520], "空洞骑士")

    def test_llm_batch_handles_think_tags_and_fences(self):
        fake_resp = {
            "choices": [
                {
                    "message": {
                        "content": '<think>reasoning...</think>\n```json\n{"504230": "蔚藍", "1145350": "哈迪斯2"}\n```'
                    }
                }
            ]
        }
        games = [
            GameInfo(appid=504230, name="Celeste", type="Game"),
            GameInfo(appid=1145350, name="Hades II", type="Game"),
        ]
        with mock.patch.object(translate, "http_json", return_value=fake_resp):
            res, err = translate.llm_translate_batch(
                games, base_url="http://127.0.0.1:11434/v1", api_key="", model="qwen2.5:7b"
            )
        self.assertEqual(err, "")
        self.assertEqual(res[504230], "蔚蓝")
        self.assertEqual(res[1145350], "哈迪斯2")

    def test_edition_suffix_preserved_in_mt(self):
        base, suffix = translate._split_edition_suffix("Divinity: Original Sin 2 - Definitive Edition")
        self.assertEqual(base, "Divinity: Original Sin 2")
        self.assertEqual(suffix, "决定版")
        self.assertEqual(translate._attach_edition_suffix("神界：原罪2", suffix), "神界：原罪2 决定版")
        self.assertEqual(translate._attach_edition_suffix("黑暗之魂", "重制版"), "黑暗之魂：重制版")

    def test_staged_pipeline_skips_ignored_and_heals_legacy_english_steam(self):
        st = State()
        # Legacy broken record: source="steam" but name is English!
        st.set_app(504230, orig_name="Celeste", zh_name="Celeste", source="steam", locked=False)
        st.ignore_app(999, name="Ignored Tool")
        games = [
            GameInfo(appid=504230, name="Celeste", type="Game"),
            GameInfo(appid=1145350, name="Hades II", type="Game"),
            GameInfo(appid=999, name="Ignored Tool", type="Game"),
        ]
        streamed = []

        opts = translate.TranslateOptions(
            use_network=True,
            enable_wikidata=True,
            enable_steam_store=False,
            llm_enabled=True,
            llm_base_url="http://localhost:11434/v1",
            llm_model="test-model",
            enable_free_mt=False,
        )
        with mock.patch.object(State, "save", return_value=None), \
             mock.patch("steam_zh.dictionary.load_merged", return_value=({}, {})), \
             mock.patch.object(translate, "wikidata_batch", return_value=({504230: "蔚蓝"}, True)), \
             mock.patch.object(translate, "llm_translate_batch", return_value=({1145350: "哈迪斯2"}, "")):
            status = translate.translate_games(
                games,
                st,
                options=opts,
                on_item=lambda aid, _rec: streamed.append(aid),
            )

        self.assertEqual(status[504230], "wikidata")
        self.assertEqual(st.get_app(504230)["zh_name"], "蔚蓝")
        self.assertEqual(status[1145350], "llm")
        self.assertEqual(st.get_app(1145350)["zh_name"], "哈迪斯2")
        self.assertEqual(status[999], "ignored")
        self.assertNotIn(999, streamed)
        self.assertIn(504230, streamed)
        self.assertIn(1145350, streamed)

    def test_system_proxy_opener_behavior(self):
        with mock.patch(
            "urllib.request.getproxies",
            return_value={"http": "http://127.0.0.1:10809", "https": "http://127.0.0.1:10809"},
        ):
            op_sys = translate._opener(None, use_system_proxy=True)
            op_direct = translate._opener(None, use_system_proxy=False)
            op_custom = translate._opener("http://127.0.0.1:7890", use_system_proxy=False)

        sys_proxies = [h.proxies for h in op_sys.handlers if hasattr(h, "proxies")]
        self.assertTrue(any(p.get("https") == "http://127.0.0.1:10809" for p in sys_proxies))

        direct_proxies = [h.proxies for h in op_direct.handlers if hasattr(h, "proxies")]
        self.assertFalse(any(p for p in direct_proxies))

        custom_proxies = [h.proxies for h in op_custom.handlers if hasattr(h, "proxies")]
        self.assertTrue(any(p.get("http") == "http://127.0.0.1:7890" for p in custom_proxies))

    def test_custom_source_priority_order_and_disable(self):
        st = State()
        games = [GameInfo(appid=413150, name="Stardew Valley", type="Game")]

        # Prioritize LLM ahead of bundled_dict / steam_store
        opts = translate.TranslateOptions(
            use_network=True,
            llm_enabled=True,
            llm_base_url="http://localhost:11434/v1",
            llm_model="test-model",
            source_order=["llm", "bundled_dict", "steam_store"],
            disabled_sources={"wikidata", "youdao", "mymemory", "google"},
        )
        with mock.patch.object(State, "save", return_value=None), \
             mock.patch("steam_zh.dictionary.load_merged", return_value=({}, {"413150": "星露谷词典名"})), \
             mock.patch.object(translate, "llm_translate_batch", return_value=({413150: "星露谷物语 AI"}, "")):
            status = translate.translate_games(games, st, options=opts)

        self.assertEqual(status[413150], "llm")
        self.assertEqual(st.get_app(413150)["zh_name"], "星露谷物语 AI")

        # Test youdao prioritized ahead of google inside MT pool
        st2 = State()
        opts2 = translate.TranslateOptions(
            use_network=True,
            source_order=["youdao", "google"],
            disabled_sources={"user_dict", "official", "bundled_dict", "wikidata", "steam_store", "llm", "mymemory"},
        )
        call_order = []
        def fake_youdao(name, **kw):
            call_order.append("youdao")
            return ("星露谷有道", True)
        def fake_google(name, **kw):
            call_order.append("google")
            return ("星露谷谷歌", True)
        with mock.patch.object(State, "save", return_value=None), \
             mock.patch("steam_zh.dictionary.load_merged", return_value=({}, {})), \
             mock.patch.object(translate, "youdao_translate", side_effect=fake_youdao), \
             mock.patch.object(translate, "google_translate", side_effect=fake_google):
            status2 = translate.translate_games(games, st2, options=opts2)

        self.assertEqual(status2[413150], "machine")
        self.assertEqual(st2.get_app(413150)["zh_name"], "星露谷有道")
        self.assertEqual(call_order, ["youdao"])

    def test_lookup_candidates_respects_custom_priority_order(self):
        game = GameInfo(appid=413150, name="Stardew Valley", type="Game")
        settings = {
            "use_network": True,
            "llm_enabled": True,
            "llm_base_url": "http://localhost:11434/v1",
            "llm_model": "test-model",
            "source_order": ["youdao", "llm", "bundled_dict"],
            "disabled_sources": ["user_dict", "official", "wikidata", "steam_store", "google", "mymemory"],
        }
        with mock.patch("steam_zh.dictionary.load_merged", return_value=({}, {"413150": "词典名"})), \
             mock.patch.object(translate, "youdao_translate", return_value=("有道名", True)), \
             mock.patch.object(translate, "llm_translate_batch", return_value=({413150: "AI名"}, "")):
            cands = translate.lookup_candidates(game, settings)
        self.assertEqual([c.name for c in cands], ["有道名", "AI名", "词典名"])


if __name__ == "__main__":
    unittest.main()
