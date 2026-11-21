"""Filter out Steam system tools, runtimes, dedicated servers, and non-game apps.

Steam's local appinfo cache and localconfig.vdf frequently contain system
runtimes (Steamworks Common Redistributables, Proton, Steam Linux Runtime),
SDK test apps (Spacewar 480), dedicated servers, test servers, authoring tools,
and soundtracks—some of which are even tagged with ``type = "Game"`` in appinfo.
"""

from __future__ import annotations

import re
from typing import Iterable

# Core Steam infrastructure / runtimes / SDK test apps that should never be
# treated as user games (even if mislabeled or present in localconfig/acf).
CORE_SYSTEM_APPIDS: frozenset[int] = frozenset({
    7,        # Steam Client
    211,      # Source SDK
    215,      # Source SDK Base 2006
    218,      # Source SDK Base 2007
    480,      # Spacewar (Steamworks SDK example)
    1007,     # Steamworks SDK Redist
    24920,    # Dragon Age: Origins Character Creator
    61800,    # AMD Driver Updater, Vista and 7, 32 bit
    61810,    # AMD Driver Updater, Vista and 7, 64 bit
    61820,    # AMD Driver Updater, XP, 32 bit
    61830,    # AMD Driver Updater, XP, 64 bit
    92500,    # PC Gamer
    221410,   # Steam for Linux
    228980,   # Steamworks Common Redistributables
    243730,   # Source SDK Base 2013 Singleplayer
    243750,   # Source SDK Base 2013 Multiplayer
    250820,   # SteamVR
    858280,   # Proton 3.7
    930400,   # Proton 3.7 Beta
    961940,   # Proton 3.16
    996510,   # Proton 3.16 Beta
    1054830,  # Proton 4.2
    1070560,  # Steam Linux Runtime 1.0 (scout)
    1113280,  # Proton 4.11
    1161040,  # Proton BattlEye Runtime
    1245040,  # Proton 5.0
    1391110,  # Steam Linux Runtime 2.0 (soldier)
    1420170,  # Proton 5.13
    1456390,  # Gamepad Controller Template
    1493710,  # Proton Experimental
    1580130,  # Proton 6.3
    1628350,  # Steam Linux Runtime 3.0 (sniper)
    1826330,  # Proton EasyAntiCheat Runtime
    1887720,  # Proton 7.0
    2180100,  # Proton Hotfix
    2230260,  # SteamVR Performance Test / Runtime
    2348590,  # Proton 8.0
    2805730,  # Proton 9.0
    3658110,  # Proton 10.0 (future-safe placeholder)
})

# Steam non-game software/utility genre IDs (50-60).
SOFTWARE_GENRE_IDS: frozenset[int] = frozenset({
    50,  # Accounting
    51,  # Animation & Modeling
    52,  # Audio Production
    53,  # Design & Illustration
    54,  # Education
    55,  # Game Development
    56,  # Photo Editing
    57,  # Utilities
    58,  # Video Production
    59,  # Web Publishing
    60,  # Software Training
})

# Non-game app types that may appear in appinfo.
NON_GAME_TYPES: frozenset[str] = frozenset({
    "tool",
    "application",
    "dlc",
    "demo",
    "music",
    "video",
    "config",
    "driver",
    "hardware",
    "series",
    "guide",
    "advertising",
    "franchise",
    "beta",
    "media",
    "plugin",
    "episode",
})

# High-precision name patterns for Steam runtimes & system components.
_RUNTIME_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)^steamworks\b.*\bredistributables?\b"), "系统运行库"),
    (re.compile(r"(?i)^steam\s+linux\s+runtime\b"), "系统运行库"),
    (re.compile(r"(?i)^steam\s+(client|controller\s+configs?|overlay|works\s+sdk)\b"), "系统组件"),
    (re.compile(r"(?i)^steamvr\b"), "系统组件"),
    (re.compile(r"(?i)^proton\s+(?:\d|experimental|hotfix|battleye|easyanticheat|next)\b"), "兼容层运行库"),
    (re.compile(r"(?i)\bsource\s+sdk\b"), "开发工具/SDK"),
    (re.compile(r"(?i)\bvulkan\s+shader\b"), "系统组件"),
    (re.compile(r"(?i)\bredistributables?\b"), "系统运行库"),
    (re.compile(r"(?i)\bdriver\s+updater\b"), "驱动程序工具"),
    (re.compile(r"(?i)\bcontroller\s+template\b"), "控制器模板"),
)

# High-precision name patterns for servers, SDKs, dev/mod kits, test builds, and OSTs.
_TOOL_AND_EXTRA_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bdedicated\s+server\b"), "专属服务器"),
    (re.compile(r"(?i)\b(?:test|experimental|staging|beta)\s+server\b"), "测试服务器"),
    (re.compile(r"(?i)\bserver\s+tool\b"), "服务器工具"),
    (re.compile(r"(?i)\b(?:authoring|modding|mod)\s+tools?\b"), "开发/Mod工具"),
    (re.compile(r"(?i)\b(?:creation|mod|dev)\s*kit\b"), "开发/Mod工具"),
    (re.compile(r"(?i)\bsdk\b"), "开发工具/SDK"),
    (re.compile(r"(?i)\bpublic\s+test\s*(?:server|realm|branch|environment)?\b"), "测试服"),
    (re.compile(r"(?i)\bplaytest\s*$"), "测试版本"),
    (re.compile(r"(?i)\bcharacter\s+creator\b"), "角色创建工具"),
    (re.compile(r"(?i)\s[-–—]\s*beta\s*$"), "测试版本"),
    (re.compile(r"(?i)\bbenchmark\s*(?:tool)?\s*$"), "基准测试工具"),
    (re.compile(r"(?i)(?:\boriginal\s+soundtrack\b|(?:\s[-–—]\s|\s)soundtrack\s*$)"), "原声音乐"),
)


def classify_non_game(
    appid: int,
    name: str,
    app_type: str,
    genres: Iterable[int] = (),
    *,
    include_software: bool = False,
    filter_non_games: bool = True,
) -> str | None:
    """Return a Chinese reason string if the app should be filtered out, else None.

    When ``filter_non_games`` is False, only strict ``app_type`` filtering is
    applied (matching legacy behavior).
    """
    typ = (app_type or "").strip().lower()
    wanted_types = {"game"}
    if include_software:
        wanted_types |= {"application", "tool"}

    if typ not in wanted_types:
        return f"非游戏类型({typ or 'unknown'})"

    if not filter_non_games:
        return None

    # Always strip core Steam infrastructure / runtimes, even if include_software=True.
    if appid in CORE_SYSTEM_APPIDS:
        return "Steam 系统组件/运行库"

    clean_name = (name or "").strip()
    for pat, reason in _RUNTIME_PATTERNS:
        if pat.search(clean_name):
            return reason

    if not include_software:
        genre_list = [int(g) for g in genres if str(g).lstrip("-").isdigit()]
        if genre_list and all(g in SOFTWARE_GENRE_IDS for g in genre_list):
            return "软件/实用工具类型"

        for pat, reason in _TOOL_AND_EXTRA_PATTERNS:
            if pat.search(clean_name):
                return reason

    return None


def is_system_or_non_game(
    appid: int,
    name: str,
    app_type: str,
    genres: Iterable[int] = (),
    *,
    include_software: bool = False,
    filter_non_games: bool = True,
) -> bool:
    return (
        classify_non_game(
            appid,
            name,
            app_type,
            genres,
            include_software=include_software,
            filter_non_games=filter_non_games,
        )
        is not None
    )
