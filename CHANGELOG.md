# Changelog

All notable changes to this project will be documented in this file.

## [2.0.0] - 2026-11-21

Major feature, stability, and usability overhaul based on upstream [moutou232/steam-unify v1.0.0](https://github.com/moutou232/steam-unify).

### Added
- **Complete Steam Library Discovery (`steam_zh/packageinfo.py`)**: Added pure-Python parser for `appcache/packageinfo.vdf` (v39/v40) and `userdata/<account>/config/licensecache` protobuf, discovering never-launched owned games while excluding public Sub `0` packages and supporting multi-account license isolation.
- **System Tool & Non-Game Filtering (`steam_zh/filters.py`)**: Added curated `CORE_SYSTEM_APPIDS` blacklist (Steamworks, Spacewar, SteamVR, Proton/Linux Runtimes, Source SDKs, AMD Driver Updater, etc.) plus regex heuristics for servers, SDKs, dev/mod kits, playtests, soundtracks, and software tools, alongside persistent manual ignore list support.
- **Zero-Dependency Traditional-to-Simplified Engine (`steam_zh/zhconv.py`)**: Converts official Traditional Chinese titles and Steam Store Traditional titles cleanly into Simplified Chinese with gaming-specific terminology mapping.
- **Layered User Dictionary Management (`steam_zh/dictionary.py`)**: Added persistent `data/user_names.json` layered over the bundled dictionary with JSON/CSV import and export.
- **High-Speed Multi-Source Translation & Custom Priority Ordering (`steam_zh/translate.py`, `steam_zh/state.py`)**:
  - Added Wikidata SPARQL batch query (`P1733`, up to 80 AppIDs per request).
  - Added concurrent Steam Store localized name lookup (`schinese` + `tchinese` -> Simplified) that caches store descriptions as a side effect.
  - Added optional OpenAI-compatible LLM batch translation (DeepSeek, SiliconFlow, OpenAI, Ollama presets + live connection test).
  - Added concurrent Google / Youdao / MyMemory MT fallback with edition-suffix preservation and per-provider circuit breakers.
  - Added configurable translation source priority ordering and per-source enable/disable toggles synced across settings UI and translation pipelines.
- **Asynchronous Multi-Source Candidate Lookup & Tabbed Settings (`steam_zh/gui_dialogs.py`)**: Name editor now fetches candidate translations from enabled sources concurrently for side-by-side selection; Settings reorganized into 3 clear tabs (*Steam & Filter*, *Translation & Network*, *AI Translation*).
- **Virtual-Scrolled High-DPI GUI (`steam_zh/ui_widgets.py`, `steam_zh/gui.py`)**: Rebuilt main window with Canvas virtual scrolling, zero-redraw hover feedback, AppID column, colored source pills, single-row workflow toolbar + overflow menu, debounced `Ctrl+F` search, keyboard shortcuts (`Enter`/`F2`/`Space`/`Ctrl+A`/`Esc`), streaming row updates, and dedicated "需核对" / "已忽略" / "已过滤" sidebar views.
- **Release Infrastructure & Tests**: Added `.gitignore`, `.github/workflows/tests.yml`, and comprehensive unit tests (`test_packageinfo.py`, `test_filters.py`, `test_zhconv.py`, `test_dictionary.py`, `test_translate.py`, `test_apply_options.py`, `test_ui_smoke.py`, plus synthetic v29 sentinel verification in `test_appinfo.py`).

### Fixed
- **Critical Steam v29 `appinfo.vdf` Sentinel Bug (`steam_zh/appinfo.py`)**: Preserved and enforced the mandatory 4-byte `0x00000000` entry-list terminator before `string_table_off`, preventing Steam from misreading the string table header as a truncated entry and deleting `appinfo.vdf` on restart; unmodified `build()` is now byte-for-byte identical to native Steam `appinfo.vdf`.
- **Startup Tolerance for Missing `appinfo.vdf` (`steam_zh/gui.py`, `steam_zh/service.py`)**: Allowed the app to start cleanly and run "从备份还原" even when `appinfo.vdf` is missing on disk.
- **System Proxy & Non-Chinese Contamination (`steam_zh/translate.py`, `steam_zh/descriptions.py`)**: Fixed default `ProxyHandler({})` bypassing system proxies; enforced CJK checks and Kana rejection so English or Japanese Kana store titles are never mistakenly accepted as Chinese translations.
- **Apply Hardening & Performance (`steam_zh/apply.py`, `steam_zh/steamenv.py`)**: Added independent `apply_names` / `apply_collections` toggles, cached `installed_apps()` calls out of loops, hardened file verification retries, and cached `steam_running()` checks in UI updates.
- **Build Script Isolation (`build_exe.ps1`)**: Restricted PyInstaller `--add-data` to static dictionary/model JSON files only, preventing accidental bundling of local `settings.json` / `state.json`.

### Changed
- Excluded ignored and system/non-game entries from both name translation and Steam collection syncing.
- Updated `__version__` to `"2.0.0"` and created a dedicated `.gitignore` to keep local `data/settings.json`, `data/state.json`, `data/descriptions.json`, `data/user_names.json`, and `backups/` untracked.

---

## [1.0.0]

- Initial upstream release from [moutou232/steam-unify](https://github.com/moutou232/steam-unify/releases/tag/v1.0.0).
