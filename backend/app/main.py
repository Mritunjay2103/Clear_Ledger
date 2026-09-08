"""ClearLedger application entry point.

API routes are mounted before the single-page-application fallback, so an
unknown ``/api`` path returns a JSON 404 rather than the HTML shell with HTTP
200 — a mistake that makes a broken deployment look healthy.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api.errors import error_body, register_error_handlers
from app.api.routes import reference, runs, samples, system
from app.api.security import (
    REQUEST_HEADER,
    guard,
    validate_startup_configuration,
)
from app.config import settings
from app.db.session import create_all, get_engine, session_scope
from app.services.queue import recover_on_startup, run_queue
from app.services.seed import seed_reference_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("clearledger")


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_startup_configuration()
    settings.ensure_directories()
    get_engine()
    create_all()
    try:
        with session_scope() as session:
            summary = seed_reference_data(session)
        logger.info("reference data seeded: %s", summary)
    except Exception:
        logger.exception("reference data could not be seeded")
    await run_queue.start()
    recovery = recover_on_startup()
    logger.info(
        "startup recovery: %s interrupted, %s requeued",
        recovery["interrupted"],
        recovery["requeued"],
    )
    logger.info("data directory: %s", settings.resolved_data_dir)
    logger.info("extraction provider: %s", settings.extraction_provider)
    yield
    await run_queue.stop()


app = FastAPI(
    title="ClearLedger",
    description=(
        "Invoice decisions with evidence. A prototype that turns a vendor invoice PDF into an "
        "explainable processing decision against purchase-order reference data. "
        "APPROVED reserves a commitment; it never initiates a payment."
    ),
    version=system.APP_VERSION,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)

register_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origin_allowlist,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", REQUEST_HEADER],
    max_age=600,
)

api = APIRouter(prefix="/api", dependencies=[Depends(guard)])
api.include_router(system.router)
api.include_router(samples.router)
api.include_router(runs.router)
api.include_router(reference.router)
app.include_router(api)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.get("/api/{unmatched:path}", include_in_schema=False)
async def api_not_found(unmatched: str) -> JSONResponse:
    """Guarantee JSON for unknown API paths, ahead of the SPA fallback."""
    return JSONResponse(
        status_code=404,
        content=error_body("not_found", f"No API route at /api/{unmatched}."),
    )


def _mount_frontend() -> None:
    """Serve the built single-page application behind the same reviewer login.

    The shell is protected too, not just the API. Otherwise a reviewer without
    credentials gets a working-looking page whose every request fails, instead
    of the browser's own credential prompt.
    """
    static_dir = settings.resolved_static_dir
    if static_dir is None:
        return

    root = static_dir.resolve()
    index = root / "index.html"

    # Bundles are served through this handler rather than a StaticFiles mount so
    # that they sit behind the same guard; a mount cannot carry a dependency.
    @app.get("/{full_path:path}", include_in_schema=False, dependencies=[Depends(guard)])
    async def spa(full_path: str):
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            # Hashed asset filenames change on every build, so they are safe to
            # cache hard; index.html must never be.
            cache = (
                "public, max-age=31536000, immutable"
                if "/assets/" in f"/{full_path}"
                else "no-cache"
            )
            return FileResponse(candidate, headers={"Cache-Control": cache})
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse(
            status_code=404, content=error_body("not_found", "No frontend build is mounted.")
        )


_mount_frontend()
