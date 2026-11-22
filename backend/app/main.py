"""FastAPI entry point.  Run from backend/:  uvicorn app.main:app --reload"""

import logging
import platform
import sys
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import errors
from app.config import APP_VERSION, get_settings
from pipeline import preflight

log = logging.getLogger("shortforge")

app = FastAPI(title="ShortForge Local", version=APP_VERSION)

# The Vite dev server proxies /api to this app; CORS is only a fallback for direct calls.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Errors -> {"code", "severity", "params"} (translated by the frontend)
# --------------------------------------------------------------------------
@app.exception_handler(errors.AppError)
async def _app_error(_: Request, exc: errors.AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = ", ".join(
        ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
        for err in exc.errors()
    )
    payload = errors.issue(errors.VALIDATION_ERROR, fields=fields)
    return JSONResponse(status_code=422, content=payload)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = errors.NOT_FOUND if exc.status_code == 404 else errors.INTERNAL_ERROR
    return JSONResponse(status_code=exc.status_code, content=errors.issue(code))


@app.exception_handler(Exception)
async def _unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content=errors.issue(errors.INTERNAL_ERROR))


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _cached_system_info() -> dict[str, Any]:
    s = get_settings()
    return preflight.system_info(s.ffmpeg_bin, s.ffprobe_bin, s.device)


@app.get("/health")
def health(refresh: bool = False) -> dict[str, Any]:
    """Backend status: ffmpeg version, compute device (cuda/mps/cpu) and machine issues."""
    if refresh:
        _cached_system_info.cache_clear()
    info = _cached_system_info()
    return {
        "status": "degraded" if preflight.has_errors(info["issues"]) else "ok",
        "app_version": APP_VERSION,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        **info,
    }


@app.get("/errors")
def error_codes() -> list[dict[str, Any]]:
    """The error code registry (useful for debugging translations)."""
    return [
        {"code": e.code, "severity": e.severity.value, "params": list(e.params)}
        for e in errors.REGISTRY.values()
    ]


if sys.platform == "win32":
    # Make Windows consoles print UTF-8 logs (Vietnamese file names).
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
