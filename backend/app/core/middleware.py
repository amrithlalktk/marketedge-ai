from __future__ import annotations

import ipaddress
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .cache import get_cache
from .config import get_settings


def client_ip(request: Request) -> str:
    """Peer address, or the right-most untrusted X-Forwarded-For hop when the peer is
    one of our own proxies (e.g. the Next.js frontend). Client-supplied XFF from
    untrusted peers is ignored, so it cannot be used to dodge the limiter."""
    peer = request.client.host if request.client else "unknown"
    nets = get_settings().trusted_proxy_networks
    def trusted(ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in n for n in nets)
    if not trusted(peer):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        if not trusted(hop):
            return hop
    return hops[0] if hops else peer


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-IP limiter; stricter bucket for authentication endpoints."""

    async def dispatch(self, request: Request, call_next):
        s = get_settings()
        path = request.url.path
        if path.endswith(("/health", "/ready", "/metrics")) or path.endswith("/telegram/webhook") or request.method == "OPTIONS":
            return await call_next(request)
        is_auth = "/auth/login" in path or "/auth/register" in path or "/auth/2fa" in path
        limit = s.auth_rate_limit_per_minute if is_auth else s.rate_limit_per_minute
        bucket = f"rl:{'auth' if is_auth else 'api'}:{client_ip(request)}:{int(time.time() // 60)}"
        if get_cache().incr_window(bucket, 70) > limit:
            return JSONResponse({"detail": "Rate limit exceeded. Try again shortly."}, status_code=429, headers={"Retry-After": "60"})
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
            resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if get_settings().cookie_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp
