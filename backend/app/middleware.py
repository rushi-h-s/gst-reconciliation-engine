import logging

import jwt
from jwt.exceptions import InvalidTokenError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

log = logging.getLogger("gst_engine")

_API_PREFIX = "/api/v1"


def _json401(detail: str) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": detail})


def _json403(detail: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": detail})


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Validate Bearer JWT on every /api/v1 request.

    On success, writes the org_id extracted from app_metadata.org_id into
    request.state.org_id so downstream dependencies can read it without
    touching the Authorization header again.

    /health and any other non-/api/v1 paths are passed through unchanged.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if not request.url.path.startswith(_API_PREFIX):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return _json401("Missing or malformed Authorization header (expected: Bearer <token>)")

        token = auth[len("Bearer "):].strip()

        try:
            claims: dict = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=["HS256"],
                # Skip audience check: Supabase user tokens carry aud="authenticated"
                # but service-role keys do not — org_id presence is the real gate.
                options={"verify_aud": False},
            )
        except InvalidTokenError as exc:
            log.debug("JWT validation failed: %s", exc)
            return _json401("Invalid or expired token")

        org_id: str | None = (claims.get("app_metadata") or {}).get("org_id")
        if not org_id:
            return _json403("Token does not carry an app_metadata.org_id claim")

        request.state.org_id = str(org_id)
        return await call_next(request)
