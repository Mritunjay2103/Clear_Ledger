"""Reviewer access control and browser-mutation protection.

HTTP Basic with environment credentials is enough for a private reviewer demo and
avoids building an account system. Because browsers resend Basic credentials
automatically, every state-changing request must also prove it came from our own
origin: an allow-listed ``Origin`` header plus an explicit request header that a
cross-site form cannot set without triggering a CORS preflight.
"""

from __future__ import annotations

import base64
import secrets
import time
from collections import defaultdict, deque

from fastapi import Request

from app.api.errors import ApiError
from app.config import settings

REQUEST_HEADER = "x-clearledger-request"
REQUEST_HEADER_VALUE = "1"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_PATHS = {"/api/health", "/api/readiness"}

_MUTATION_WINDOW_SECONDS = 60
_recent_mutations: dict[str, deque[float]] = defaultdict(deque)


class StartupConfigurationError(RuntimeError):
    pass


def validate_startup_configuration() -> None:
    """Refuse to start a production profile without reviewer credentials."""
    if settings.is_production and not settings.auth_enabled:
        raise StartupConfigurationError(
            "APP_ENV is production but DEMO_USERNAME/DEMO_PASSWORD are not set. "
            "A public deployment must not expose invoice data unauthenticated."
        )
    if settings.auth_enabled and len(settings.demo_password) < 12:
        raise StartupConfigurationError(
            "DEMO_PASSWORD must be at least 12 characters. There is no default password."
        )


def _unauthorized() -> ApiError:
    # WWW-Authenticate is what makes the browser show its own credential prompt;
    # without it a reviewer just sees a page whose requests all fail.
    return ApiError(
        "unauthorized",
        "Reviewer credentials are required.",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="ClearLedger", charset="UTF-8"'},
    )


def check_credentials(request: Request) -> None:
    if not settings.auth_enabled:
        return
    header = request.headers.get("authorization", "")
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        raise _unauthorized()
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except Exception as exc:
        raise _unauthorized() from exc
    username, _, password = decoded.partition(":")
    username_ok = secrets.compare_digest(username, settings.demo_username)
    password_ok = secrets.compare_digest(password, settings.demo_password)
    if not (username_ok and password_ok):
        raise _unauthorized()


def check_mutation_origin(request: Request) -> None:
    """Reject cross-site writes that ride on automatically resent credentials."""
    if request.method in SAFE_METHODS:
        return
    if request.headers.get(REQUEST_HEADER, "").strip() != REQUEST_HEADER_VALUE:
        raise ApiError(
            "missing_request_header",
            "State-changing requests must send the X-ClearLedger-Request header.",
            status_code=403,
        )
    origin = request.headers.get("origin")
    if origin is None:
        return  # Non-browser client; Basic credentials still apply.
    allowed = set(settings.origin_allowlist)
    same_origin = str(request.base_url).rstrip("/")
    allowed.add(same_origin)
    if origin.rstrip("/") not in allowed:
        raise ApiError(
            "origin_not_allowed",
            "This origin is not allowed to make changes.",
            status_code=403,
        )


def check_mutation_rate(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _recent_mutations[client]
    while bucket and now - bucket[0] > _MUTATION_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= limit:
        raise ApiError(
            "rate_limited",
            f"Too many changes from this client; the limit is {limit} per minute.",
            status_code=429,
            retryable=True,
        )
    bucket.append(now)


async def guard(request: Request) -> None:
    """Dependency applied to every /api route except liveness/readiness."""
    if request.url.path in PUBLIC_PATHS:
        return
    check_credentials(request)
    check_mutation_origin(request)
    check_mutation_rate(request)


def reset_rate_limits() -> None:
    _recent_mutations.clear()
