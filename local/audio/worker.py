"""一起听：本地工作线程。向云端领任务 → 把当前这首歌的录音交给听骨 → 回传报告。"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from audio.analyze import analyze
from audio.recorder import LoopbackRecorder

log = logging.getLogger("collector.audio")


class ListenWorker(threading.Thread):
    def __init__(self, cfg, recorder: LoopbackRecorder, audio_dir: Path) -> None:
        super().__init__(name="listen-worker", daemon=True)
        self.cfg = cfg
        self.recorder = recorder
        self.audio_dir = Path(audio_dir)
        self._stopping = threading.Event()
        self.last_error: str | None = None
        self.jobs_done = 0

    def stop(self) -> None:
        self._stopping.set()

    # ---------- HTTP ----------
    def _call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        url = f"{self.cfg.cloud_base_url}{path}"
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.cfg.collector_token}",
                "User-Agent": "qqmusic-companion-collector/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")[:300]
        except Exception as exc:  # noqa: BLE001
            return 0, str(exc)

    # ---------- 主循环 ----------
    def run(self) -> None:
        if not self.cfg.cloud_base_url or not self.cfg.collector_token:
            log.info("没配置云端，听歌分析不领任务")
            return
        log.info("听歌分析已就绪：每 %.0f 秒问一次云端有没有要听的歌", self.cfg.listen_poll_seconds)
        while not self._stopping.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("听歌分析循环出错")
            self._stopping.wait(self.cfg.listen_poll_seconds)

    def _tick(self) -> None:
        self._handle_reserved()
        status, payload = self._call("GET", f"/api/v1/listen/job?device={self.cfg.device_name}")
        if status != 200 or not isinstance(payload, dict):
            if status != 0:
                log.warning("领任务返回 HTTP %s：%s", status, str(payload)[:120])
                self.last_error = f"领任务失败 HTTP {status}"
            return
        job = payload.get("job")
        if not job:
            self._polls = getattr(self, "_polls", 0) + 1
            if self._polls % 200 == 1:
                log.info("轮询中（云端=%s，暂无任务）", self.cfg.cloud_base_url)
            return
        log.info("领到任务 %s（%s）", job.get("id"), job.get("mode"))

        job_id = job["id"]
        mode = job.get("mode", "shallow")
        buffered = self.recorder.buffered_seconds()

        if buffered < self.cfg.listen_min_seconds:
            log.info("任务 %s 跳过：只录到 %.0f 秒，不够 %.0f 秒", job_id, buffered, self.cfg.listen_min_seconds)
            self._finish(job_id, False, None, f"还没录到足够的声音（当前 {buffered:.0f} 秒，至少需要 {self.cfg.listen_min_seconds:.0f} 秒）")
            return

        if mode == "deep" and not self.cfg.listen_deep_enabled:
            self._finish(job_id, False, None, "服务端配置为只允许浅听")
            return

        info = self.recorder.track_info()
        duration = info.get("duration")
        coverage = (buffered / duration) if duration else None

        # 深听要整首：只录到片段就明确拒绝，不出一份"看着像整首"的深听
        if mode == "deep" and coverage is not None and coverage < 0.85:
            note = (f"深听需要整首歌，这次只录到 {buffered:.0f} 秒 / 全曲约 {duration:.0f} 秒"
                    f"（{coverage * 100:.0f}%），改天完整听一遍再深听")
            log.info("任务 %s 拒绝深听：覆盖率只有 %.0f%%", job_id, coverage * 100)
            self._finish(job_id, False, None, note)
            return

        wav = self.audio_dir / f"{self._safe_name(info.get('title')) or job_id}.wav"
        started = time.time()
        try:
            log.info("任务 %s 开始：%s，缓冲 %.0f 秒", job_id, mode, buffered)
            key, title, seconds = self.recorder.snapshot(wav)
            log.info("任务 %s 录音快照 %.0f 秒（%.1f 秒）", job_id, seconds, time.time() - started)
            lrc_path = None
            try:
                from audio.lyrics_for_analysis import fetch_lrc, write_lrc

                lrc = fetch_lrc(info.get("title") or title, info.get("artist"), info.get("duration"))
                if lrc:
                    lrc_path = write_lrc(lrc, self.audio_dir / f"{job_id}.lrc")
                log.info("任务 %s 歌词：%s", job_id, "有" if lrc else "没有")
            except Exception:  # noqa: BLE001
                log.warning("取歌词失败，按纯音乐处理", exc_info=True)

            log.info("开始分析：%s（%s，%.0f 秒）", title or key or "未知", mode, seconds)
            outcome = analyze(
                wav, mode,
                venv_python=self.cfg.audio_python,
                tinggu_dir=self.cfg.tinggu_dir,
                lyric=str(lrc_path) if lrc_path else None,
            )
            log.info("任务 %s 分析结束：ok=%s（共 %.0f 秒）", job_id, outcome.get("ok"), time.time() - started)
            if outcome.get("ok"):
                self.jobs_done += 1
                self._finish(job_id, True, self._with_coverage(outcome["report"], seconds, duration),
                             None, seconds, title)
                log.info("分析完成：%s", title or key)
            else:
                self._finish(job_id, False, None, outcome.get("error") or "分析失败", seconds, title)
                log.warning("分析失败：%s", outcome.get("error"))
        except Exception as exc:  # noqa: BLE001
            log.exception("任务 %s 处理出错", job_id)
            self._finish(job_id, False, None, f"本机处理失败：{exc}", None, None)
        finally:
            for leftover in (wav, self.audio_dir / f"{job_id}.lrc"):
                if leftover and Path(leftover).exists():
                    try:
                        Path(leftover).unlink(missing_ok=True)
                    except OSError:
                        pass

    # ---------- 预约：这首放完再深听 ----------
    def _handle_reserved(self) -> None:
        last_info = getattr(self.recorder, "last_info", None)
        snapshot_last = getattr(self.recorder, "snapshot_last", None)
        if last_info is None or snapshot_last is None:
            return

        status, payload = self._call("GET", "/api/v1/listen/reserved")
        if status != 200 or not isinstance(payload, dict):
            return
        jobs = payload.get("jobs") or []
        if not jobs:
            return

        info = last_info()
        seconds = float(info.get("seconds") or 0)
        if seconds < self.cfg.listen_min_seconds:
            return
        rotated = info.get("rotated_at")
        if not rotated or rotated == getattr(self, "_last_rotated", None):
            return

        target = None
        for job in jobs:
            wanted = (job.get("track_title") or "").strip()
            got = (info.get("title") or "").strip()
            if wanted and got and wanted != got:
                continue
            target = job
            break
        if target is None:
            return

        self._last_rotated = rotated
        job_id = target["id"]
        title = info.get("title") or target.get("track_title") or job_id
        duration = info.get("duration")
        log.info("预约的任务 %s：%s 放完了（%.0f 秒），开始深听", job_id, title, seconds)

        wav = self.audio_dir / f"{self._safe_name(title) or job_id}.wav"
        lrc_path = None
        try:
            _, secs = snapshot_last(wav)
            if secs < self.cfg.listen_min_seconds:
                self._finish(job_id, False, None,
                             f"这首歌的录音没保存好（只有 {secs:.0f} 秒），这次没做成，下次再约", secs, title)
                log.warning("预约 %s 的录音只有 %.0f 秒，放弃", job_id, secs)
                return
            try:
                from audio.lyrics_for_analysis import fetch_lrc, write_lrc

                lrc = fetch_lrc(title, info.get("artist"), duration or secs)
                if lrc:
                    lrc_path = write_lrc(lrc, self.audio_dir / f"{job_id}.lrc")
            except Exception:  # noqa: BLE001
                log.warning("取歌词失败，按纯音乐处理", exc_info=True)

            outcome = analyze(wav, "deep", venv_python=self.cfg.audio_python,
                              tinggu_dir=self.cfg.tinggu_dir,
                              lyric=str(lrc_path) if lrc_path else None)
            if outcome.get("ok"):
                self.jobs_done += 1
                self._finish(job_id, True, self._with_coverage(outcome["report"], secs, duration),
                             None, secs, title)
                log.info("预约深听完成：%s", title)
            else:
                self._finish(job_id, False, None, outcome.get("error") or "深听失败", secs, title)
        except Exception as exc:  # noqa: BLE001
            log.exception("预约深听出错")
            self._finish(job_id, False, None, f"本机处理失败：{exc}")
        finally:
            for leftover in (wav, self.audio_dir / f"{job_id}.lrc"):
                if Path(leftover).exists():
                    try:
                        Path(leftover).unlink(missing_ok=True)
                    except OSError:
                        pass

    @staticmethod
    def _safe_name(text: str | None) -> str:
        import re

        cleaned = re.sub("[\\\\/:*?\"<>|\\r\\n]", "_", (text or "").strip())
        return cleaned[:40]

    @staticmethod
    def _with_coverage(report: str, seconds: float, duration: float | None) -> str:
        """在报告开头写清"这次录了多少、是不是整首"，避免被当成完整分析。"""
        if not duration:
            header = (f"【完整度】这次录到 {seconds:.0f} 秒；这首的时长没查到，"
                      "无法判断是不是整首，当作片段看。\n\n")
        elif seconds < duration * 0.95:
            header = (f"【完整度】这次只录到 {seconds:.0f} 秒，全曲约 {duration:.0f} 秒"
                      f"（{seconds / duration * 100:.0f}%），是片段，不是整首；"
                      "报告里的时间从这次录音的起点算，不是从歌头算"
                      "（QQ 音乐不提供播放进度，起点在歌里的位置无法确定）。\n\n")
        else:
            header = f"【完整度】基本是整首（录到 {seconds:.0f} 秒，全曲约 {duration:.0f} 秒）。\n\n"
        return header + report

    def _finish(self, job_id: str, ok: bool, report: str | None, error: str | None,
                seconds: float | None = None, title: str | None = None) -> None:
        body = {
            "job_id": job_id,
            "device": self.cfg.device_name,
            "ok": ok,
            "report": report,
            "error": error,
            "seconds": seconds,
            "title": title,
        }
        for attempt in range(8):
            status, payload = self._call("POST", "/api/v1/listen/result", body)
            if status == 200:
                return
            log.warning("回传任务 %s 失败（第 %d 次）：%s", job_id, attempt + 1, str(payload)[:120])
            time.sleep(min(15, 3 * (attempt + 1)))
        log.error("回传任务 %s 连续失败，云端会把它判为超时", job_id)
