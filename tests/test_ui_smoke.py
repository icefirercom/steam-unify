from __future__ import annotations

import tkinter as tk
import unittest

from types import SimpleNamespace
from unittest import mock

from steam_zh import gui, gui_dialogs, ui_widgets
from steam_zh.metadata import GameInfo
from steam_zh.state import State


class UiSmokeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")

    def tearDown(self):
        if hasattr(self, "root"):
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    def test_virtual_scrolled_table_renders_only_viewport(self):
        table = ui_widgets.TagTable(self.root)
        table.pack(fill="both", expand=True)
        self.root.geometry("1200x700")
        self.root.update_idletasks()

        rows = [
            ui_widgets.TableRow(
                appid=1000 + i,
                zh=f"游戏名称 {i}",
                orig=f"Game Title {i}",
                categories=["动作", "角色扮演"],
                source="维基" if i % 2 == 0 else "AI",
                status="可应用",
                installed=(i % 10 == 0),
                locked=(i == 0),
            )
            for i in range(2000)
        ]
        table.set_rows(rows)
        self.root.update_idletasks()

        lo, hi = table._rendered_range
        rendered_count = hi - lo
        self.assertGreater(rendered_count, 0)
        # Must NOT draw all 2,000 rows; only visible viewport + buffer!
        self.assertLess(rendered_count, 60)

        # Checked rows take precedence over selected in active_ids()
        table.selected = {1000}
        self.assertEqual(table.active_ids(), {1000})
        table.checked = {1001, 1002}
        self.assertEqual(table.active_ids(), {1001, 1002})

        # Responsive columns expand beyond minimum width
        table._compute_columns(1200)
        self.assertGreaterEqual(table.total_width, 1180)
        self.assertIn("appid", table.x_offsets)
        self.assertIn("source", table.x_offsets)

        # Single-row in-place update works without error
        rows[0].zh = "新译名"
        table.update_row(rows[0])

    def test_dialogs_and_sidebar_sync_smoke(self):
        st = State()
        st.set_app(367520, orig_name="Hollow Knight", zh_name="空洞骑士", source="wikidata")
        game = GameInfo(appid=367520, name="Hollow Knight", type="Game", installed=True, owned=True)
        fake_env = SimpleNamespace(persona="Tester", account="12345", has_shards=True)
        fake_ctx = SimpleNamespace(
            env=fake_env, state=st, games=[game], new_appids=set(), warnings=[], filtered_items=[]
        )

        with mock.patch("steam_zh.gui.SteamZhApp.reload", return_value=None), \
             mock.patch("steam_zh.steamenv.steam_running", return_value=False):
            app = gui.SteamZhApp(self.root)
            app.ctx = fake_ctx
            app.games_by_id = {367520: game}
            app.refresh_sidebar()
            app.refresh_table()
            app.update_status()
            self.root.update_idletasks()

            # Verify sidebar switch from "installed" to "translated" resets installed_var!
            app.sidebar.selection_set("installed")
            app._on_sidebar()
            self.assertTrue(app.installed_var.get())
            app.sidebar.selection_set("translated")
            app._on_sidebar()
            self.assertFalse(app.installed_var.get())

            # Instantiate dialogs cleanly
            dlg1 = gui_dialogs.NameEditDialog(app, game, st.get_app(367520))
            self.root.update_idletasks()
            dlg1.destroy()

            dlg2 = gui_dialogs.ApplyDialog(
                app, all_count=1, selected_ids={367520}, on_confirm=lambda **_kw: None
            )
            self.root.update_idletasks()
            dlg2.destroy()

            with mock.patch("steam_zh.steamenv.detect_root", side_effect=OSError("no steam")):
                dlg3 = gui_dialogs.SettingsDialog(app)
                self.root.update_idletasks()
                # Reorder and toggle source priority list
                orig_first = dlg3.source_order[0]
                orig_second = dlg3.source_order[1]
                dlg3.source_list.selection_clear(0, "end")
                dlg3.source_list.selection_set(1)
                dlg3._move_source(-1)
                self.assertEqual(dlg3.source_order[0], orig_second)
                self.assertEqual(dlg3.source_order[1], orig_first)
                dlg3.source_list.selection_clear(0, "end")
                dlg3.source_list.selection_set(0)
                dlg3._toggle_source()
                self.assertIn(orig_second, dlg3.disabled_sources)
                dlg3._reset_sources()
                self.assertEqual(dlg3.source_order[0], orig_first)
                dlg3.destroy()


if __name__ == "__main__":
    unittest.main()
