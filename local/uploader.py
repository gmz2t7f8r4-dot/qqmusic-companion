"""把状态和播放事件上传到云端。断网时事件留在本地队列，恢复后按顺序补传。"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from config import Config
from store import Store

log = logging.getLogger("collector.uploader")
LOCAL_TZ = timezone(timedelta(hours=8))


def _iso_now() -> str:
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


class Uploader(threading.Thread):
    def __init__(self, cfg: Config, store: Store) -> None:
        super().__init__(name="uploader", daemon=True)
        self.cfg = cfg
        self.store = store
        self._state: dict | None = None
        self._state_dirty = False
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self.failures = 0
        self.last_ok_at: str | None = store.get_meta("last_upload_ok_at")
        self.last_error: str | None = store.get_meta("last_upload_error")
        self.warned_unconfigured = False

    # ---------- 外部调用 ----------
    def push_state(self, payload: dict) -> None:
        with self._lock:
            self._state = payload
            self._state_dirty = True
        self._wake.set()

    def notify(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    # ---------- 内部 ----------
    def run(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait(timeout=5)
            self._wake.clear()
            if self._stopping.is_set():
                break
            try:
                self._cycle()
            except Exception:  # noqa: BLE001
                log.exception("上传循环异常")
                time.sleep(5)

    def _cycle(self) -> None:
        if not self.cfg.cloud_base_url or not self.cfg.collector_token:
            if not self.warned_unconfigured:
                log.warning("还没配置云端地址或采集口令，数据只保存在本地队列，不会上传")
                self.warned_unconfigured = True
            return

        pending = self.store.pending_events(limit=50)
        with self._lock:
            state = self._state
            dirty = self._state_dirty

        if not pending and not dirty:
            return

        body = {
            "device": self.cfg.device_name,
            "sent_at": _iso_now(),
            "state": state,
            "events": [item["payload"] for item in pending],
        }
        try:
            result = self._post(body)
        except urllib.error.HTTPError as exc:
            detail = f"HTTP {exc.code}"
            try:
                detail += " " + exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            self._fail(detail, [item["_id"] for item in pending], permanent=400 <= exc.code < 500 and exc.code not in (408, 429))
            return
        except Exception as exc:  # noqa: BLE001
            self._fail(f"网络错误: {exc}", [item["_id"] for item in pending], permanent=False)
            return

        self.store.delete_events([item["_id"] for item in pending])
        with self._lock:
            if self._state is state:
                self._state_dirty = False
        self.failures = 0
        self.last_ok_at = _iso_now()
        self.last_error = None
        self.store.set_meta("last_upload_ok_at", self.last_ok_at)
        self.store.set_meta("last_upload_error", "")
        if pending:
            log.info("已上传 %d 条记录（云端新增 %s，重复 %s）", len(pending), result.get("accepted"), result.get("duplicates"))

    def _fail(self, detail: str, ids: list[str], permanent: bool) -> None:
        self.failures += 1
        self.last_error = detail
        self.store.set_meta("last_upload_error", detail)
        self.store.mark_attempt(ids, detail)
        wait = min(300, 5 * (2 ** min(self.failures, 6)))
        log.warning("上传失败（%s），%s 秒后重试；队列 %d 条", detail, wait, self.store.pending_count())
        time.sleep(wait if not permanent else min(wait, 60))

    def _post(self, body: dict) -> dict:
        url = f"{self.cfg.cloud_base_url}/api/v1/ingest"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.cfg.collector_token}",
                "User-Agent": "qqmusic-companion-collector/1.0",
            },
        )
        context = None if self.cfg.verify_tls else ssl._create_unverified_context()  # noqa: SLF001
        with urllib.request.urlopen(request, timeout=15, context=context) as resp:
            return json.load(resp)
