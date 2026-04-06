"""Bearer-token authentication for the FastAPI dashboard.

- Single shared secret in env var DASH_PASS.
- Clients send it in the X-API-Key header on every non-health request.
- Comparison is constant-time via hmac.compare_digest.
- Unauthenticated routes: /api/health (Railway probe) and all non /api/* paths
  (static SPA assets mounted at /).
- Startup fails fast if DASH_PASS is empty in a production environment
  (when CONFIG_PATH points at config.railway.yaml).

Also exposes a small typed whitelist for the /api/config PUT endpoint so
attacker-supplied keys can't poison the risk_config table.
"""

from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass
from typing import Callable

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public paths that skip the auth check
# ---------------------------------------------------------------------------
_PUBLIC_PATHS: frozenset[str] = frozenset({
    "/api/health",
    "/docs",
    "/redoc",
    "/openapi.json",  # keep OpenAPI docs open for convenience; no secrets leak
})


def _is_api_path(path: str) -> bool:
    return path.startswith("/api/")


def _require_dash_pass() -> str:
    """Return DASH_PASS from env, or raise RuntimeError at startup.

    Called during middleware construction, not per-request, so startup fails
    fast if the secret is missing.
    """
    value = os.environ.get("DASH_PASS", "").strip()
    if not value:
        raise RuntimeError(
            "DASH_PASS is not set. The dashboard refuses to boot without an "
            "authentication secret. Set it in your .env or on Railway via "
            "`railway variables --set DASH_PASS=<strong-random-token>`."
        )
    if len(value) < 16:
        raise RuntimeError(
            f"DASH_PASS is too short ({len(value)} chars); require >= 16. "
            "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    return value


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject /api/* requests that don't present a valid X-API-Key header.

    Non-/api paths (static SPA assets) and explicitly-public paths bypass.
    """

    def __init__(self, app, secret: str) -> None:
        super().__init__(app)
        self._secret = secret.encode("utf-8")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Public by policy
        if path in _PUBLIC_PATHS or not _is_api_path(path):
            return await call_next(request)

        # Require X-API-Key
        supplied = request.headers.get("x-api-key", "")
        if not supplied:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing X-API-Key header"},
            )

        if not hmac.compare_digest(supplied.encode("utf-8"), self._secret):
            # Do NOT log the supplied value
            logger.warning("Rejected dashboard request with bad X-API-Key on %s", path)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid X-API-Key"},
            )

        return await call_next(request)


def install_auth(app) -> None:
    """Install the bearer-token middleware on the given FastAPI app.

    Raises RuntimeError at startup if DASH_PASS is missing/too-short.
    """
    secret = _require_dash_pass()
    app.add_middleware(BearerAuthMiddleware, secret=secret)
    logger.info("Bearer-token auth installed (DASH_PASS, %d chars)", len(secret))


# ---------------------------------------------------------------------------
# /api/config PUT input validation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfigKeySpec:
    key: str
    type_: type
    min_value: float | None = None
    max_value: float | None = None

    def coerce(self, raw: str) -> int | float | bool | str:
        if self.type_ is bool:
            if raw.lower() in {"true", "1", "yes", "on"}:
                return True
            if raw.lower() in {"false", "0", "no", "off"}:
                return False
            raise ValueError(f"not a bool: {raw!r}")
        if self.type_ is int:
            v = int(raw)
        elif self.type_ is float:
            v = float(raw)
        elif self.type_ is str:
            v = raw
        else:
            raise ValueError(f"unsupported type: {self.type_}")
        if isinstance(v, (int, float)):
            if self.min_value is not None and v < self.min_value:
                raise ValueError(f"{self.key}={v} below min {self.min_value}")
            if self.max_value is not None and v > self.max_value:
                raise ValueError(f"{self.key}={v} above max {self.max_value}")
        return v


# Whitelist of hot-reloadable risk parameters.
# These are the only keys /api/config PUT will accept into risk_config.
ALLOWED_CONFIG_KEYS: dict[str, ConfigKeySpec] = {
    spec.key: spec
    for spec in [
        ConfigKeySpec("max_position_usdc", float, min_value=1, max_value=1000),
        ConfigKeySpec("min_trade_size_usdc", float, min_value=1, max_value=500),
        ConfigKeySpec("max_open_positions", int, min_value=1, max_value=100),
        ConfigKeySpec("daily_loss_cap_usdc", float, min_value=1, max_value=5000),
        ConfigKeySpec("max_exposure_per_city_usdc", float, min_value=1, max_value=5000),
        ConfigKeySpec("max_exposure_per_date_usdc", float, min_value=1, max_value=5000),
        ConfigKeySpec("drawdown_pause_pct", float, min_value=0.0, max_value=1.0),
        ConfigKeySpec("min_edge", float, min_value=0.0, max_value=1.0),
        ConfigKeySpec("min_confidence", float, min_value=0.0, max_value=1.0),
        ConfigKeySpec("kelly_fraction", float, min_value=0.0, max_value=1.0),
        # Deliberately NOT included:
        #   paper_trading — requires redeploy + LIVE_TRADING_CONFIRMED=yes env var
        #   private_key / api_key / api_secret — secrets never go through this path
    ]
}


def validate_config_update(key: str, value: str) -> int | float | bool | str:
    """Validate and coerce a /api/config PUT payload.

    Raises HTTPException(400) on unknown key or value out of range.
    Returns the coerced typed value ready to persist.
    """
    spec = ALLOWED_CONFIG_KEYS.get(key)
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=f"config key '{key}' is not in the allowed whitelist. "
                   f"Allowed: {sorted(ALLOWED_CONFIG_KEYS)}",
        )
    try:
        return spec.coerce(value)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
