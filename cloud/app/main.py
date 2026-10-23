"""云端入口：一个进程里同时提供 上传接口 + MCP 工具 + 健康检查。"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from . import db
from .auth import mcp_authorized, require_collector
from .config import VERSION, settings
from .mcp_tools import mcp
from .timeutil import iso, now_iso, retention_start

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("cloud")

STARTED_AT = time.time()


class IngestBody(BaseModel):
    device: str = Field(default="unknown", max_length=64)
    sent_at: str | None = None
    state: dict | None = None
    events: list[dict] = Field(default_factory=list)


api = FastAPI(title="QQ Music Companion", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)


@api.get("/health")
async def health() -> dict:
    ok, error = db.health()
    return {
        "ok": ok,
        "version": VERSION,
        "time": now_iso(),
        "uptime_seconds": round(time.time() - STARTED_AT),
        "database_error": error,
    }


@api.post("/api/v1/ingest", dependencies=[Depends(require_collector)])
async def ingest(body: IngestBody) -> dict:
    if body.state:
        db.save_state(body.device, body.state)
    accepted, duplicates = db.insert_events(body.device, body.events)
    log.info("收到 %s：新增 %d，重复 %d", body.device, accepted, duplicates)
    return {
        "ok": True,
        "accepted": accepted,
        "duplicates": duplicates,
        "server_time": now_iso(),
        "retention_from": iso(retention_start()),
    }


@api.get("/")
async def root() -> dict:
    return {"service": "qqmusic-companion", "version": VERSION, "mcp": "/mcp"}


# ---------- MCP 传输 ----------
mcp.settings.json_response = settings.json_response
if settings.allowed_hosts:
    from mcp.server.transport_security import TransportSecuritySettings

    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=[f"https://{h}" for h in settings.allowed_hosts],
    )

mcp_http_app = mcp.streamable_http_app()   # 路径 /mcp
mcp_sse_app = mcp.sse_app()                # 路径 /sse 与 /messages/

PROTECTED_PREFIXES = ("/mcp", "/sse", "/messages")
PUBLIC_PATHS = {"/health"}


async def _unauthorized(scope, receive, send) -> None:
    response = JSONResponse({"error": "unauthorized", "hint": "缺少或错误的访问口令"}, status_code=401)
    await response(scope, receive, send)


class App:
    """把自己拼成一个 ASGI 应用：/mcp、/sse 走 MCP，其余走上传接口。"""

    def __init__(self) -> None:
        self._cleanup_task: asyncio.Task | None = None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._lifespan(scope, receive, send)
            return
        if scope["type"] != "http":
            return

        path = scope.get("path", "/")
        # 有些客户端会多带一个结尾斜杠，直接当 /mcp 处理，避免 307 跳转
        if path == "/mcp/":
            scope = dict(scope)
            scope["path"] = "/mcp"
            path = "/mcp"
        if path not in PUBLIC_PATHS and any(
            path == prefix or path.startswith(prefix + "/") for prefix in PROTECTED_PREFIXES
        ):
            if not mcp_authorized(scope):
                await _unauthorized(scope, receive, send)
                return

        if path == "/mcp" or path.startswith("/mcp/"):
            await mcp_http_app(scope, receive, send)
        elif path.startswith("/sse") or path.startswith("/messages"):
            await mcp_sse_app(scope, receive, send)
        else:
            await api(scope, receive, send)

    async def _lifespan(self, scope, receive, send):
        message = await receive()
        if message["type"] != "lifespan.startup":
            return
        try:
            settings.validate()
            db.init()
            settings.db_path.parent.mkdir(parents=True, exist_ok=True)
            deleted = db.cleanup_old_events()
            log.info("启动清理：删除过期记录 %d 条（保留 %d 天）", deleted, settings.retention_days)
        except Exception as exc:  # noqa: BLE001
            log.exception("启动失败")
            await send({"type": "lifespan.startup.failed", "message": str(exc)})
            return

        try:
            async with api.router.lifespan_context(api):
                async with mcp_http_app.router.lifespan_context(mcp_http_app):
                    async with mcp_sse_app.router.lifespan_context(mcp_sse_app):
                        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
                        await send({"type": "lifespan.startup.complete"})
                        while True:
                            msg = await receive()
                            if msg["type"] == "lifespan.shutdown":
                                break
        except Exception as exc:  # noqa: BLE001
            log.exception("运行期错误")
            await send({"type": "lifespan.shutdown.failed", "message": str(exc)})
            return
        finally:
            if self._cleanup_task:
                self._cleanup_task.cancel()

        await send({"type": "lifespan.shutdown.complete"})

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(settings.cleanup_interval_seconds)
                deleted = db.cleanup_old_events()
                if deleted:
                    log.info("定时清理：删除过期记录 %d 条", deleted)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("定时清理失败")


app = App()
