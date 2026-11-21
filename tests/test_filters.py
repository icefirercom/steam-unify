from __future__ import annotations

import unittest

from steam_zh import filters


class FilterTests(unittest.TestCase):
    def test_system_appids_always_filtered(self):
        for appid, name in (
            (228980, "Steamworks Common Redistributables"),
            (480, "Spacewar"),
            (250820, "SteamVR"),
            (1493710, "Proton Experimental"),
            (1628350, "Steam Linux Runtime 3.0 (sniper)"),
        ):
            self.assertIsNotNone(
                filters.classify_non_game(appid, name, "Game", include_software=False),
                f"{appid} {name} should be filtered",
            )
            self.assertIsNotNone(
                filters.classify_non_game(appid, name, "Tool", include_software=True),
                f"{appid} {name} should stay filtered even when include_software=True",
            )

    def test_servers_sdks_and_soundtracks_filtered(self):
        samples = [
            (999001, "Valheim Dedicated Server", "Game", []),
            (999002, "PUBG: Test Server", "Game", []),
            (999003, "PUBG: Experimental Server", "Game", []),
            (999004, "Skyrim Creation Kit", "Game", []),
            (999005, "Source SDK Base 2013", "Game", []),
            (999006, "Half-Life - Soundtrack", "Game", []),
            (999007, "Some Game Original Soundtrack", "Game", []),
            (999008, "Monster Hunter Wilds Benchmark", "Game", []),
            (999009, "New Game Playtest", "Game", []),
            (999010, "Some Utility", "Game", [57]),
            (999011, "Wallpaper Engine", "Application", [57]),
            (999012, "Some DLC", "DLC", []),
            (61800, "AMD Driver Updater, Vista and 7, 32 bit", "game", []),
            (1456390, "Gamepad Controller Template", "Game", []),
            (92500, "PC Gamer", "game", []),
            (24920, "Dragon Age: Origins Character Creator", "game", []),
            (317170, "Company of Heroes 2 - Beta", "Game", []),
        ]
        for appid, name, typ, genres in samples:
            self.assertIsNotNone(
                filters.classify_non_game(appid, name, typ, genres, include_software=False),
                f"{name} should be filtered",
            )

    def test_real_games_never_falsely_filtered(self):
        real_games = [
            (367520, "Hollow Knight", "Game", [1, 25, 23]),
            (413150, "Stardew Valley", "Game", [3, 28, 23]),
            (578080, "PUBG: BATTLEGROUNDS", "Game", [1, 25, 37]),
            (1091500, "Cyberpunk 2077", "Game", [3]),
            (239820, "Game Dev Tycoon", "Game", [23, 28, 2]),
            (105600, "Terraria", "Game", [1, 25, 23, 3]),
            (632470, "Disco Elysium - The Final Cut", "Game", [3]),
            (1145360, "Hades", "Game", [1, 23, 3]),
        ]
        for appid, name, typ, genres in real_games:
            self.assertIsNone(
                filters.classify_non_game(appid, name, typ, genres, include_software=False),
                f"real game {name} should NOT be filtered",
            )

    def test_include_software_allows_user_apps(self):
        self.assertIsNone(
            filters.classify_non_game(431960, "Wallpaper Engine", "Application", [57], include_software=True)
        )


if __name__ == "__main__":
    unittest.main()
