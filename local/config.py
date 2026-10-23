"""本地采集器配置：从 config.ini 读取，缺省值保证不开配置也能跑起来（只是不会上传）。"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
DATA_DIR = PROJECT_DIR / "local-data"
LOG_DIR = DATA_DIR / "logs"


@dataclass
class Config:
    # 云端
    cloud_base_url: str = ""
    collector_token: str = ""
    device_name: str = "my-pc"
    verify_tls: bool = True

    # 采集
    poll_seconds: float = 1.0
    heartbeat_seconds: float = 15.0
    complete_ratio: float = 0.95
    # 两次采集间隔超过这个秒数，视为电脑休眠/采集中断，不把这段时间算成听歌
    discontinuity_seconds: float = 30.0

    # 日志
    log_keep_days: int = 7


def load(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or (APP_DIR / "config.ini")
    if not path.exists():
        return cfg

    parser = configparser.ConfigParser()
    # utf-8-sig：兼容记事本保存时带的 BOM，不然会读不出第一段
    parser.read(path, encoding="utf-8-sig")

    def get(section: str, key: str, default: str) -> str:
        if parser.has_option(section, key):
            return parser.get(section, key).strip()
        return default

    cfg.cloud_base_url = get("cloud", "base_url", "").rstrip("/")
    cfg.collector_token = get("cloud", "collector_token", "")
    cfg.device_name = get("cloud", "device_name", cfg.device_name)
    cfg.verify_tls = get("cloud", "verify_tls", "true").lower() != "false"

    cfg.poll_seconds = float(get("collector", "poll_seconds", "1"))
    cfg.heartbeat_seconds = float(get("collector", "heartbeat_seconds", "15"))
    cfg.complete_ratio = float(get("collector", "complete_ratio", "0.95"))
    cfg.discontinuity_seconds = float(get("collector", "discontinuity_seconds", "30"))
    cfg.log_keep_days = int(get("collector", "log_keep_days", "7"))

    # 环境变量优先，方便临时指向测试环境
    cfg.cloud_base_url = os.environ.get("QQCOMP_BASE_URL", cfg.cloud_base_url).rstrip("/")
    cfg.collector_token = os.environ.get("QQCOMP_COLLECTOR_TOKEN", cfg.collector_token)
    cfg.device_name = os.environ.get("QQCOMP_DEVICE", cfg.device_name)
    return cfg
