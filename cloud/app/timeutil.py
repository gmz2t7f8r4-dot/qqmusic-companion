"""时间处理：统一用配置时区（默认 Asia/Shanghai）计算自然日。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import settings

try:
    TZ = ZoneInfo(settings.timezone)
except Exception:  # noqa: BLE001
    TZ = timezone(timedelta(hours=8))


def now() -> datetime:
    return datetime.now(TZ)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def now_iso() -> str:
    return iso(now())


def parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def today() -> date:
    return now().date()


def day_start(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=TZ)


def parse_day(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def retention_start(today_date: date | None = None, days: int | None = None) -> datetime:
    """保留最近 N 个自然日：今天是第 1 天，所以起点是今天往前 N-1 天的 0 点。"""
    today_date = today_date or today()
    days = days or settings.retention_days
    return day_start(today_date - timedelta(days=days - 1))


def retention_range() -> tuple[str, str]:
    start = retention_start()
    end = day_start(today() + timedelta(days=1))
    return iso(start), iso(end)


def clamp_day_range(day_from: date | None, day_to: date | None) -> tuple[datetime, datetime]:
    """把查询范围限制在实际保留的七天之内，任何参数都不能越界。"""
    allow_from = retention_start()
    allow_to = day_start(today() + timedelta(days=1))
    start = day_start(day_from) if day_from else allow_from
    end = day_start(day_to + timedelta(days=1)) if day_to else allow_to
    start = max(start, allow_from)
    end = min(end, allow_to)
    if end <= start:
        # 超出保留期的日期：给一个空窗口，而不是报错
        return allow_from, allow_from
    return start, end


def human_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒前"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"
