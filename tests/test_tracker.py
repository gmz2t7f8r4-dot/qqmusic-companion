"""不依赖 QQ 音乐的逻辑测试：用模拟的时间线验证“听完才记录”。

跑法：python tools/test_tracker.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "local"))
sys.path.insert(0, str(ROOT / "local" / "vendor"))

import tracker as tracker_mod  # noqa: E402
from media_session import Snapshot  # noqa: E402
from store import Store  # noqa: E402

DURATIONS = {("完整歌", "歌手A"): 200.0, ("半首歌", "歌手B"): 200.0, ("暂停歌", "歌手C"): 200.0, ("冷门歌", "歌手D"): None}


def fake_lookup(title, artist, cache, key):
    value = DURATIONS.get((title, artist))
    return (value, "test") if value else (None, "test")


tracker_mod.lookup_duration = fake_lookup


def snap(title: str, artist: str, status: str = "playing", present: bool = True) -> Snapshot:
    return Snapshot(
        present=present,
        app_id="QQMusic.exe",
        title=title if present else "",
        artist=artist if present else "",
        album="专辑",
        status=status if present else "none",
    )


def run(scenario: str, events: list[dict]) -> int:
    db_path = ROOT / "local-data" / "test-queue.db"
    if db_path.exists():
        db_path.unlink()
    store = Store(db_path)
    t = tracker_mod.Tracker(store, complete_ratio=0.95, discontinuity_seconds=30)
    captured: list[dict] = []
    t.on_event(captured.append)

    def feed(seconds: int, title: str, artist: str, start_at: float, status: str = "playing") -> float:
        now = start_at
        for _ in range(seconds):
            now += 1
            t.tick(snap(title, artist, status=status), now)
        return now

    now = 1000.0
    if scenario == "complete":
        now = feed(200, "完整歌", "歌手A", now)
        now = feed(5, "完整歌", "歌手A", now)  # 结尾多停几秒
        now += 1
        t.tick(snap("下一首", "歌手X"), now)
    elif scenario == "half":
        now = feed(100, "半首歌", "歌手B", now)
        now += 1
        t.tick(snap("下一首", "歌手X"), now)
    elif scenario == "pause":
        now = feed(60, "暂停歌", "歌手C", now)
        now = feed(60, "暂停歌", "歌手C", now, status="paused")
        now = feed(60, "暂停歌", "歌手C", now)
        now += 1
        t.tick(snap("下一首", "歌手X"), now)
    elif scenario == "unknown":
        now = feed(100, "冷门歌", "歌手D", now)
        now += 1
        t.tick(snap("下一首", "歌手X"), now)
    elif scenario == "sleep":
        now = feed(50, "完整歌", "歌手A", now)
        now += 600  # 电脑休眠 10 分钟，醒来时已经换了歌
        now = feed(3, "另一首", "歌手X", now)
        now += 1
        t.tick(snap("再一首", "歌手Y"), now)

    ok = True
    if scenario == "complete":
        ok = len(captured) == 1 and captured[0]["played_seconds"] >= 190 and captured[0]["title"] == "完整歌"
    elif scenario in ("half", "pause", "sleep"):
        ok = len(captured) == 0
    elif scenario == "unknown":
        ok = len(captured) == 0 and t.unknown_duration_skipped == 1

    print(f"[{'OK ' if ok else 'FAIL'}] {scenario:<9} 事件数={len(captured)} 漏记计数={t.unknown_duration_skipped} "
          f"{captured[0]['played_seconds'] if captured else ''}")
    events.extend(captured)
    store.close()
    return 0 if ok else 1


def main() -> int:
    events: list[dict] = []
    failures = 0
    for scenario in ("complete", "half", "pause", "unknown", "sleep"):
        failures += run(scenario, events)
    print("\n全部通过" if not failures else f"\n{failures} 项失败")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
