"""五个 MCP 工具：当前播放、歌词、播放历史、听歌统计、服务状态。

原则：拿不到就说拿不到；过期数据一定标注，不把旧状态伪装成实时状态。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP

from . import db
from .config import VERSION, settings
from .lyrics import fetch_lyrics
from .timeutil import clamp_day_range, day_start, human_age, iso, now, now_iso, parse_day, parse_iso, retention_range, retention_start, today

mcp = FastMCP(
    "qqmusic-companion",
    instructions=(
        "查询我在 Windows 电脑上用 QQ 音乐听歌的情况。"
        "get_now_playing 获取当前/最后一次播放状态；get_lyrics 查歌词；"
        "get_play_history 查最近七天播放记录；get_listening_stats 查简单统计；"
        "get_service_status 排查采集是否正常。"
        "播放进度是按累计播放时间估算的，返回值里 position_estimated 为 true。"
    ),
)


def _state_or_error() -> tuple[dict | None, dict | None]:
    state = db.latest_state()
    if not state:
        return None, {
            "ok": False,
            "status": "no_data",
            "note": "还没有收到过采集器的数据（电脑没开过采集器，或还没连上云端）",
            "server_time": now_iso(),
        }
    return state, None


@mcp.tool()
def get_now_playing() -> dict:
    """获取当前正在播放的歌曲、歌手、专辑、播放状态、进度和最后更新时间。"""
    state, error = _state_or_error()
    if error:
        return error

    assert state is not None
    payload = state["payload"]
    media = payload.get("media") or {}
    collector = payload.get("collector") or {}

    received = parse_iso(state.get("received_at"))
    captured = parse_iso(payload.get("captured_at"))
    current = now()
    heartbeat_age = (current - received).total_seconds() if received else None
    media_age = (current - captured).total_seconds() if captured else None

    online = heartbeat_age is not None and heartbeat_age <= settings.stale_seconds
    stale_reason = None
    if not online:
        stale_reason = "collector_offline"
    elif media_age is not None and media_age > settings.media_stale_seconds:
        stale_reason = "media_stale"

    has_track = bool(media.get("title"))
    track = (
        {"title": media.get("title"), "artist": media.get("artist"), "album": media.get("album")}
        if has_track
        else None
    )

    playback = media.get("status") or "none"
    if not online:
        playback = "unknown_offline"

    result = {
        "ok": True,
        "realtime": stale_reason is None,
        "stale_reason": stale_reason,
        "playback": playback,
        "track": track,
        "position_seconds": media.get("position_seconds"),
        "position_estimated": True,
        "duration_seconds": media.get("duration_seconds"),
        "duration_source": media.get("duration_source"),
        "track_started_at": media.get("track_started_at"),
        "last_updated": payload.get("captured_at"),
        "seconds_since_update": round(media_age) if media_age is not None else None,
        "last_updated_text": human_age(media_age) if media_age is not None else None,
        "collector": {
            "device": state.get("device"),
            "online": online,
            "last_heartbeat_at": state.get("received_at"),
            "heartbeat_age_seconds": round(heartbeat_age) if heartbeat_age is not None else None,
            "queue_pending": collector.get("queue_pending"),
        },
        "server_time": now_iso(),
    }

    if not online:
        result["note"] = (
            f"电脑上的采集器最后一次上传是 {human_age(heartbeat_age)}，"
            "下面这些都是最后一次成功采集到的内容，不能当成现在正在播放"
        )
    elif stale_reason == "media_stale":
        result["note"] = "数据超过预期时间没有更新，可能已过期"
    elif not has_track:
        result["note"] = "现在没有可用的播放会话（QQ 音乐没开，或者没有在放歌）"
    elif not media.get("duration_seconds"):
        result["note"] = "这首歌的时长查不到，进度无法估算，也不会被记为一次播放"
    return result


@mcp.tool()
def get_lyrics(
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    duration_seconds: float | None = None,
    timestamps: bool = False,
) -> dict:
    """查询歌词。不传歌曲信息时，查当前正在播放的歌。

    timestamps=true 时优先返回带时间轴的歌词，否则优先纯文本。
    """
    using_current = not title
    if using_current:
        state = db.latest_state()
        media = (state or {}).get("payload", {}).get("media", {})
        title = media.get("title")
        artist = artist or media.get("artist")
        album = album or media.get("album")
        duration_seconds = duration_seconds or media.get("duration_seconds")
        if not title:
            return {
                "found": False,
                "status": "no_current_song",
                "note": "没有正在播放的歌，也没有指定歌曲",
                "server_time": now_iso(),
            }

    result = fetch_lyrics(title, artist, album, duration_seconds, want_synced=timestamps)
    result["query"] = {"title": title, "artist": artist, "album": album, "duration_seconds": duration_seconds}
    result["queried_current_song"] = using_current
    result["server_time"] = now_iso()
    return result


@mcp.tool()
def get_play_history(
    date: str | None = None,
    title: str | None = None,
    event_id: str | None = None,
    limit: int = 100,
) -> dict:
    """查询最近七天的播放记录。

    date 传 YYYY-MM-DD 时只查那一天；不传则返回七天内全部。
    title 支持模糊匹配歌名或歌手；event_id 精确定位某一条记录。
    """
    parsed = parse_day(date) if date else None
    if date and parsed is None:
        return {"ok": False, "status": "bad_date", "note": "日期格式应为 YYYY-MM-DD"}

    start, end = clamp_day_range(parsed, parsed)
    events = db.query_events(day=parsed, title=title, event_id=event_id, limit=limit)
    allow_from, allow_to = retention_range()
    result = {
        "ok": True,
        "retention_days": settings.retention_days,
        "retention_window": {"from": allow_from, "to": allow_to},
        "query_range": {"from": iso(start), "to": iso(end)},
        "count": len(events),
        "events": events,
        "server_time": now_iso(),
    }
    if not events:
        result["note"] = "这个范围内没有记录（没有听完一首歌，或者已经超出七天保留期）"
    if date and parsed and parsed < retention_start().date():
        result["note"] = "指定日期早于七天保留期，只能返回空结果"
    return result


@mcp.tool()
def get_listening_stats(
    kind: str = "top",
    title: str | None = None,
    limit: int = 10,
    date: str | None = None,
) -> dict:
    """最近七天的简单听歌统计。

    kind: top=播放次数排行；song=单首歌播放次数（需配合 title）；daily=按天统计。
    date 传 YYYY-MM-DD 时只统计那一天。
    """
    parsed = parse_day(date) if date else None
    if date and parsed is None:
        return {"ok": False, "status": "bad_date", "note": "日期格式应为 YYYY-MM-DD"}

    start, end = clamp_day_range(parsed, parsed)
    base = {
        "ok": True,
        "kind": kind,
        "scope": {"from": iso(start), "to": iso(end)},
        "retention_days": settings.retention_days,
        "server_time": now_iso(),
    }

    if kind == "top":
        rows = db.top_songs(start, end, limit=limit)
        base.update({"count": len(rows), "songs": rows})
        if not rows:
            base["note"] = "这个范围内没有听完的歌"
        return base

    if kind == "song":
        if not title:
            return {"ok": False, "status": "missing_title", "note": "kind=song 时需要提供 title"}
        plays, seconds = db.song_plays(start, end, title=title)
        base.update({"title": title, "plays": plays, "played_seconds": round(seconds, 1)})
        if not plays:
            base["note"] = "这个范围内这首歌没有被完整听完过"
        return base

    if kind == "daily":
        rows = db.daily_counts(start, end)
        base.update({"count": len(rows), "days": rows})
        if not rows:
            base["note"] = "这个范围内没有记录"
        return base

    return {"ok": False, "status": "bad_kind", "note": "kind 只能是 top / song / daily"}


@mcp.tool()
def get_service_status() -> dict:
    """检查云端服务、采集器、数据库的状态，用于排查问题。"""
    ok, error = db.health()
    state = db.latest_state()
    info = db.counts()

    collector_block: dict = {"online": False, "note": "从未收到过采集器的数据"}
    if state:
        received = parse_iso(state.get("received_at"))
        age = (now() - received).total_seconds() if received else None
        payload = state["payload"]
        media = payload.get("media") or {}
        collector = payload.get("collector") or {}
        online = age is not None and age <= settings.stale_seconds
        collector_block = {
            "device": state.get("device"),
            "online": online,
            "last_heartbeat_at": state.get("received_at"),
            "heartbeat_age_seconds": round(age) if age is not None else None,
            "last_media_state": {
                "status": media.get("status"),
                "title": media.get("title"),
                "artist": media.get("artist"),
                "captured_at": payload.get("captured_at"),
            },
            "queue_pending": collector.get("queue_pending"),
            "last_upload_ok_at": collector.get("last_upload_ok_at"),
            "last_upload_error": collector.get("last_upload_error"),
            "media_read_error": collector.get("media_read_error"),
            "unknown_duration_skipped": collector.get("unknown_duration_skipped"),
            "collector_version": collector.get("version"),
            "uptime_seconds": collector.get("uptime_seconds"),
        }
        if not online:
            collector_block["note"] = (
                f"电脑可能关机或采集器没开（最后一次上传是 {human_age(age)}），"
                "仍然可以查已经上传的历史"
            )

    return {
        "ok": ok,
        "version": VERSION,
        "server_time": now_iso(),
        "timezone": settings.timezone,
        "database": {
            "ok": ok,
            "error": error,
            "events": info["events"],
            "latest_event_at": info["latest_event_at"],
            "last_ingest_at": info["last_ingest_at"],
        },
        "retention": {
            "days": settings.retention_days,
            "from": iso(retention_start()),
            "to": iso(day_start(today() + timedelta(days=1))),
        },
        "collector": collector_block,
    }
