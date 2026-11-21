"""High-speed multi-source Chinese game name translation engine.

Layered resolution pipeline (fastest & most authoritative first):
  0. Local (0ms): already Simplified Chinese -> Steam appinfo schinese ->
     appinfo tchinese (Traditional->Simplified) -> user dictionary -> bundled dictionary
  1. Wikidata SPARQL batch query (by Steam AppID P1733, ~80 games/request)
  2. Concurrent Steam Store API (schinese + tchinese->Simplified, also caches descriptions)
  3. Optional OpenAI-compatible LLM batch translation (DeepSeek / OpenAI / Ollama)
  4. Concurrent free multi-engine MT fallback (Google -> Youdao -> MyMemory)
     with edition-suffix normalization and per-provider circuit breakers.
"""

from __future__ import annotations

import concurrent.futures
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from . import descriptions, dictionary as dict_mod, zhconv
from .metadata import GameInfo
from .state import DEFAULT_SOURCE_ORDER, State, now_ts

SOURCE_META: dict[str, tuple[str, str, bool]] = {
    "user_dict": ("用户词典", "用户自定义词典 (data/user_names.json)", False),
    "official": ("Steam 内置", "Steam 本地 appinfo 官简 / 官繁转简", False),
    "bundled_dict": ("内置词典", "程序内置游戏词典 (official_names.json)", False),
    "wikidata": ("维基百科", "Wikidata 批量查询标准中文名 (80款/批)", True),
    "steam_store": ("Steam 商店", "Steam 商店 API 简/繁转简 + 顺带缓存简介", True),
    "llm": ("AI 大模型", "OpenAI 兼容大模型批量翻译 (DeepSeek/Ollama)", True),
    "google": ("Google 机翻", "Google 免费翻译 (自动识别英/日/韩等语种)", True),
    "youdao": ("有道机翻", "有道免费翻译 (国内直连可用)", True),
    "mymemory": ("MyMemory", "MyMemory 免费机翻兜底", True),
}

MT_SOURCES: frozenset[str] = frozenset({"google", "youdao", "mymemory"})


def normalize_source_order(order: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in order or []:
        sid = str(item).strip()
        if sid in SOURCE_META and sid not in seen:
            seen.add(sid)
            out.append(sid)
    for sid in DEFAULT_SOURCE_ORDER:
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out

USER_AGENT = (
    "SteamUnify/2.0 (+https://github.com/icefirercom/steam-unify; local library helper)"
)
CJK_RE = re.compile(r"[\u3400-\u9fff]")
KANA_RE = re.compile(r"[\u3040-\u30ff]")
NAME_STRIP_RE = re.compile(
    r"^[\s\"'“”‘’《》〈〉「」『』\[\]()（）]+|[\s\"'“”‘’《》〈〉「」『』\[\]（）]+$"
)
WIKI_DISAMBIG_RE = re.compile(
    r"\s*[\(（]\s*(?:电子游戏|電子遊戲|视频游戏|視頻遊戲|游戏|遊戲|PC游戏|电脑游戏|\d{4}年(?:电子)?游戏)\s*[\)）]\s*$"
)
THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
TM_RE = re.compile(r"[™®©]")

EDITION_SUFFIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\s:：\-–—]+Game\s+of\s+the\s+Year\s+Edition\s*$", re.I), "年度版"),
    (re.compile(r"[\s:：\-–—]+GOTY\s+Edition\s*$", re.I), "年度版"),
    (re.compile(r"[\s:：\-–—]+Definitive\s+Edition\s*$", re.I), "决定版"),
    (re.compile(r"[\s:：\-–—]+Director'?s\s+Cut\s*$", re.I), "导演剪辑版"),
    (re.compile(r"[\s:：\-–—]+The\s+Final\s+Cut\s*$", re.I), "最终剪辑版"),
    (re.compile(r"[\s:：\-–—]+Anniversary\s+Edition\s*$", re.I), "周年版"),
    (re.compile(r"[\s:：\-–—]+Complete\s+Edition\s*$", re.I), "完整版"),
    (re.compile(r"[\s:：\-–—]+Ultimate\s+Edition\s*$", re.I), "终极版"),
    (re.compile(r"[\s:：\-–—]+Enhanced\s+Edition\s*$", re.I), "增强版"),
    (re.compile(r"[\s:：\-–—]+Special\s+Edition\s*$", re.I), "特别版"),
    (re.compile(r"[\s:：\-–—]+Deluxe\s+Edition\s*$", re.I), "豪华版"),
    (re.compile(r"[\s:：\-–—]+Royal\s+Edition\s*$", re.I), "皇家版"),
    (re.compile(r"[\s:：\-–—]+Gold\s+Edition\s*$", re.I), "黄金版"),
    (re.compile(r"[\s:：\-–—]+HD\s+Remaster(?:ed)?\s*$", re.I), "高清重制版"),
    (re.compile(r"[\s:：\-–—]+Remastered\s*$", re.I), "重制版"),
    (re.compile(r"[\s:：\-–—]+Remake\s*$", re.I), "重制版"),
)


@dataclass
class Translation:
    name: str
    source: str
    detail: str = ""


@dataclass
class TranslateOptions:
    use_network: bool = True
    proxy: str | None = None
    use_system_proxy: bool = True
    concurrency: int = 6
    enable_wikidata: bool = True
    enable_steam_store: bool = True
    enable_free_mt: bool = True
    llm_enabled: bool = False
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_batch_size: int = 30
    source_order: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCE_ORDER))
    disabled_sources: set[str] = field(default_factory=set)

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None) -> "TranslateOptions":
        s = settings or {}
        proxy = str(s.get("proxy") or "").strip() or None
        order = normalize_source_order(s.get("source_order"))
        disabled = {str(x).strip() for x in (s.get("disabled_sources") or []) if str(x).strip() in SOURCE_META}
        enable_wikidata = bool(s.get("enable_wikidata", True))
        enable_steam_store = bool(s.get("enable_steam_store", True))
        enable_free_mt = bool(s.get("enable_free_mt", True))
        llm_enabled = bool(s.get("llm_enabled", False))
        if not enable_wikidata:
            disabled.add("wikidata")
        if not enable_steam_store:
            disabled.add("steam_store")
        if not enable_free_mt:
            disabled.update(MT_SOURCES)
        if not llm_enabled:
            disabled.add("llm")
        return cls(
            use_network=bool(s.get("use_network", True)),
            proxy=proxy,
            use_system_proxy=bool(s.get("use_system_proxy", True)),
            concurrency=max(1, min(int(s.get("concurrency", 6) or 6), 12)),
            enable_wikidata=enable_wikidata,
            enable_steam_store=enable_steam_store,
            enable_free_mt=enable_free_mt,
            llm_enabled=llm_enabled,
            llm_base_url=str(s.get("llm_base_url") or "https://api.deepseek.com/v1").strip(),
            llm_api_key=str(s.get("llm_api_key") or "").strip(),
            llm_model=str(s.get("llm_model") or "deepseek-chat").strip(),
            llm_batch_size=max(5, min(int(s.get("llm_batch_size", 30) or 30), 60)),
            source_order=order,
            disabled_sources=disabled,
        )

    def effective_sources(self, *, allow_network: bool | None = None) -> list[str]:
        net_ok = self.use_network if allow_network is None else bool(allow_network)
        disabled = set(self.disabled_sources)
        if not self.enable_wikidata:
            disabled.add("wikidata")
        if not self.enable_steam_store:
            disabled.add("steam_store")
        if not self.enable_free_mt:
            disabled.update(MT_SOURCES)
        if not (self.llm_enabled and self.llm_base_url and self.llm_model):
            disabled.add("llm")

        out: list[str] = []
        for sid in normalize_source_order(self.source_order):
            if sid in disabled:
                continue
            if not net_ok and SOURCE_META[sid][2]:
                continue
            out.append(sid)
        return out


def load_dictionary() -> dict[str, str]:
    """Return merged bundled + user dictionaries (user overrides bundled)."""
    user, bundled = dict_mod.load_merged()
    merged = dict(bundled)
    merged.update(user)
    return merged


def clean_text(text: str) -> str:
    text = html.unescape(str(text or "")).strip()
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = TM_RE.sub("", text)
    text = re.sub(r"^\s*\(\s*\d+\s*\)\s*", "", text)
    text = WIKI_DISAMBIG_RE.sub("", text)
    text = NAME_STRIP_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


def _has_kana(text: str) -> bool:
    return bool(KANA_RE.search(text or ""))


def is_chinese_name(text: str) -> bool:
    """True when text contains CJK Hanzi and is not Japanese Kana text."""
    return _has_cjk(text) and not _has_kana(text)


def _opener(proxy: str | None = None, use_system_proxy: bool = True):
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        return urllib.request.build_opener(handler)
    if use_system_proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(
    url: str,
    proxy: str | None = None,
    timeout: float = 10.0,
    *,
    use_system_proxy: bool = True,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
) -> Any:
    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    }
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    opener = _opener(proxy, use_system_proxy=use_system_proxy)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    return json.loads(text)


# ------------------------------------------------------------------ Wikidata
def wikidata_batch(
    appids: list[int],
    proxy: str | None = None,
    *,
    use_system_proxy: bool = True,
    timeout: float = 12.0,
) -> tuple[dict[int, str], bool]:
    """Batch-resolve Steam AppIDs -> Simplified Chinese names via Wikidata P1733.

    Returns ``({appid: zh_name}, network_ok)``.
    """
    if not appids:
        return {}, True
    values = " ".join(f'"{int(aid)}"' for aid in appids)
    query = (
        "SELECT ?appid ?zhcn ?zhhans ?zh ?zhtw ?zhhk ?zhwiki WHERE {\n"
        f"  VALUES ?appid {{ {values} }}\n"
        "  ?item wdt:P1733 ?appid .\n"
        '  OPTIONAL { ?item rdfs:label ?zhcn FILTER(LANG(?zhcn) = "zh-cn") }\n'
        '  OPTIONAL { ?item rdfs:label ?zhhans FILTER(LANG(?zhhans) = "zh-hans") }\n'
        '  OPTIONAL { ?item rdfs:label ?zh FILTER(LANG(?zh) = "zh") }\n'
        '  OPTIONAL { ?item rdfs:label ?zhtw FILTER(LANG(?zhtw) = "zh-tw") }\n'
        '  OPTIONAL { ?item rdfs:label ?zhhk FILTER(LANG(?zhhk) = "zh-hk") }\n'
        "  OPTIONAL {\n"
        "    ?article schema:about ?item ;\n"
        "             schema:isPartOf <https://zh.wikipedia.org/> ;\n"
        "             schema:name ?zhwiki .\n"
        "  }\n"
        "}"
    )
    body = urllib.parse.urlencode({"query": query}).encode("utf-8")
    try:
        data = http_json(
            "https://query.wikidata.org/sparql",
            proxy=proxy,
            timeout=timeout,
            use_system_proxy=use_system_proxy,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/sparql-results+json",
            },
            data=body,
            method="POST",
        )
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return {}, False

    bindings = data.get("results", {}).get("bindings", []) if isinstance(data, dict) else []
    out: dict[int, str] = {}
    for row in bindings:
        if not isinstance(row, dict):
            continue
        aid_raw = row.get("appid", {}).get("value", "")
        if not str(aid_raw).isdigit():
            continue
        aid = int(aid_raw)
        if aid in out:
            continue
        # Priority: zh-cn > zh-hans > zh > zhwiki > zh-tw > zh-hk
        for field, convert in (
            ("zhcn", False),
            ("zhhans", False),
            ("zh", True),
            ("zhwiki", True),
            ("zhtw", True),
            ("zhhk", True),
        ):
            val = row.get(field, {}).get("value", "")
            if not val:
                continue
            cleaned = clean_text(val)
            if convert:
                cleaned = zhconv.to_simplified(cleaned)
            if cleaned and is_chinese_name(cleaned):
                out[aid] = cleaned
                break
    return out, True


# --------------------------------------------------------------- Steam Store
def _steam_appdetails(
    base: str,
    appid: int,
    lang: str,
    proxy: str | None = None,
    use_system_proxy: bool = True,
    timeout: float = 6.5,
) -> tuple[dict[str, Any] | None, bool]:
    url = f"{base}/api/appdetails?appids={appid}&cc=cn&l={lang}"
    try:
        data = http_json(url, proxy=proxy, timeout=timeout, use_system_proxy=use_system_proxy)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None, False
    item = data.get(str(appid)) if isinstance(data, dict) else None
    if not isinstance(item, dict) or not item.get("success"):
        return None, True
    info = item.get("data")
    return (info if isinstance(info, dict) else None), True


def steam_store_details(
    appid: int,
    proxy: str | None = None,
    *,
    use_system_proxy: bool = True,
    try_tchinese: bool = True,
) -> tuple[str | None, str, str | None, bool]:
    """Query Steam Store for a verified Chinese name and short description.

    Returns ``(zh_name, detail, description_text, network_ok)``.
    English-only store titles are NOT returned as Chinese names.
    """
    info, ok = _steam_appdetails(
        "https://store.steampowered.com",
        appid,
        "schinese",
        proxy=proxy,
        use_system_proxy=use_system_proxy,
    )
    desc: str | None = None
    if info:
        raw_desc = info.get("short_description") or info.get("detailed_description") or ""
        if isinstance(raw_desc, str) and raw_desc.strip():
            desc = raw_desc
        name = clean_text(str(info.get("name") or ""))
        if name and is_chinese_name(name):
            return name, "Steam 商店官方简体中文名", desc, True

        # Store reachable, but schinese returned non-Chinese (e.g. English).
        # Try tchinese and convert Traditional -> Simplified.
        if try_tchinese:
            tc_info, tc_ok = _steam_appdetails(
                "https://store.steampowered.com",
                appid,
                "tchinese",
                proxy=proxy,
                use_system_proxy=use_system_proxy,
            )
            if tc_info:
                tc_name = clean_text(str(tc_info.get("name") or ""))
                if tc_name and is_chinese_name(tc_name):
                    simp = zhconv.to_simplified(tc_name)
                    if is_chinese_name(simp):
                        return simp, "Steam 商店繁体中文转简体", desc, True
            return None, "", desc, tc_ok or ok
        return None, "", desc, True

    if not ok:
        # Steam global store unreachable; try Steam China as fallback.
        cn_info, cn_ok = _steam_appdetails(
            "https://store.steamchina.com",
            appid,
            "schinese",
            proxy=proxy,
            use_system_proxy=use_system_proxy,
        )
        if cn_info:
            raw_desc = cn_info.get("short_description") or ""
            if isinstance(raw_desc, str) and raw_desc.strip():
                desc = raw_desc
            name = clean_text(str(cn_info.get("name") or ""))
            if name and is_chinese_name(name):
                return name, "蒸汽平台官方简体中文名", desc, True
        return None, "", desc, cn_ok

    return None, "", desc, True


def steam_store(
    appid: int,
    proxy: str | None = None,
    *,
    use_system_proxy: bool = True,
) -> str | None:
    zh, _detail, _desc, _ok = steam_store_details(
        appid, proxy=proxy, use_system_proxy=use_system_proxy
    )
    return zh


def steam_china(
    appid: int,
    proxy: str | None = None,
    *,
    use_system_proxy: bool = True,
) -> str | None:
    info, _ok = _steam_appdetails(
        "https://store.steamchina.com",
        appid,
        "schinese",
        proxy=proxy,
        use_system_proxy=use_system_proxy,
    )
    if not info:
        return None
    name = clean_text(str(info.get("name") or ""))
    return name if (name and is_chinese_name(name)) else None


# --------------------------------------------------------------- Free MT
def _split_edition_suffix(name: str) -> tuple[str, str]:
    text = TM_RE.sub("", str(name or "")).strip()
    for pat, zh_suffix in EDITION_SUFFIXES:
        m = pat.search(text)
        if m:
            base = text[: m.start()].strip(" :-–—")
            if base:
                return base, zh_suffix
    return text, ""


def _attach_edition_suffix(zh_base: str, zh_suffix: str) -> str:
    if not zh_suffix:
        return zh_base
    if zh_suffix in zh_base:
        return zh_base
    sep = "：" if ("：" not in zh_base and ":" not in zh_base) else " "
    return f"{zh_base}{sep}{zh_suffix}"


def google_translate(
    name: str,
    proxy: str | None = None,
    *,
    use_system_proxy: bool = True,
    timeout: float = 7.0,
) -> tuple[str | None, bool]:
    """Translate via Google Translate web endpoint (auto-detects EN/JA/KO/etc.)."""
    base, suffix = _split_edition_suffix(name)
    query = urllib.parse.quote(base)
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=auto&tl=zh-CN&dt=t&q={query}"
    )
    try:
        data = http_json(url, proxy=proxy, timeout=timeout, use_system_proxy=use_system_proxy)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None, False
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        return None, True
    parts = [
        str(seg[0])
        for seg in data[0]
        if isinstance(seg, list) and seg and isinstance(seg[0], str)
    ]
    cleaned = clean_text("".join(parts))
    if not cleaned or not is_chinese_name(cleaned) or cleaned.lower() == base.lower():
        return None, True
    return _attach_edition_suffix(cleaned, suffix), True


def youdao_translate(
    name: str,
    proxy: str | None = None,
    *,
    use_system_proxy: bool = True,
    timeout: float = 6.0,
) -> tuple[str | None, bool]:
    """Translate via Youdao public endpoint (works directly inside mainland China)."""
    base, suffix = _split_edition_suffix(name)
    query = urllib.parse.quote(base)
    url = f"https://fanyi.youdao.com/translate?&doctype=json&type=AUTO&i={query}"
    try:
        data = http_json(url, proxy=proxy, timeout=timeout, use_system_proxy=use_system_proxy)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None, False
    if not isinstance(data, dict) or data.get("errorCode") not in (0, "0", None):
        return None, False
    result = data.get("translateResult")
    if not isinstance(result, list):
        return None, True
    parts: list[str] = []
    for para in result:
        if isinstance(para, list):
            for seg in para:
                if isinstance(seg, dict) and isinstance(seg.get("tgt"), str):
                    parts.append(seg["tgt"])
    cleaned = clean_text("".join(parts))
    if not cleaned or not is_chinese_name(cleaned) or cleaned.lower() == base.lower():
        return None, True
    return _attach_edition_suffix(cleaned, suffix), True


def mymemory(
    name: str,
    proxy: str | None = None,
    *,
    use_system_proxy: bool = True,
    timeout: float = 8.0,
) -> str | None:
    zh, _ok = _mymemory_ex(name, proxy=proxy, use_system_proxy=use_system_proxy, timeout=timeout)
    return zh


def _mymemory_ex(
    name: str,
    proxy: str | None = None,
    *,
    use_system_proxy: bool = True,
    timeout: float = 8.0,
) -> tuple[str | None, bool]:
    base, suffix = _split_edition_suffix(name)
    query = urllib.parse.quote(base)
    url = f"https://api.mymemory.translated.net/get?q={query}&langpair=en|zh-CN"
    try:
        data = http_json(url, proxy=proxy, timeout=timeout, use_system_proxy=use_system_proxy)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None, False
    try:
        text = data.get("responseData", {}).get("translatedText", "")
    except AttributeError:
        return None, False
    cleaned = clean_text(text)
    if not cleaned or "MYMEMORY WARNING" in cleaned.upper():
        return None, False
    if not is_chinese_name(cleaned) or cleaned.lower() == base.lower():
        return None, True
    return _attach_edition_suffix(cleaned, suffix), True


# --------------------------------------------------------------- LLM Batch
def _llm_endpoint(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def _extract_json_object(content: str) -> dict[str, Any]:
    text = THINK_TAG_RE.sub("", str(content or "")).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def llm_translate_batch(
    games: list[GameInfo],
    *,
    base_url: str,
    api_key: str,
    model: str,
    proxy: str | None = None,
    use_system_proxy: bool = True,
    timeout: float = 45.0,
) -> tuple[dict[int, str], str]:
    """Batch-translate games using an OpenAI-compatible chat completions API.

    Returns ``({appid: zh_name}, error_message)``.
    """
    endpoint = _llm_endpoint(base_url)
    if not endpoint or not model:
        return {}, "未配置大模型 API 地址或模型名称"
    if not games:
        return {}, ""

    lines = []
    for g in games:
        orig = (g.original_name or g.name or "").strip()
        tags = ", ".join(g.tag_names[:5])
        if tags:
            lines.append(f"{g.appid} | {orig} | 标签: {tags}")
        else:
            lines.append(f"{g.appid} | {orig}")

    system_prompt = (
        "你是资深电子游戏本地化专家。请将给定的 Steam 游戏原名翻译为国内玩家最常用的简体中文正式或社区公认译名。\n"
        "规则：\n"
        "1. 优先使用官方简体中文名或国内玩家公认译名（例如 Hollow Knight -> 空洞骑士，Celeste -> 蔚蓝，Hades II -> 哈迪斯2）。\n"
        "2. 常见版本后缀规范翻译（Definitive Edition -> 决定版，Remastered -> 重制版，Complete Edition -> 完整版，GOTY -> 年度版）。\n"
        "3. 如果原名是专有缩写或极少数惯例保留英文的，仍请尽量给出公认中文名或“中文名 + 缩写”。\n"
        "4. 只返回一个严格合法的 JSON 对象，键为输入里的 AppID 字符串，值为简体中文游戏名，不要输出任何额外解释。"
    )
    user_prompt = "请翻译以下游戏名并返回 JSON 对象：\n" + "\n".join(lines)

    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        resp = http_json(
            endpoint,
            proxy=proxy,
            timeout=timeout,
            use_system_proxy=use_system_proxy,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
        )
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        return {}, f"HTTP {exc.code}: {detail or exc.reason}"
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {}, str(exc)

    try:
        content = resp["choices"][0]["message"]["content"]
        mapping = _extract_json_object(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        return {}, f"模型返回格式解析失败: {exc}"

    allowed_ids = {g.appid for g in games}
    out: dict[int, str] = {}
    for k, v in mapping.items():
        ks = str(k).strip()
        if not ks.isdigit():
            continue
        aid = int(ks)
        if aid not in allowed_ids or not isinstance(v, str):
            continue
        cleaned = zhconv.to_simplified(clean_text(v))
        if cleaned and is_chinese_name(cleaned):
            out[aid] = cleaned
    return out, ""


def probe_llm(
    base_url: str,
    api_key: str,
    model: str,
    proxy: str | None = None,
    *,
    use_system_proxy: bool = True,
) -> tuple[bool, str, int]:
    """Test an OpenAI-compatible LLM endpoint with a single iconic game."""
    sample = GameInfo(appid=367520, name="Hollow Knight", type="Game", tag_names=["类银河战士恶魔城", "动作"])
    t0 = time.perf_counter()
    res, err = llm_translate_batch(
        [sample],
        base_url=base_url,
        api_key=api_key,
        model=model,
        proxy=proxy,
        use_system_proxy=use_system_proxy,
        timeout=20.0,
    )
    ms = int((time.perf_counter() - t0) * 1000)
    if err:
        return False, err, ms
    got = res.get(367520)
    if got:
        return True, f"Hollow Knight -> {got}", ms
    return False, "模型返回成功但未解析到有效中文译名", ms


# ------------------------------------------------------------------ Probing
def probe(
    proxy: str | None = None,
    *,
    use_system_proxy: bool = True,
) -> dict[str, bool]:
    """Concurrently probe which online translation sources are reachable."""
    checks: dict[str, Callable[[], bool]] = {
        "wikidata": lambda: bool(
            wikidata_batch([413150], proxy=proxy, use_system_proxy=use_system_proxy, timeout=8.0)[0]
        ),
        "steam": lambda: bool(
            steam_store(413150, proxy=proxy, use_system_proxy=use_system_proxy)
        ),
        "google": lambda: bool(
            google_translate("Stardew Valley", proxy=proxy, use_system_proxy=use_system_proxy, timeout=6.0)[0]
        ),
        "youdao": lambda: bool(
            youdao_translate("Stardew Valley", proxy=proxy, use_system_proxy=use_system_proxy, timeout=6.0)[0]
        ),
        "mymemory": lambda: bool(
            mymemory("Stardew Valley", proxy=proxy, use_system_proxy=use_system_proxy, timeout=7.0)
        ),
    }
    result = {k: False for k in checks}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(checks)) as ex:
        futs = {ex.submit(fn): name for name, fn in checks.items()}
        for fut in concurrent.futures.as_completed(futs):
            name = futs[fut]
            try:
                result[name] = bool(fut.result())
            except Exception:
                result[name] = False
    return result


# --------------------------------------------------------------- Resolution
def _resolve_from_local_source(
    sid: str,
    game: GameInfo,
    user_dict: dict[str, str],
    bundled_dict: dict[str, str],
) -> Translation | None:
    if sid == "user_dict":
        user_hit = user_dict.get(str(game.appid))
        if user_hit:
            return Translation(user_hit, "dictionary", "用户词典")
        return None
    if sid == "official":
        official = game.official_zh
        if official:
            cleaned = clean_text(official)
            if cleaned and is_chinese_name(cleaned):
                return Translation(cleaned, "official", "Steam 内置简体中文名")
        tchinese = game.official_tchinese
        if tchinese:
            cleaned = zhconv.to_simplified(clean_text(tchinese))
            if cleaned and is_chinese_name(cleaned):
                return Translation(cleaned, "official", "Steam 内置繁体转简体")
        return None
    if sid == "bundled_dict":
        dict_name = bundled_dict.get(str(game.appid))
        if dict_name:
            return Translation(dict_name, "dictionary", "内置词典")
        return None
    return None


def _resolve_local(
    game: GameInfo,
    user_dict: dict[str, str],
    bundled_dict: dict[str, str],
    sources: list[str] | None = None,
) -> Translation | None:
    order = sources if sources is not None else list(DEFAULT_SOURCE_ORDER)
    checked_already = False
    for sid in order:
        if sid == "user_dict":
            hit = _resolve_from_local_source(sid, game, user_dict, bundled_dict)
            if hit:
                return hit
            if is_chinese_name(game.name):
                return Translation(game.name, "already", "原名已是中文")
            checked_already = True
        elif sid in ("official", "bundled_dict"):
            if not checked_already and is_chinese_name(game.name):
                return Translation(game.name, "already", "原名已是中文")
            checked_already = True
            hit = _resolve_from_local_source(sid, game, user_dict, bundled_dict)
            if hit:
                return hit
    if not checked_already and is_chinese_name(game.name):
        return Translation(game.name, "already", "原名已是中文")
    return None


def _needs_translation(rec: dict[str, Any], *, force: bool = False) -> bool:
    if rec.get("locked") and rec.get("zh_name") and not force:
        return False
    if force:
        return True
    zh = str(rec.get("zh_name") or "").strip()
    src = str(rec.get("source") or "")
    if not zh:
        return True
    # Heal broken legacy records where Steam Store returned an English title
    # and was mistakenly stored as source="steam".
    if src == "steam" and not is_chinese_name(zh):
        return True
    if src in ("official", "dictionary", "wikidata", "steam", "llm", "manual", "already", "original"):
        return False
    return False


def _resolve_mt_single(
    sid: str,
    orig: str,
    opts: TranslateOptions,
) -> tuple[Translation | None, bool]:
    if sid == "google":
        zh, ok = google_translate(orig, proxy=opts.proxy, use_system_proxy=opts.use_system_proxy)
        return (Translation(zh, "machine", "Google 机翻") if zh else None), ok
    if sid == "youdao":
        zh, ok = youdao_translate(orig, proxy=opts.proxy, use_system_proxy=opts.use_system_proxy)
        return (Translation(zh, "machine", "有道机翻") if zh else None), ok
    if sid == "mymemory":
        zh, ok = _mymemory_ex(orig, proxy=opts.proxy, use_system_proxy=opts.use_system_proxy)
        return (Translation(zh, "machine", "MyMemory 机翻") if zh else None), ok
    return None, True


def translate_one(
    game: GameInfo,
    state: State,
    dictionary: dict[str, str],
    use_network: bool,
    proxy: str | None = None,
    delay: float = 0.0,
    *,
    use_system_proxy: bool = True,
    options: TranslateOptions | None = None,
    force: bool = False,
) -> Translation | None:
    """Resolve a single game's Simplified Chinese name following configured source order."""
    rec = state.get_app(game.appid)
    if not _needs_translation(rec, force=force):
        return None

    opts = options or TranslateOptions(
        use_network=use_network,
        proxy=proxy,
        use_system_proxy=use_system_proxy,
    )
    if not use_network:
        opts.use_network = False
    if proxy:
        opts.proxy = proxy

    user_dict, bundled_dict = dict_mod.load_merged()
    if dictionary:
        merged_bundled = dict(bundled_dict)
        merged_bundled.update(dictionary)
    else:
        merged_bundled = bundled_dict

    order = opts.effective_sources()
    if "user_dict" in order:
        hit = _resolve_from_local_source("user_dict", game, user_dict, merged_bundled)
        if hit:
            return hit
    if is_chinese_name(game.name):
        return Translation(game.name, "already", "原名已是中文")

    orig = game.original_name or game.name
    for sid in order:
        if sid in ("official", "bundled_dict"):
            hit = _resolve_from_local_source(sid, game, user_dict, merged_bundled)
            if hit:
                return hit
        elif sid == "wikidata":
            wd, _ok = wikidata_batch(
                [game.appid], proxy=opts.proxy, use_system_proxy=opts.use_system_proxy
            )
            if game.appid in wd:
                return Translation(wd[game.appid], "wikidata", "Wikidata / 维基百科译名")
        elif sid == "steam_store":
            zh, detail, desc, _ok = steam_store_details(
                game.appid, proxy=opts.proxy, use_system_proxy=opts.use_system_proxy
            )
            if desc and not game.description:
                game.description = descriptions._clean(desc)
                descriptions.merge_cache({str(game.appid): desc})
            if zh:
                return Translation(zh, "steam", detail)
        elif sid == "llm":
            res, _err = llm_translate_batch(
                [game],
                base_url=opts.llm_base_url,
                api_key=opts.llm_api_key,
                model=opts.llm_model,
                proxy=opts.proxy,
                use_system_proxy=opts.use_system_proxy,
            )
            if game.appid in res:
                return Translation(res[game.appid], "llm", f"AI 翻译 ({opts.llm_model})")
        elif sid in MT_SOURCES:
            if delay:
                time.sleep(delay)
            tr, _ok = _resolve_mt_single(sid, orig, opts)
            if tr:
                return tr

    return None


def lookup_candidates(
    game: GameInfo,
    settings: dict[str, Any] | None = None,
) -> list[Translation]:
    """Concurrently query all enabled sources and return candidates in configured priority order."""
    opts = TranslateOptions.from_settings(settings)
    user_dict, bundled_dict = dict_mod.load_merged()
    order = opts.effective_sources()

    local_hits: dict[str, Translation] = {}
    for sid in order:
        if sid in ("user_dict", "official", "bundled_dict"):
            hit = _resolve_from_local_source(sid, game, user_dict, bundled_dict)
            if hit:
                local_hits[sid] = hit

    orig = game.original_name or game.name
    tasks: dict[str, Callable[[], Translation | None]] = {}
    if opts.use_network:
        for sid in order:
            if sid == "wikidata":
                tasks[sid] = lambda: (
                    lambda m: Translation(m[game.appid], "wikidata", "维基百科") if game.appid in m else None
                )(wikidata_batch([game.appid], proxy=opts.proxy, use_system_proxy=opts.use_system_proxy, timeout=8.0)[0])
            elif sid == "steam_store":
                def _st():
                    zh, detail, _desc, _ = steam_store_details(
                        game.appid, proxy=opts.proxy, use_system_proxy=opts.use_system_proxy
                    )
                    return Translation(zh, "steam", detail) if zh else None
                tasks[sid] = _st
            elif sid == "llm":
                def _llm():
                    m, _ = llm_translate_batch(
                        [game],
                        base_url=opts.llm_base_url,
                        api_key=opts.llm_api_key,
                        model=opts.llm_model,
                        proxy=opts.proxy,
                        use_system_proxy=opts.use_system_proxy,
                        timeout=20.0,
                    )
                    return Translation(m[game.appid], "llm", f"AI ({opts.llm_model})") if game.appid in m else None
                tasks[sid] = _llm
            elif sid in MT_SOURCES:
                tasks[sid] = (lambda s=sid: _resolve_mt_single(s, orig, opts)[0])

    net_results: dict[str, Translation | None] = {}
    if tasks:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as ex:
            futs = {ex.submit(fn): key for key, fn in tasks.items()}
            for fut in concurrent.futures.as_completed(futs):
                key = futs[fut]
                try:
                    net_results[key] = fut.result()
                except Exception:
                    net_results[key] = None

    ordered: list[Translation] = []
    already_added = False
    for sid in order:
        if sid in local_hits:
            ordered.append(local_hits[sid])
        elif sid in net_results and net_results[sid]:
            ordered.append(net_results[sid])  # type: ignore[arg-type]
        if sid == "user_dict" and not already_added and is_chinese_name(game.name):
            ordered.append(Translation(game.name, "already", "原名已是中文"))
            already_added = True
    if not already_added and is_chinese_name(game.name):
        ordered.append(Translation(game.name, "already", "原名已是中文"))

    seen: set[str] = set()
    unique: list[Translation] = []
    for tr in ordered:
        norm = tr.name.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            unique.append(tr)
    return unique


def translate_games(
    games: list[GameInfo],
    state: State,
    use_network: bool = True,
    proxy: str | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    *,
    options: TranslateOptions | dict[str, Any] | None = None,
    force: bool = False,
    on_item: Callable[[int, dict[str, Any]], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[int, str]:
    """High-speed staged + concurrent translation pipeline honoring custom source order.

    Returns ``{appid: source_or_status}``.
    """
    if isinstance(options, dict):
        opts = TranslateOptions.from_settings(options)
    elif isinstance(options, TranslateOptions):
        opts = options
    else:
        opts = TranslateOptions(use_network=use_network, proxy=proxy)

    if not use_network:
        opts.use_network = False
    if proxy:
        opts.proxy = proxy

    user_dict, bundled_dict = dict_mod.load_merged()
    status: dict[int, str] = {}
    state_lock = threading.Lock()
    dirty_count = 0

    def _apply_tr(game: GameInfo, tr: Translation) -> None:
        nonlocal dirty_count
        with state_lock:
            rec = state.get_app(game.appid)
            rec["zh_name"] = tr.name
            rec["source"] = tr.source
            rec["detail"] = tr.detail
            rec["error"] = ""
            rec["translated_at"] = now_ts()
            status[game.appid] = tr.source
            dirty_count += 1
            if dirty_count >= 20:
                state.save()
                dirty_count = 0
        if on_item:
            on_item(game.appid, rec)

    def _flush_state() -> None:
        nonlocal dirty_count
        with state_lock:
            if dirty_count > 0:
                state.save()
                dirty_count = 0

    def _stopped() -> bool:
        return bool(should_stop and should_stop())

    # Filter out ignored and already-settled entries up front.
    pending: list[GameInfo] = []
    for game in games:
        if state.is_ignored(game.appid):
            status[game.appid] = "ignored"
            continue
        rec = state.get_app(game.appid)
        if not _needs_translation(rec, force=force):
            status[game.appid] = str(rec.get("source") or "skipped")
            continue
        pending.append(game)

    if not pending:
        return status

    order = opts.effective_sources()

    # Build ordered execution steps; consecutive MT engines share one concurrent pool.
    steps: list[tuple[str, list[str]]] = []
    for sid in order:
        if sid in MT_SOURCES:
            if steps and steps[-1][0] == "mt":
                steps[-1][1].append(sid)
            else:
                steps.append(("mt", [sid]))
        else:
            steps.append((sid, [sid]))

    # Ensure already-Chinese original names are resolved before any network call
    # (while still letting an earlier user_dict/official/bundled_dict match first).
    inserted_already = False
    expanded_steps: list[tuple[str, list[str]]] = []
    for kind, sids in steps:
        if not inserted_already and SOURCE_META[sids[0]][2]:
            expanded_steps.append(("already", []))
            inserted_already = True
        expanded_steps.append((kind, sids))
    if not inserted_already:
        expanded_steps.append(("already", []))

    total_est = len(pending)
    done_est = 0

    def _report(msg: str) -> None:
        if progress:
            progress(done_est, total_est, msg)

    for kind, sids in expanded_steps:
        if not pending or _stopped():
            break

        if kind in ("user_dict", "official", "bundled_dict"):
            next_pending: list[GameInfo] = []
            for g in pending:
                tr = _resolve_from_local_source(kind, g, user_dict, bundled_dict)
                if tr:
                    _apply_tr(g, tr)
                    done_est += 1
                else:
                    next_pending.append(g)
            pending = next_pending
            _flush_state()
            continue

        if kind == "already":
            next_pending = []
            for g in pending:
                if is_chinese_name(g.name):
                    _apply_tr(g, Translation(g.name, "already", "原名已是中文"))
                    done_est += 1
                else:
                    next_pending.append(g)
            pending = next_pending
            _flush_state()
            continue

        if kind == "wikidata":
            chunk_size = 80
            next_pending = []
            wd_hits = 0
            wd_failed = False
            for i in range(0, len(pending), chunk_size):
                if _stopped():
                    next_pending.extend(pending[i:])
                    break
                chunk = pending[i : i + chunk_size]
                if wd_failed:
                    next_pending.extend(chunk)
                    continue
                _report(f"Wikidata 批量查询 ({i + 1}-{i + len(chunk)}/{len(pending)})")
                mapping, ok = wikidata_batch(
                    [g.appid for g in chunk],
                    proxy=opts.proxy,
                    use_system_proxy=opts.use_system_proxy,
                )
                if not ok:
                    wd_failed = True
                    if log:
                        log("Wikidata 暂不可达，自动跳过并切换下一翻译源")
                    next_pending.extend(chunk)
                    continue
                for g in chunk:
                    zh = mapping.get(g.appid)
                    if zh:
                        _apply_tr(g, Translation(zh, "wikidata", "Wikidata / 维基百科译名"))
                        wd_hits += 1
                        done_est += 1
                    else:
                        next_pending.append(g)
            pending = next_pending
            _flush_state()
            if log and wd_hits:
                log(f"Wikidata 批量命中 {wd_hits} 个标准中文名")
            continue

        if kind == "steam_store":
            collected_descs: dict[str, str] = {}
            next_pending = []
            store_hits = 0
            consecutive_net_err = 0
            trip_store = False
            err_lock = threading.Lock()
            workers = max(1, min(opts.concurrency, 8))

            def _store_worker(game: GameInfo) -> tuple[GameInfo, Translation | None, str | None, bool]:
                if trip_store or _stopped():
                    return game, None, None, True
                zh, detail, desc, ok = steam_store_details(
                    game.appid, proxy=opts.proxy, use_system_proxy=opts.use_system_proxy
                )
                tr = Translation(zh, "steam", detail) if zh else None
                return game, tr, desc, ok

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_store_worker, g): g for g in pending}
                for fut in concurrent.futures.as_completed(futs):
                    g = futs[fut]
                    try:
                        game, tr, desc, ok = fut.result()
                    except Exception:
                        game, tr, desc, ok = g, None, None, False
                    if desc:
                        cleaned_desc = descriptions._clean(desc)
                        if cleaned_desc:
                            collected_descs[str(game.appid)] = cleaned_desc
                            if not game.description:
                                game.description = cleaned_desc
                                game.description_source = "steam"
                    if tr:
                        _apply_tr(game, tr)
                        store_hits += 1
                        done_est += 1
                        with err_lock:
                            consecutive_net_err = 0
                        _report(f"Steam 商店 ({done_est}/{total_est}): {tr.name}")
                    else:
                        next_pending.append(game)
                        with err_lock:
                            if not ok:
                                consecutive_net_err += 1
                                if consecutive_net_err >= 6 and not trip_store:
                                    trip_store = True
                                    if log:
                                        log("Steam 商店连续连接失败，已熔断并切换下一翻译源")
                            else:
                                consecutive_net_err = 0
                    if trip_store or _stopped():
                        for p in futs:
                            p.cancel()
                        break

            seen_ids = {g.appid for g in next_pending}
            for g in pending:
                if g.appid not in status and g.appid not in seen_ids:
                    next_pending.append(g)
            pending = next_pending
            _flush_state()
            if collected_descs:
                added_d = descriptions.merge_cache(collected_descs)
                if log and added_d:
                    log(f"同步缓存 {added_d} 条 Steam 商店简介")
            if log and store_hits:
                log(f"Steam 商店命中 {store_hits} 个官方中文名")
            continue

        if kind == "llm":
            next_pending = []
            llm_hits = 0
            llm_failed = False
            bsize = max(5, min(opts.llm_batch_size, 60))
            for i in range(0, len(pending), bsize):
                if _stopped():
                    next_pending.extend(pending[i:])
                    break
                chunk = pending[i : i + bsize]
                if llm_failed:
                    next_pending.extend(chunk)
                    continue
                _report(f"AI 批量翻译 ({i + 1}-{i + len(chunk)}/{len(pending)})")
                mapping, err = llm_translate_batch(
                    chunk,
                    base_url=opts.llm_base_url,
                    api_key=opts.llm_api_key,
                    model=opts.llm_model,
                    proxy=opts.proxy,
                    use_system_proxy=opts.use_system_proxy,
                )
                if err:
                    llm_failed = True
                    if log:
                        log(f"AI 翻译出错（已切换下一源）：{err}")
                    next_pending.extend(chunk)
                    continue
                for g in chunk:
                    zh = mapping.get(g.appid)
                    if zh:
                        _apply_tr(g, Translation(zh, "llm", f"AI 翻译 ({opts.llm_model})"))
                        llm_hits += 1
                        done_est += 1
                    else:
                        next_pending.append(g)
            pending = next_pending
            _flush_state()
            if log and llm_hits:
                log(f"AI 批量翻译完成 {llm_hits} 个游戏")
            continue

        if kind == "mt":
            mt_hits = 0
            breaker_lock = threading.Lock()
            fail_counts = {s: 0 for s in sids}
            tripped = {s: False for s in sids}
            workers = max(1, min(opts.concurrency, 6))

            def _mt_worker(game: GameInfo) -> tuple[GameInfo, Translation | None]:
                if _stopped():
                    return game, None
                orig = game.original_name or game.name
                for eng in sids:
                    if _stopped():
                        return game, None
                    with breaker_lock:
                        if tripped[eng]:
                            continue
                    tr, ok = _resolve_mt_single(eng, orig, opts)
                    if tr:
                        with breaker_lock:
                            fail_counts[eng] = 0
                        return game, tr
                    if not ok:
                        with breaker_lock:
                            fail_counts[eng] += 1
                            if fail_counts[eng] >= 5 and not tripped[eng]:
                                tripped[eng] = True
                                if log:
                                    log(f"{SOURCE_META[eng][0]} 连续失败，已熔断跳过")
                return game, None

            next_pending = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_mt_worker, g): g for g in pending}
                for fut in concurrent.futures.as_completed(futs):
                    g = futs[fut]
                    try:
                        game, tr = fut.result()
                    except Exception:
                        game, tr = g, None
                    done_est += 1
                    if tr:
                        _apply_tr(game, tr)
                        mt_hits += 1
                        _report(f"机翻 ({done_est}/{total_est}): {tr.name}")
                    else:
                        next_pending.append(game)
                        _report(f"机翻 ({done_est}/{total_est}): {game.name}")
                    if _stopped() or all(tripped.values()):
                        for p in futs:
                            p.cancel()
                        break

            seen_ids = {g.appid for g in next_pending}
            for g in pending:
                if g.appid not in status and g.appid not in seen_ids:
                    next_pending.append(g)
            pending = next_pending
            _flush_state()
            if log and mt_hits:
                log(f"机翻命中 {mt_hits} 个游戏")

    # Mark unresolved items cleanly.
    with state_lock:
        for g in pending:
            rec = state.get_app(g.appid)
            if not rec.get("zh_name") or (
                rec.get("source") == "steam" and not is_chinese_name(str(rec.get("zh_name") or ""))
            ):
                rec["zh_name"] = ""
                rec["source"] = ""
                rec["error"] = "未找到中文名，可尝试开启 AI 翻译或手动填写"
                dirty_count += 1
            status[g.appid] = rec.get("source") or "missing"
    _flush_state()
    return status
