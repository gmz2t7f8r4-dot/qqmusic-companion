"""用 lrclib 查歌曲时长（只查时长，不取歌词；查不到就返回 None，不猜）。"""

from __future__ import annotations

import json
import statistics
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Protocol


class DurationCacheProtocol(Protocol):
    def cached_duration(self, key: str, max_age_days: int = 30) -> float | None: ...

    def cache_duration(self, key: str, duration: float | None, source: str) -> None: ...

API = "https://lrclib.net/api/search"
USER_AGENT = "qqmusic-companion/1.0 (local collector)"


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    for ch in " \t\u3000-—_/&,，.。'\"()（）[]【】":
        text = text.replace(ch, "")
    return text


def _artist_matches(wanted: str, got: str) -> bool:
    a, b = _norm(wanted), _norm(got)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    # 多歌手（例如 "歌手A/歌手B"）：任一位对上就算
    parts_a = {p for p in a.replace("、", "/").replace(",", "/").split("/") if p}
    parts_b = {p for p in b.replace("、", "/").replace(",", "/").split("/") if p}
    return bool(parts_a & parts_b)


def _pick_duration(hits: list[dict], title: str, artist: str) -> float | None:
    candidates: list[float] = []
    for hit in hits:
        duration = hit.get("duration")
        if not duration or duration <= 0:
            continue
        if _norm(hit.get("trackName", "")) != _norm(title):
            continue
        if artist and hit.get("artistName") and not _artist_matches(artist, hit["artistName"]):
            continue
        candidates.append(float(duration))
    if not candidates:
        return None
    # 同名同歌手常有多个版本，取出现次数最多的时长，避免选到偏短的版本导致“提前算听完”
    counts = Counter(round(c, 1) for c in candidates)
    top = max(counts.values())
    winners = [d for d, n in counts.items() if n == top]
    if len(winners) == 1:
        return winners[0]
    median = statistics.median(winners)
    return min(winners, key=lambda d: abs(d - median))


def lookup_duration(title: str, artist: str, cache: DurationCacheProtocol, key: str) -> tuple[float | None, str]:
    """返回 (时长秒, 来源)。来源可能是 cache / lrclib / not_found / error。"""
    cached = cache.cached_duration(key)
    if cached is not None:
        return cached, "cache"

    if not title.strip():
        return None, "no_title"

    params = {"track_name": title}
    if artist:
        params["artist_name"] = artist
    url = f"{API}?{urllib.parse.urlencode(params)}"

    payload = None
    for attempt in range(2):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as resp:
                payload = json.load(resp)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(2.5)  # 限流/临时故障，等一下再试一次
                continue
            return None, "error"
        except Exception:  # noqa: BLE001
            return None, "error"

    if payload is None:
        return None, "error"

    if not isinstance(payload, list) or not payload:
        cache.cache_duration(key, None, "lrclib")
        return None, "not_found"

    duration = _pick_duration(payload, title, artist)
    cache.cache_duration(key, duration, "lrclib")
    return (duration, "lrclib") if duration else (None, "not_found")
