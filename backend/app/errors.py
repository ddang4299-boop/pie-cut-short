"""Error code registry.

The backend never sends user-facing text. It sends a code plus params, e.g.
    {"code": "DISK_SPACE_LOW", "severity": "error", "params": {"needed_gb": 12, "free_gb": 3.5}}
and the frontend translates `errors.<CODE>` from its locale files (en.json / vi.json).
A test checks that every code registered here has a translation in both locales.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Severity(str, Enum):
    ERROR = "error"  # blocks the pipeline
    WARNING = "warning"  # works, but degraded (e.g. CPU fallback)
    INFO = "info"  # handled automatically, shown for transparency


@dataclass(frozen=True)
class ErrorCode:
    code: str
    severity: Severity
    http_status: int = 400
    params: tuple[str, ...] = ()  # names of the params the translation expects


REGISTRY: dict[str, ErrorCode] = {}


def _register(
    code: str, severity: Severity, http_status: int = 400, params: tuple[str, ...] = ()
) -> ErrorCode:
    if code in REGISTRY:
        raise ValueError(f"duplicate error code {code}")
    entry = ErrorCode(code, severity, http_status, params)
    REGISTRY[code] = entry
    return entry


E, W, I = Severity.ERROR, Severity.WARNING, Severity.INFO

# --- Generic API errors ---------------------------------------------------
INTERNAL_ERROR = _register("INTERNAL_ERROR", E, 500)
NOT_FOUND = _register("NOT_FOUND", E, 404)
VALIDATION_ERROR = _register("VALIDATION_ERROR", E, 422, ("fields",))

# --- Tools / machine ------------------------------------------------------
FFMPEG_NOT_FOUND = _register("FFMPEG_NOT_FOUND", E, 500)
FFPROBE_NOT_FOUND = _register("FFPROBE_NOT_FOUND", E, 500)
GPU_NOT_AVAILABLE = _register("GPU_NOT_AVAILABLE", W)
DISK_SPACE_LOW = _register("DISK_SPACE_LOW", E, 507, ("needed_gb", "free_gb"))

# --- Source file ----------------------------------------------------------
FILE_NOT_FOUND = _register("FILE_NOT_FOUND", E, 404, ("name",))
FILE_EMPTY = _register("FILE_EMPTY", E, 400, ("name",))
PROBE_FAILED = _register("PROBE_FAILED", E, 400, ("name",))
INVALID_DURATION = _register("INVALID_DURATION", E)
NO_VIDEO_STREAM = _register("NO_VIDEO_STREAM", E)
NO_AUDIO_STREAM = _register("NO_AUDIO_STREAM", W)
VIDEO_DECODE_FAILED = _register("VIDEO_DECODE_FAILED", E, 400, ("codec",))
AUDIO_DECODE_FAILED = _register("AUDIO_DECODE_FAILED", E, 400, ("codec",))
LOW_RESOLUTION = _register("LOW_RESOLUTION", W, 400, ("width", "height"))
VARIABLE_FRAME_RATE = _register("VARIABLE_FRAME_RATE", I)
SOURCE_ALREADY_VERTICAL = _register("SOURCE_ALREADY_VERTICAL", I)
VERY_LONG_VIDEO = _register("VERY_LONG_VIDEO", I, 400, ("hours",))

del E, W, I


def issue(code: ErrorCode, **params: Any) -> dict[str, Any]:
    """Build a serializable issue dict, checking that params match the registry."""
    expected = set(code.params)
    if set(params) != expected:
        raise ValueError(f"{code.code} expects params {sorted(expected)}, got {sorted(params)}")
    return {"code": code.code, "severity": code.severity.value, "params": params}


class AppError(Exception):
    """Raise from routes/pipeline; FastAPI turns it into {"code", "severity", "params"}."""

    def __init__(self, code: ErrorCode, http_status: int | None = None, **params: Any):
        self.payload = issue(code, **params)
        self.http_status = http_status or code.http_status
        super().__init__(code.code)

    def to_dict(self) -> dict[str, Any]:
        return self.payload
