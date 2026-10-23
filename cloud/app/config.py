"""云端服务配置：全部从环境变量读，方便 Coolify 里填。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "1.0.1"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    mcp_token: str = field(default_factory=lambda: _env("MCP_TOKEN"))
    collector_token: str = field(default_factory=lambda: _env("COLLECTOR_TOKEN"))
    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", "/data/qqmusic.db")))
    timezone: str = field(default_factory=lambda: _env("APP_TIMEZONE", "Asia/Shanghai"))
    retention_days: int = field(default_factory=lambda: _int("RETENTION_DAYS", 7))
    # 超过这么久没收到心跳，就认为采集器/电脑离线
    stale_seconds: int = field(default_factory=lambda: _int("STALE_SECONDS", 60))
    # 心跳里带的媒体状态超过这么久，就标注“可能过期”
    media_stale_seconds: int = field(default_factory=lambda: _int("MEDIA_STALE_SECONDS", 90))
    cleanup_interval_seconds: int = field(default_factory=lambda: _int("CLEANUP_INTERVAL_SECONDS", 3600))
    json_response: bool = field(default_factory=lambda: _env("MCP_JSON_RESPONSE", "false").lower() == "true")
    allowed_hosts: list[str] = field(
        default_factory=lambda: [h for h in _env("MCP_ALLOWED_HOSTS").split(",") if h]
    )

    def validate(self) -> None:
        missing = [name for name, value in (("MCP_TOKEN", self.mcp_token), ("COLLECTOR_TOKEN", self.collector_token)) if not value]
        if missing:
            raise RuntimeError(f"缺少环境变量: {', '.join(missing)}（访问口令，必须自己设置）")
        if self.mcp_token == self.collector_token:
            raise RuntimeError("MCP_TOKEN 和 COLLECTOR_TOKEN 不能相同")


settings = Settings()
