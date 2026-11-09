"""给音频分析配歌词：用 lrclib 的带时间轴歌词，写成 .lrc 交给听骨。

只查歌词、不落库。查不到就返回 None，让听骨按纯音乐处理。
"""

from __future__ import annotations

import json
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://lrclib.net/api"
UA = "qqmusic-companion/1.0 (local listen)"


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    for ch in " \t\u3000-—_/&,，.。'\"()（）[]【】":
        text = text.replace(ch, "")
    return text


def fetch_lrc(title: str, artist: str | None, duration: float | None = None) -> str | None:
    title = (title or "").strip()
    if not title:
        return None
    params = {"track_name": title}
    if artist:
        params["artist_name"] = artist
    if duration:
        params["duration"] = round(duration)
    url = f"{API}/get?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=12) as resp:
            data = json.load(resp)
        if isinstance(data, dict) and data.get("syncedLyrics"):
            return data["syncedLyrics"]
    except Exception:  # noqa: BLE001
        pass

    # 退一步：搜索后取第一条歌名对得上的
    try:
        search = {"track_name": title}
        if artist:
            search["artist_name"] = artist
        req = urllib.request.Request(
            f"{API}/search?{urllib.parse.urlencode(search)}",
            headers={"User-Agent": UA, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            hits = json.load(resp)
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(hits, list):
        return None
    for hit in hits:
        if _norm(hit.get("trackName")) != _norm(title):
            continue
        if artist and hit.get("artistName") and not (
            _norm(artist) in _norm(hit.get("artistName")) or _norm(hit.get("artistName")) in _norm(artist)
        ):
            continue
        if hit.get("syncedLyrics"):
            return hit["syncedLyrics"]

    # 翻唱兜底：只按歌名找一次（翻唱歌词通常和原唱一样，按歌手搜会搜不到）
    try:
        req = urllib.request.Request(
            f"{API}/search?{urllib.parse.urlencode({'track_name': title})}",
            headers={"User-Agent": UA, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            loose = json.load(resp)
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(loose, list):
        return None
    for hit in loose:
        if _norm(hit.get("trackName")) != _norm(title):
            continue
        if hit.get("syncedLyrics"):
            return hit["syncedLyrics"]
    return None


def write_lrc(lrc_text: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(lrc_text, encoding="utf-8")
    return target
