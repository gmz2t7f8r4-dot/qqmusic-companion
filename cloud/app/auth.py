"""访问口令校验：MCP 用 MCP_TOKEN，采集器用 COLLECTOR_TOKEN，两者不同。"""

from __future__ import annotations

import hmac
from urllib.parse import parse_qs

from fastapi import Header, HTTPException

from .config import settings


def _match(given: str | None, expected: str) -> bool:
    if not given or not expected:
        return False
    return hmac.compare_digest(given.strip(), expected)


def token_from_scope(scope) -> str | None:
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    direct = headers.get("x-mcp-token") or headers.get("x-api-key")
    if direct:
        return direct.strip()
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    for key in ("token", "key", "access_token"):
        if query.get(key):
            return query[key][0].strip()
    return None


def mcp_authorized(scope) -> bool:
    return _match(token_from_scope(scope), settings.mcp_token)


def collector_token_ok(token: str | None) -> bool:
    return _match(token, settings.collector_token)


def require_collector(authorization: str | None = Header(default=None)) -> None:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not collector_token_ok(token):
        raise HTTPException(status_code=401, detail="采集口令无效")
